"""
tests/test_order_history_export.py — unit tests for the order-history pipeline.

Coverage areas
--------------
TestPathProvider          — path generation, ensure_dir, mockability
TestOrderHistoryExporter  — headers, row mapping, CSV write, path injection
TestOrderHistoryMerger    — the new delta / Load-Merge-Sort-Overwrite logic
TestOrderHistoryService   — order_status filter forwarded to client
TestExportOrderHistoryAction — end-to-end action handler (mocked I/O & API)

Run with:
    pytest tests/test_order_history_export.py -v
"""

from __future__ import annotations

import csv
import pathlib
from dataclasses import dataclass, field
from typing import Any
from unittest.mock import MagicMock, call, patch

import pytest


# ---------------------------------------------------------------------------
# Shared test fixtures / helpers
# ---------------------------------------------------------------------------

def _make_order(
    order_id:     str   = "ORD001",
    symbol:       str   = "CCUSDT",
    side:         str   = "Buy",
    order_type:   str   = "Limit",
    price:        float = 100.0,
    qty:          float = 1.0,
    order_status: str   = "Filled",
    created_ts:   str   = "1700000000000",
    updated_ts:   str   = "1700000001000",
    created_date: str   = "2023-11-14",
    created_time: str   = "22:13:20",
    updated_date: str   = "2023-11-14",
    updated_time: str   = "22:13:21",
):
    """Return an Order dataclass.  Import is deferred so stubs work too."""
    from services.order_history import Order
    return Order(
        order_id=order_id,
        symbol=symbol,
        side=side,
        order_type=order_type,
        price=price,
        qty=qty,
        order_status=order_status,
        created_ts=created_ts,
        updated_ts=updated_ts,
        created_date=created_date,
        created_time=created_time,
        updated_date=updated_date,
        updated_time=updated_time,
    )


def _write_csv(path: pathlib.Path, orders) -> None:
    """Write a list of Order objects to a CSV (mirrors OrderHistoryExporter)."""
    from exporters.order_history_exporter import HEADERS
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(HEADERS)
        for o in orders:
            writer.writerow([
                o.order_id, o.symbol, o.side, o.order_type,
                o.price, o.qty, o.order_status,
                o.created_ts, o.updated_ts,
                o.created_date, o.created_time,
                o.updated_date, o.updated_time,
            ])


# ===========================================================================
# PathProvider
# ===========================================================================

class TestPathProvider:
    """PathProvider generates correct paths without touching the filesystem."""

    def test_order_history_path_uses_upper_symbol(self):
        from exporters.path_provider import PathProvider
        p = PathProvider(base_dir="data/exported", symbol="ccusdt")
        assert p.order_history_path() == pathlib.Path("data/exported/CCUSDT_orderHistory.csv")

    def test_balance_path_is_symbol_independent(self):
        from exporters.path_provider import PathProvider
        p = PathProvider(base_dir="data/exported", symbol="BTCUSDT")
        assert p.balance_path() == pathlib.Path("data/exported/balance.csv")

    def test_all_paths_share_base_dir(self, tmp_path):
        from exporters.path_provider import PathProvider
        p = PathProvider(base_dir=tmp_path, symbol="ZECUSDT")
        for path in [
            p.order_history_path(), p.trade_history_path(),
            p.recent_fills_path(), p.balance_path(), p.futures_positions_path(),
        ]:
            assert path.parent == tmp_path

    def test_ensure_dir_creates_directory(self, tmp_path):
        from exporters.path_provider import PathProvider
        target = tmp_path / "sub" / "exported"
        p = PathProvider(base_dir=target, symbol="BTCUSDT")
        assert not target.exists()
        p.ensure_dir()
        assert target.is_dir()

    def test_ensure_dir_is_idempotent(self, tmp_path):
        from exporters.path_provider import PathProvider
        p = PathProvider(base_dir=tmp_path, symbol="BTCUSDT")
        p.ensure_dir()
        p.ensure_dir()   # must not raise

    def test_provider_can_be_fully_mocked(self):
        from exporters.path_provider import PathProvider
        mock_provider = MagicMock(spec=PathProvider)
        mock_provider.order_history_path.return_value = pathlib.Path("/tmp/test.csv")
        result = mock_provider.order_history_path()
        assert result == pathlib.Path("/tmp/test.csv")
        mock_provider.order_history_path.assert_called_once()


# ===========================================================================
# OrderHistoryExporter
# ===========================================================================

