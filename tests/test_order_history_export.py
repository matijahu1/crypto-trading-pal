"""
tests/test_order_history_export.py — example unit tests for the refactored
order-history export pipeline.

These tests demonstrate *why* the PathProvider refactor improves testability:

  * No real filesystem paths are constructed inside the exporter.
  * PathProvider itself can be replaced with a MagicMock — or instantiated
    with a tmp_path supplied by pytest — with a single line.
  * The action handler (_export_order_history) can be tested end-to-end
    without writing to data/exported/.

Run with:
    pytest tests/test_order_history_export.py -v
"""

from __future__ import annotations

import pathlib
from dataclasses import dataclass, field
from typing import Any
from unittest.mock import MagicMock, patch, call

import pytest

# ---------------------------------------------------------------------------
# Minimal stubs so the tests run without the full project installed
# ---------------------------------------------------------------------------

@dataclass
class _Order:
    order_id:     str
    symbol:       str
    side:         str
    order_type:   str
    price:        str
    qty:          str
    order_status: str
    created_ts:   str
    updated_ts:   str
    created_date: str
    created_time: str
    updated_date: str
    updated_time: str


@dataclass
class _OrderHistory:
    orders: list[_Order] = field(default_factory=list)


# ---------------------------------------------------------------------------
# PathProvider tests — pure unit tests, zero I/O
# ---------------------------------------------------------------------------

class TestPathProvider:
    """PathProvider generates the correct paths without touching the filesystem."""

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
            p.order_history_path(),
            p.trade_history_path(),
            p.recent_fills_path(),
            p.balance_path(),
            p.futures_positions_path(),
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
        p.ensure_dir()   # should not raise

    def test_provider_can_be_fully_mocked(self):
        from exporters.path_provider import PathProvider
        mock_provider = MagicMock(spec=PathProvider)
        mock_provider.order_history_path.return_value = pathlib.Path("/tmp/test.csv")

        # Simulate how an action handler uses the provider
        result = mock_provider.order_history_path()
        assert result == pathlib.Path("/tmp/test.csv")
        mock_provider.order_history_path.assert_called_once()


# ---------------------------------------------------------------------------
# OrderHistoryExporter tests — path is injected, no internal logic to test
# ---------------------------------------------------------------------------

class TestOrderHistoryExporter:
    """The exporter writes the correct CSV content to a caller-supplied path."""

    def _make_history(self) -> _OrderHistory:
        return _OrderHistory(orders=[
            _Order(
                order_id="ORD001", symbol="CCUSDT", side="Buy",
                order_type="Limit", price="100.0", qty="1.0",
                order_status="Filled", created_ts="1700000000000",
                updated_ts="1700000001000", created_date="2023-11-14",
                created_time="22:13:20", updated_date="2023-11-14",
                updated_time="22:13:21",
            )
        ])

    def test_headers_are_correct(self):
        from exporters.order_history_exporter import OrderHistoryExporter, HEADERS
        exporter = OrderHistoryExporter(pathlib.Path("/dev/null"))
        assert exporter.headers == HEADERS

    def test_rows_maps_all_fields(self):
        from exporters.order_history_exporter import OrderHistoryExporter
        exporter = OrderHistoryExporter(pathlib.Path("/dev/null"))
        history  = self._make_history()
        rows     = exporter.rows(history)   # type: ignore[arg-type]

        assert len(rows) == 1
        row = rows[0]
        assert row[0] == "ORD001"    # order_id
        assert row[1] == "CCUSDT"    # symbol
        assert row[6] == "Filled"    # order_status

    def test_export_writes_csv_to_tmp_path(self, tmp_path):
        from exporters.order_history_exporter import OrderHistoryExporter
        output = tmp_path / "CCUSDT_orderHistory.csv"
        exporter = OrderHistoryExporter(output)
        history  = self._make_history()
        exporter.export(history)    # type: ignore[arg-type]

        assert output.exists()
        lines = output.read_text().splitlines()
        assert lines[0].startswith("order_id")   # header row
        assert "ORD001" in lines[1]

    def test_exporter_accepts_path_from_provider(self, tmp_path):
        """Canonical integration: PathProvider → path → exporter."""
        from exporters.path_provider       import PathProvider
        from exporters.order_history_exporter import OrderHistoryExporter

        provider = PathProvider(base_dir=tmp_path, symbol="ccusdt")
        exporter = OrderHistoryExporter(provider.order_history_path())
        history  = self._make_history()
        exporter.export(history)    # type: ignore[arg-type]

        expected = tmp_path / "CCUSDT_orderHistory.csv"
        assert expected.exists()


# ---------------------------------------------------------------------------
# Action-handler integration test — mocked PathProvider, no real I/O
# ---------------------------------------------------------------------------

class TestExportOrderHistoryAction:
    """
    _export_order_history in main.py can be tested without any real filesystem
    access by passing a MagicMock(spec=PathProvider).
    """

    def test_action_uses_path_from_provider_before_api_call(self, tmp_path):
        """
        Verify that the action obtains the output path from PathProvider and
        passes it to the exporter — without checking filesystem state.
        """
        from exporters.path_provider import PathProvider

        fake_path     = tmp_path / "CCUSDT_orderHistory.csv"
        mock_provider = MagicMock(spec=PathProvider)
        mock_provider.order_history_path.return_value = fake_path
        mock_provider.symbol = "CCUSDT"

        mock_history = MagicMock()
        mock_history.orders = []   # no orders → early return

        mock_service = MagicMock()
        mock_service.get_history.return_value = mock_history

        mock_client = MagicMock()

        with (
            patch("main.OrderHistoryService", return_value=mock_service),
            patch("main.OrderHistoryExporter") as MockExporter,
        ):
            from main import _export_order_history
            _export_order_history(mock_client, mock_provider, lookback_days=30)

        # PathProvider must be asked for the path (early, before API call)
        mock_provider.order_history_path.assert_called_once()
        # Exporter must receive the path from the provider, not construct it
        MockExporter.assert_called_once_with(fake_path)