class TestOrderHistoryExporter:
    """The exporter writes correct CSV content to a caller-supplied path."""

    def test_headers_are_correct(self):
        from exporters.order_history_exporter import OrderHistoryExporter, HEADERS
        exporter = OrderHistoryExporter(pathlib.Path("/dev/null"))
        assert exporter.headers == HEADERS

    def test_rows_maps_all_fields(self):
        from exporters.order_history_exporter import OrderHistoryExporter
        from services.order_history import OrderHistory
        exporter = OrderHistoryExporter(pathlib.Path("/dev/null"))
        history  = OrderHistory(symbol="CCUSDT", category="linear", orders=[_make_order()])
        rows     = exporter.rows(history)

        assert len(rows) == 1
        row = rows[0]
        assert row[0] == "ORD001"   # order_id
        assert row[1] == "CCUSDT"   # symbol
        assert row[6] == "Filled"   # order_status

    def test_export_writes_csv_to_tmp_path(self, tmp_path):
        from exporters.order_history_exporter import OrderHistoryExporter
        from services.order_history import OrderHistory
        output   = tmp_path / "CCUSDT_orderHistory.csv"
        exporter = OrderHistoryExporter(output)
        exporter.export(OrderHistory(symbol="CCUSDT", category="linear", orders=[_make_order()]))

        assert output.exists()
        lines = output.read_text().splitlines()
        assert lines[0].startswith("order_id")
        assert "ORD001" in lines[1]

    def test_exporter_accepts_path_from_provider(self, tmp_path):
        from exporters.path_provider import PathProvider
        from exporters.order_history_exporter import OrderHistoryExporter
        from services.order_history import OrderHistory

        provider = PathProvider(base_dir=tmp_path, symbol="ccusdt")
        exporter = OrderHistoryExporter(provider.order_history_path())
        exporter.export(OrderHistory(symbol="CCUSDT", category="linear", orders=[_make_order()]))

        assert (tmp_path / "CCUSDT_orderHistory.csv").exists()


# ===========================================================================
# OrderHistoryMerger  ← new tests covering the delta / merge logic
# ===========================================================================

class TestOrderHistoryMerger:
    """
    Unit tests for the Load-Merge-Sort-Overwrite logic in OrderHistoryMerger.

    All tests use tmp_path or no real file at all — zero production I/O.
    """

    # ------------------------------------------------------------------
    # _load_existing
    # ------------------------------------------------------------------

    def test_load_returns_empty_when_file_missing(self, tmp_path):
        from exporters.order_history_merger import OrderHistoryMerger
        merger = OrderHistoryMerger(tmp_path / "missing.csv")
        # Access via merge([]) — an empty merge on a missing file yields []
        result = merger.merge([])
        assert result == []

    def test_load_returns_empty_for_header_only_csv(self, tmp_path):
        from exporters.order_history_merger import OrderHistoryMerger
        from exporters.order_history_exporter import HEADERS
        path = tmp_path / "empty.csv"
        with path.open("w", newline="", encoding="utf-8") as fh:
            csv.writer(fh).writerow(HEADERS)
        result = OrderHistoryMerger(path).merge([])
        assert result == []

    def test_load_reconstructs_order_fields_from_csv(self, tmp_path):
        """Fields read from CSV must round-trip to the same Order values."""
        from exporters.order_history_merger import OrderHistoryMerger
        path = tmp_path / "CCUSDT_orderHistory.csv"
        original = _make_order(order_id="ORD-LOAD", price=42.5, qty=3.0)
        _write_csv(path, [original])

        merger = OrderHistoryMerger(path)
        result = merger.merge([])   # no new orders → just load

        assert len(result) == 1
        loaded = result[0]
        assert loaded.order_id == "ORD-LOAD"
        assert loaded.price    == 42.5
        assert loaded.qty      == 3.0
        assert loaded.side     == "Buy"

    # ------------------------------------------------------------------
    # Deduplication
    # ------------------------------------------------------------------

    def test_merge_skips_duplicate_order_ids(self, tmp_path):
        """An order already on disk must not appear twice after merging."""
        from exporters.order_history_merger import OrderHistoryMerger
        path     = tmp_path / "CCUSDT_orderHistory.csv"
        existing = _make_order(order_id="DUP-001", updated_ts="1700000010000")
        _write_csv(path, [existing])

        duplicate = _make_order(order_id="DUP-001", updated_ts="1700000010000")
        result    = OrderHistoryMerger(path).merge([duplicate])

        ids = [o.order_id for o in result]
        assert ids.count("DUP-001") == 1

    def test_merge_adds_genuinely_new_orders(self, tmp_path):
        """New order IDs not present on disk must be added."""
        from exporters.order_history_merger import OrderHistoryMerger
        path = tmp_path / "CCUSDT_orderHistory.csv"
        _write_csv(path, [_make_order(order_id="OLD-001", updated_ts="1700000001000")])

        new_order = _make_order(order_id="NEW-002", updated_ts="1700000002000")
        result    = OrderHistoryMerger(path).merge([new_order])

        ids = {o.order_id for o in result}
        assert "OLD-001" in ids
        assert "NEW-002" in ids
        assert len(result) == 2

    def test_merge_with_no_new_orders_is_idempotent(self, tmp_path):
        """Calling merge([]) must return the existing records unchanged."""
        from exporters.order_history_merger import OrderHistoryMerger
        path     = tmp_path / "CCUSDT_orderHistory.csv"
        existing = [
            _make_order(order_id="A", updated_ts="1700000002000"),
            _make_order(order_id="B", updated_ts="1700000001000"),
        ]
        _write_csv(path, existing)

        result = OrderHistoryMerger(path).merge([])
        assert {o.order_id for o in result} == {"A", "B"}

    def test_merge_on_fresh_file_returns_only_new_orders(self, tmp_path):
        """When no CSV exists yet, the result is exactly the new orders."""
        from exporters.order_history_merger import OrderHistoryMerger
        path   = tmp_path / "fresh.csv"
        new    = [_make_order(order_id=f"N{i}", updated_ts=str(1700000000000 + i))
                  for i in range(3)]
        result = OrderHistoryMerger(path).merge(new)
        assert {o.order_id for o in result} == {"N0", "N1", "N2"}

    def test_merge_partial_overlap(self, tmp_path):
        """Only truly new orders are added; overlapping ones are skipped."""
        from exporters.order_history_merger import OrderHistoryMerger
        path     = tmp_path / "CCUSDT_orderHistory.csv"
        existing = [_make_order(order_id="OLD", updated_ts="1700000001000")]
        _write_csv(path, existing)

        incoming = [
            _make_order(order_id="OLD", updated_ts="1700000001000"),   # duplicate
            _make_order(order_id="NEW", updated_ts="1700000002000"),   # genuine
        ]
        result = OrderHistoryMerger(path).merge(incoming)
        assert len(result) == 2
        assert {o.order_id for o in result} == {"OLD", "NEW"}

    # ------------------------------------------------------------------
    # Sorting
    # ------------------------------------------------------------------

    def test_merge_result_is_sorted_desc_by_updated_ts(self, tmp_path):
        """Combined list must be sorted newest-first by updated_ts."""
        from exporters.order_history_merger import OrderHistoryMerger
        path = tmp_path / "CCUSDT_orderHistory.csv"
        _write_csv(path, [
            _make_order(order_id="OLD-1", updated_ts="1700000001000"),
        ])
        new_orders = [
            _make_order(order_id="NEW-3", updated_ts="1700000003000"),
            _make_order(order_id="NEW-2", updated_ts="1700000002000"),
        ]
        result = OrderHistoryMerger(path).merge(new_orders)

        timestamps = [int(o.updated_ts) for o in result]
        assert timestamps == sorted(timestamps, reverse=True), \
            f"Expected DESC order, got: {timestamps}"

    def test_merge_sorts_even_with_no_existing_file(self, tmp_path):
        """Sorting must work even on first run (no CSV on disk)."""
        from exporters.order_history_merger import OrderHistoryMerger
        path = tmp_path / "new.csv"
        orders = [
            _make_order(order_id="Z", updated_ts="1700000001000"),
            _make_order(order_id="A", updated_ts="1700000009000"),
            _make_order(order_id="M", updated_ts="1700000005000"),
        ]
        result = OrderHistoryMerger(path).merge(orders)
        ids    = [o.order_id for o in result]
        assert ids == ["A", "M", "Z"], f"Expected ['A','M','Z'], got {ids}"

    # ------------------------------------------------------------------
    # Round-trip: merger output can be re-read by a second merger instance
    # ------------------------------------------------------------------

    def test_round_trip_merge_write_reload(self, tmp_path):
        """
        Simulate two consecutive runs:
          Run 1 — write new orders via merger → exporter.
          Run 2 — merger loads that CSV, adds more new orders, deduplicates.
        """
        from exporters.order_history_merger import OrderHistoryMerger
        from exporters.order_history_exporter import OrderHistoryExporter
        from services.order_history import OrderHistory

        path = tmp_path / "CCUSDT_orderHistory.csv"

        # ── Run 1 ────────────────────────────────────────────────────────────
        run1_new = [
            _make_order(order_id="RUN1-A", updated_ts="1700000002000"),
            _make_order(order_id="RUN1-B", updated_ts="1700000001000"),
        ]
        combined1 = OrderHistoryMerger(path).merge(run1_new)
        OrderHistoryExporter(path).export(
            OrderHistory(symbol="CCUSDT", category="linear", orders=combined1)
        )
        assert len(combined1) == 2

        # ── Run 2 — one duplicate, one new ───────────────────────────────────
        run2_new = [
            _make_order(order_id="RUN1-A", updated_ts="1700000002000"),   # dup
            _make_order(order_id="RUN2-C", updated_ts="1700000003000"),   # new
        ]
        combined2 = OrderHistoryMerger(path).merge(run2_new)

        assert len(combined2) == 3, f"Expected 3, got {len(combined2)}"
        ids = {o.order_id for o in combined2}
        assert ids == {"RUN1-A", "RUN1-B", "RUN2-C"}

        # Verify sort order (newest first)
        timestamps = [int(o.updated_ts) for o in combined2]
        assert timestamps == sorted(timestamps, reverse=True)


# ===========================================================================
# OrderHistoryService — order_status filter
# ===========================================================================

class TestOrderHistoryServiceStatusFilter:
    """
    The service must forward order_status to the API client.
    Uses a mock client so no network call is made.
    """

    def _make_api_entry(self, order_id: str, ts: str = "1700000000000") -> dict:
        return {
            "orderId":      order_id,
            "symbol":       "CCUSDT",
            "side":         "Buy",
            "orderType":    "Limit",
            "price":        "100",
            "qty":          "1",
            "orderStatus":  "Filled",
            "createdTime":  ts,
            "updatedTime":  ts,
        }

    def test_get_history_forwards_filled_status_to_client(self):
        """get_history(order_status='Filled') must pass orderStatus to the client."""
        from services.order_history import OrderHistoryService

        mock_client = MagicMock()
        # Return one order on the first call, then empty to end paging
        mock_client.get_order_history.side_effect = [
            [self._make_api_entry("ORD-1", "1700000050000")],
            [],   # end inner loop
        ]

        service = OrderHistoryService(
            client=mock_client,
            category="linear",
            limit=50,
            lookback_days=1,
        )
        with patch("services.order_history._now_ms", return_value=1700000100000):
            result = service.get_history("CCUSDT", order_status="Filled")

        # Verify order_status="Filled" was passed in every call
        for call_args in mock_client.get_order_history.call_args_list:
            assert call_args.kwargs.get("order_status") == "Filled", \
                f"order_status missing or wrong: {call_args}"

        assert len(result.orders) == 1
        assert result.orders[0].order_id == "ORD-1"

    def test_get_history_with_none_status_omits_filter(self):
        """get_history(order_status=None) must NOT pass orderStatus to the client."""
        from services.order_history import OrderHistoryService

        mock_client = MagicMock()
        mock_client.get_order_history.side_effect = [
            [self._make_api_entry("ORD-2", "1700000050000")],
            [],
        ]

        service = OrderHistoryService(
            client=mock_client,
            category="linear",
            limit=50,
            lookback_days=1,
        )
        with patch("services.order_history._now_ms", return_value=1700000100000):
            service.get_history("CCUSDT", order_status=None)

        for call_args in mock_client.get_order_history.call_args_list:
            assert call_args.kwargs.get("order_status") is None, \
                f"Expected order_status=None, got: {call_args}"

    def test_get_history_default_status_is_filled(self):
        """Calling get_history() without order_status must default to 'Filled'."""
        from services.order_history import OrderHistoryService

        mock_client = MagicMock()
        mock_client.get_order_history.side_effect = [
            [self._make_api_entry("ORD-3", "1700000050000")],
            [],
        ]

        service = OrderHistoryService(
            client=mock_client,
            category="linear",
            limit=50,
            lookback_days=1,
        )
        with patch("services.order_history._now_ms", return_value=1700000100000):
            service.get_history("CCUSDT")   # no order_status kwarg

        first_call = mock_client.get_order_history.call_args_list[0]
        assert first_call.kwargs.get("order_status") == "Filled"


# ===========================================================================
# Action-handler integration — mocked PathProvider, API, and merger
# ===========================================================================

class TestExportOrderHistoryAction:
    """
    _run_order_history in main.py orchestrates Load-Merge-Sort-Overwrite.
    Verified end-to-end with mocks — zero real I/O or network.
    """

    def test_action_fetches_filled_status_only(self):
        """The action must call get_history with order_status='Filled'."""
        from exporters.path_provider import PathProvider
        import pathlib

        mock_provider = MagicMock(spec=PathProvider)
        mock_provider.order_history_path.return_value = pathlib.Path("/tmp/test.csv")
        mock_provider.symbol = "CCUSDT"

        mock_history = MagicMock()
        mock_history.orders = []

        mock_service = MagicMock()
        mock_service.get_history.return_value = mock_history

        mock_merger = MagicMock()
        mock_merger.merge.return_value = []   # nothing to write → early return

        mock_client = MagicMock()

        with (
            patch("main.OrderHistoryService", return_value=mock_service),
            patch("main.OrderHistoryMerger",  return_value=mock_merger),
            patch("main.OrderHistoryExporter"),
        ):
            from main import _run_order_history
            _run_order_history(mock_client, mock_provider, lookback_days=30)

        mock_service.get_history.assert_called_once_with("CCUSDT", order_status="Filled")

    def test_action_passes_output_path_to_merger_and_exporter(self, tmp_path):
        """Both OrderHistoryMerger and OrderHistoryExporter receive the path from PathProvider."""
        from exporters.path_provider import PathProvider

        fake_path     = tmp_path / "CCUSDT_orderHistory.csv"
        mock_provider = MagicMock(spec=PathProvider)
        mock_provider.order_history_path.return_value = fake_path
        mock_provider.symbol = "CCUSDT"

        mock_history = MagicMock()
        mock_history.orders = [_make_order()]

        mock_service = MagicMock()
        mock_service.get_history.return_value = mock_history

        mock_merger = MagicMock()
        mock_merger.merge.return_value = [_make_order()]

        mock_client = MagicMock()

        with (
            patch("main.OrderHistoryService", return_value=mock_service),
            patch("main.OrderHistoryMerger")  as MockMerger,
            patch("main.OrderHistoryExporter") as MockExporter,
        ):
            MockMerger.return_value  = mock_merger
            MockExporter.return_value = MagicMock()

            from main import _run_order_history
            _run_order_history(mock_client, mock_provider, lookback_days=30)

        MockMerger.assert_called_once_with(fake_path)
        MockExporter.assert_called_once_with(fake_path)

    def test_action_does_not_write_when_combined_is_empty(self, tmp_path):
        """If merger returns an empty list, the exporter must not be called."""
        from exporters.path_provider import PathProvider

        fake_path     = tmp_path / "CCUSDT_orderHistory.csv"
        mock_provider = MagicMock(spec=PathProvider)
        mock_provider.order_history_path.return_value = fake_path
        mock_provider.symbol = "CCUSDT"

        mock_history = MagicMock()
        mock_history.orders = []

        mock_service = MagicMock()
        mock_service.get_history.return_value = mock_history

        mock_merger = MagicMock()
        mock_merger.merge.return_value = []   # nothing after merge

        mock_client = MagicMock()

        with (
            patch("main.OrderHistoryService", return_value=mock_service),
            patch("main.OrderHistoryMerger",  return_value=mock_merger),
            patch("main.OrderHistoryExporter") as MockExporter,
        ):
            from main import _run_order_history
            _run_order_history(mock_client, mock_provider, lookback_days=30)

        MockExporter.assert_not_called()

    def test_action_path_obtained_before_api_call(self, tmp_path):
        """
        PathProvider.order_history_path() must be called before
        OrderHistoryService.get_history() — the key enabler for future
        'skip if up-to-date' logic.
        """
        from exporters.path_provider import PathProvider

        call_order: list[str] = []

        fake_path     = tmp_path / "CCUSDT_orderHistory.csv"
        mock_provider = MagicMock(spec=PathProvider)

        def _record_path():
            call_order.append("path_provider")
            return fake_path

        mock_provider.order_history_path.side_effect = _record_path
        mock_provider.symbol = "CCUSDT"

        mock_history = MagicMock()
        mock_history.orders = []

        mock_service = MagicMock()

        def _record_api(*args, **kwargs):
            call_order.append("api")
            return mock_history

        mock_service.get_history.side_effect = _record_api

        mock_merger = MagicMock()
        mock_merger.merge.return_value = []

        mock_client = MagicMock()

        with (
            patch("main.OrderHistoryService", return_value=mock_service),
            patch("main.OrderHistoryMerger",  return_value=mock_merger),
            patch("main.OrderHistoryExporter"),
        ):
            from main import _run_order_history
            _run_order_history(mock_client, mock_provider, lookback_days=30)

        assert call_order.index("path_provider") < call_order.index("api"), \
            f"path_provider must be called before api; order was {call_order}"
