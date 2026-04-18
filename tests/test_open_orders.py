"""
tests/test_open_orders.py — unit tests for the open_orders feature.

Tests are grouped into three classes:

  TestOpenOrderService
    - Successful API response → correct dataclass mapping
    - Decimal precision for price and qty (never float)
    - Empty-string / missing price handled as Decimal("0")
    - No orders → empty list, not an error
    - Symbol is upper-cased before the API call
    - API errors propagate unchanged

  TestOpenOrdersExporter
    - File created at the given path
    - Parent directory created automatically
    - Headers always written (even with zero orders)
    - Correct column count and header names
    - Decimal values written as plain strings (no float noise)
    - Overwrites existing file, never appends

  TestOpenOrdersIntegration
    - End-to-end: service → exporter → CSV, verify round-trip values

No real network calls are made.  The Bybit client is replaced by a
plain stub (no unittest.mock needed for the happy-path tests; mock.patch
is used where we want to assert call arguments).
"""

from __future__ import annotations

import csv
import pathlib
from decimal import Decimal
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from exporters.open_orders_exporter import HEADERS, OpenOrdersExporter
from services.open_orders import OpenOrder, OpenOrderService, OpenOrderSnapshot


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _raw_order(
    order_id: str = "ord-001",
    symbol: str = "ICPUSDT",
    side: str = "Buy",
    order_type: str = "Limit",
    price: str = "2.187",
    qty: str = "25",
    order_status: str = "New",
    created_time: str = "1772181575546",
) -> dict[str, Any]:
    """Return a minimal raw order dict that mirrors the Bybit API shape."""
    return {
        "orderId":     order_id,
        "symbol":      symbol,
        "side":        side,
        "orderType":   order_type,
        "price":       price,
        "qty":         qty,
        "orderStatus": order_status,
        "createdTime": created_time,
    }


class _StubClient:
    """Returns a fixed list of raw order dicts; records the call arguments."""

    def __init__(self, orders: list[dict[str, Any]] | None = None) -> None:
        self._orders = orders or []
        self.last_call: dict[str, Any] = {}

    def get_open_orders(
        self,
        symbol: str,
        category: str = "linear",
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        self.last_call = {"symbol": symbol, "category": category, "limit": limit}
        return self._orders


class _ErrorClient:
    """Always raises BybitAPIError."""

    def get_open_orders(self, **_: Any) -> list[dict[str, Any]]:
        from api.bybit_client import BybitAPIError
        raise BybitAPIError("Bybit API error [10003]: Invalid api_key")


def _read_csv(path: pathlib.Path) -> list[list[str]]:
    return list(csv.reader(path.read_text(encoding="utf-8").splitlines()))


# ---------------------------------------------------------------------------
# OpenOrderService tests
# ---------------------------------------------------------------------------

class TestOpenOrderService:

    # --- return types -------------------------------------------------------

    def test_returns_snapshot_dataclass(self) -> None:
        svc = OpenOrderService(_StubClient([_raw_order()]))
        result = svc.get_open_orders("ICPUSDT")
        assert isinstance(result, OpenOrderSnapshot)

    def test_orders_are_open_order_instances(self) -> None:
        svc = OpenOrderService(_StubClient([_raw_order()]))
        result = svc.get_open_orders("ICPUSDT")
        assert all(isinstance(o, OpenOrder) for o in result.orders)

    def test_correct_order_count(self) -> None:
        svc = OpenOrderService(_StubClient([_raw_order("a"), _raw_order("b")]))
        assert len(svc.get_open_orders("ICPUSDT").orders) == 2

    # --- field mapping ------------------------------------------------------

    def test_order_id_mapped(self) -> None:
        svc = OpenOrderService(_StubClient([_raw_order(order_id="ord-xyz")]))
        assert svc.get_open_orders("ICPUSDT").orders[0].order_id == "ord-xyz"

    def test_symbol_mapped(self) -> None:
        svc = OpenOrderService(_StubClient([_raw_order(symbol="BTCUSDT")]))
        assert svc.get_open_orders("BTCUSDT").orders[0].symbol == "BTCUSDT"

    def test_side_mapped(self) -> None:
        svc = OpenOrderService(_StubClient([_raw_order(side="Sell")]))
        assert svc.get_open_orders("ICPUSDT").orders[0].side == "Sell"

    def test_order_type_mapped(self) -> None:
        svc = OpenOrderService(_StubClient([_raw_order(order_type="Market")]))
        assert svc.get_open_orders("ICPUSDT").orders[0].order_type == "Market"

    def test_order_status_mapped(self) -> None:
        svc = OpenOrderService(_StubClient([_raw_order(order_status="PartiallyFilled")]))
        assert svc.get_open_orders("ICPUSDT").orders[0].order_status == "PartiallyFilled"

    def test_snapshot_symbol_is_set(self) -> None:
        svc = OpenOrderService(_StubClient([_raw_order()]))
        assert svc.get_open_orders("ICPUSDT").symbol == "ICPUSDT"

    def test_snapshot_category_is_set(self) -> None:
        svc = OpenOrderService(_StubClient([_raw_order()]), category="inverse")
        assert svc.get_open_orders("ICPUSDT").category == "inverse"

    # --- Decimal precision --------------------------------------------------

    def test_price_is_decimal(self) -> None:
        svc = OpenOrderService(_StubClient([_raw_order(price="2.187")]))
        price = svc.get_open_orders("ICPUSDT").orders[0].price
        assert isinstance(price, Decimal)

    def test_qty_is_decimal(self) -> None:
        svc = OpenOrderService(_StubClient([_raw_order(qty="25")]))
        qty = svc.get_open_orders("ICPUSDT").orders[0].qty
        assert isinstance(qty, Decimal)

    def test_price_decimal_value_exact(self) -> None:
        svc = OpenOrderService(_StubClient([_raw_order(price="2.187")]))
        assert svc.get_open_orders("ICPUSDT").orders[0].price == Decimal("2.187")

    def test_qty_decimal_value_exact(self) -> None:
        svc = OpenOrderService(_StubClient([_raw_order(qty="25.5")]))
        assert svc.get_open_orders("ICPUSDT").orders[0].qty == Decimal("25.5")

    def test_price_never_a_float(self) -> None:
        svc = OpenOrderService(_StubClient([_raw_order(price="1.1")]))
        price = svc.get_open_orders("ICPUSDT").orders[0].price
        # float("1.1") != Decimal("1.1") due to binary representation
        assert price != float("1.1") or True   # always passes — just confirms type
        assert type(price) is Decimal

    def test_empty_price_becomes_decimal_zero(self) -> None:
        raw = _raw_order()
        raw["price"] = ""
        svc = OpenOrderService(_StubClient([raw]))
        assert svc.get_open_orders("ICPUSDT").orders[0].price == Decimal("0")

    def test_missing_price_key_becomes_decimal_zero(self) -> None:
        raw = _raw_order()
        del raw["price"]
        svc = OpenOrderService(_StubClient([raw]))
        assert svc.get_open_orders("ICPUSDT").orders[0].price == Decimal("0")

    def test_high_precision_price_preserved(self) -> None:
        svc = OpenOrderService(_StubClient([_raw_order(price="3.141592653589793")]))
        assert svc.get_open_orders("ICPUSDT").orders[0].price == Decimal("3.141592653589793")

    # --- timestamp parsing --------------------------------------------------

    def test_created_ts_is_raw_string(self) -> None:
        svc = OpenOrderService(_StubClient([_raw_order(created_time="1772181575546")]))
        assert svc.get_open_orders("ICPUSDT").orders[0].created_ts == "1772181575546"

    def test_created_date_derived_from_ts(self) -> None:
        # 1772181575546 ms → 2026-02-27 UTC
        svc = OpenOrderService(_StubClient([_raw_order(created_time="1772181575546")]))
        assert svc.get_open_orders("ICPUSDT").orders[0].created_date == "2026-02-27"

    def test_created_time_is_hh_mm_ss(self) -> None:
        svc = OpenOrderService(_StubClient([_raw_order(created_time="1772181575546")]))
        t = svc.get_open_orders("ICPUSDT").orders[0].created_time
        assert len(t.split(":")) == 3

    def test_empty_created_time_gives_empty_strings(self) -> None:
        raw = _raw_order()
        raw["createdTime"] = ""
        svc = OpenOrderService(_StubClient([raw]))
        o = svc.get_open_orders("ICPUSDT").orders[0]
        assert o.created_date == ""
        assert o.created_time == ""

    # --- symbol casing ------------------------------------------------------

    def test_symbol_upper_cased_in_api_call(self) -> None:
        client = _StubClient([_raw_order()])
        OpenOrderService(client).get_open_orders("icpusdt")
        assert client.last_call["symbol"] == "ICPUSDT"

    def test_snapshot_symbol_is_upper_cased(self) -> None:
        svc = OpenOrderService(_StubClient([]))
        snapshot = svc.get_open_orders("icpusdt")
        assert snapshot.symbol == "ICPUSDT"

    # --- empty response -----------------------------------------------------

    def test_no_orders_returns_empty_list(self) -> None:
        svc = OpenOrderService(_StubClient([]))
        assert svc.get_open_orders("ICPUSDT").orders == []

    def test_no_orders_does_not_raise(self) -> None:
        svc = OpenOrderService(_StubClient([]))
        snapshot = svc.get_open_orders("ICPUSDT")
        assert isinstance(snapshot, OpenOrderSnapshot)

    # --- error propagation --------------------------------------------------

    def test_api_error_propagates(self) -> None:
        from api.bybit_client import BybitAPIError
        with pytest.raises(BybitAPIError):
            OpenOrderService(_ErrorClient()).get_open_orders("ICPUSDT")

    def test_api_error_message_preserved(self) -> None:
        from api.bybit_client import BybitAPIError
        with pytest.raises(BybitAPIError, match="10003"):
            OpenOrderService(_ErrorClient()).get_open_orders("ICPUSDT")

    # --- category forwarded -------------------------------------------------

    def test_category_forwarded_to_client(self) -> None:
        client = _StubClient([])
        OpenOrderService(client, category="inverse").get_open_orders("BTCUSD")
        assert client.last_call["category"] == "inverse"


# ---------------------------------------------------------------------------
# OpenOrdersExporter tests
# ---------------------------------------------------------------------------

class TestOpenOrdersExporter:

    def _snapshot(self, orders: list[dict[str, Any]] | None = None) -> OpenOrderSnapshot:
        raw_list = orders if orders is not None else [_raw_order()]
        svc = OpenOrderService(_StubClient(raw_list))
        return svc.get_open_orders("ICPUSDT")

    def _empty_snapshot(self) -> OpenOrderSnapshot:
        return self._snapshot([])

    # --- file creation ------------------------------------------------------

    def test_file_is_created(self, tmp_path: pathlib.Path) -> None:
        out = tmp_path / "ICPUSDT_openOrders.csv"
        OpenOrdersExporter(out).export(self._snapshot())
        assert out.exists()

    def test_parent_directory_created_automatically(self, tmp_path: pathlib.Path) -> None:
        out = tmp_path / "nested" / "dir" / "out.csv"
        OpenOrdersExporter(out).export(self._snapshot())
        assert out.parent.is_dir()

    def test_returns_output_path(self, tmp_path: pathlib.Path) -> None:
        out = tmp_path / "out.csv"
        result = OpenOrdersExporter(out).export(self._snapshot())
        assert result == out

    # --- headers ------------------------------------------------------------

    def test_correct_headers_written(self, tmp_path: pathlib.Path) -> None:
        out = tmp_path / "out.csv"
        OpenOrdersExporter(out).export(self._empty_snapshot())
        assert _read_csv(out)[0] == HEADERS

    def test_column_count_matches_headers(self, tmp_path: pathlib.Path) -> None:
        out = tmp_path / "out.csv"
        OpenOrdersExporter(out).export(self._snapshot())
        rows = _read_csv(out)
        assert len(rows[1]) == len(HEADERS)

    def test_headers_include_required_columns(self, tmp_path: pathlib.Path) -> None:
        out = tmp_path / "out.csv"
        OpenOrdersExporter(out).export(self._empty_snapshot())
        headers = _read_csv(out)[0]
        for col in ("order_id", "symbol", "side", "order_type",
                    "price", "qty", "order_status", "created_date"):
            assert col in headers

    # --- empty snapshot writes headers only ---------------------------------

    def test_empty_snapshot_writes_header_only(self, tmp_path: pathlib.Path) -> None:
        out = tmp_path / "out.csv"
        OpenOrdersExporter(out).export(self._empty_snapshot())
        rows = _read_csv(out)
        assert rows == [HEADERS]

    def test_empty_snapshot_file_exists(self, tmp_path: pathlib.Path) -> None:
        out = tmp_path / "out.csv"
        OpenOrdersExporter(out).export(self._empty_snapshot())
        assert out.exists()

    # --- row count ----------------------------------------------------------

    def test_two_orders_produce_three_rows(self, tmp_path: pathlib.Path) -> None:
        out = tmp_path / "out.csv"
        snap = self._snapshot([_raw_order("a"), _raw_order("b")])
        OpenOrdersExporter(out).export(snap)
        assert len(_read_csv(out)) == 3  # header + 2

    # --- Decimal written as clean string ------------------------------------

    def test_price_written_as_string_not_float(self, tmp_path: pathlib.Path) -> None:
        out = tmp_path / "out.csv"
        OpenOrdersExporter(out).export(self._snapshot([_raw_order(price="2.187")]))
        row = _read_csv(out)[1]
        assert row[HEADERS.index("price")] == "2.187"

    def test_qty_written_as_string(self, tmp_path: pathlib.Path) -> None:
        out = tmp_path / "out.csv"
        OpenOrdersExporter(out).export(self._snapshot([_raw_order(qty="25")]))
        row = _read_csv(out)[1]
        assert row[HEADERS.index("qty")] == "25"

    def test_high_precision_price_round_trips(self, tmp_path: pathlib.Path) -> None:
        out = tmp_path / "out.csv"
        price_str = "3.141592653589793"
        OpenOrdersExporter(out).export(
            self._snapshot([_raw_order(price=price_str)])
        )
        row = _read_csv(out)[1]
        assert row[HEADERS.index("price")] == price_str

    # --- field values -------------------------------------------------------

    def test_correct_values_first_row(self, tmp_path: pathlib.Path) -> None:
        out = tmp_path / "out.csv"
        OpenOrdersExporter(out).export(self._snapshot([
            _raw_order(
                order_id="ord-001",
                symbol="ICPUSDT",
                side="Buy",
                order_type="Limit",
                price="2.187",
                qty="25",
                order_status="New",
                created_time="1772181575546",
            )
        ]))
        rows = _read_csv(out)
        assert rows[1][HEADERS.index("order_id")]     == "ord-001"
        assert rows[1][HEADERS.index("symbol")]       == "ICPUSDT"
        assert rows[1][HEADERS.index("side")]         == "Buy"
        assert rows[1][HEADERS.index("order_type")]   == "Limit"
        assert rows[1][HEADERS.index("price")]        == "2.187"
        assert rows[1][HEADERS.index("qty")]          == "25"
        assert rows[1][HEADERS.index("order_status")] == "New"
        assert rows[1][HEADERS.index("created_date")] == "2026-02-27"

    # --- overwrite, not append ----------------------------------------------

    def test_overwrites_existing_file(self, tmp_path: pathlib.Path) -> None:
        out = tmp_path / "out.csv"
        exporter = OpenOrdersExporter(out)
        exporter.export(self._snapshot([_raw_order("a"), _raw_order("b")]))
        exporter.export(self._snapshot([_raw_order("c")]))   # one order only
        rows = _read_csv(out)
        assert len(rows) == 2   # header + 1

    def test_stale_data_not_appended(self, tmp_path: pathlib.Path) -> None:
        out = tmp_path / "out.csv"
        exporter = OpenOrdersExporter(out)
        exporter.export(self._snapshot([_raw_order("stale")]))
        exporter.export(self._empty_snapshot())
        rows = _read_csv(out)
        assert rows == [HEADERS]


# ---------------------------------------------------------------------------
# Integration: service → exporter → CSV round-trip
# ---------------------------------------------------------------------------

class TestOpenOrdersIntegration:

    def test_full_pipeline_produces_correct_csv(self, tmp_path: pathlib.Path) -> None:
        raw = _raw_order(
            order_id="int-001",
            symbol="ICPUSDT",
            side="Sell",
            order_type="Limit",
            price="4.567",
            qty="10",
            order_status="New",
            created_time="1772181575546",
        )
        client   = _StubClient([raw])
        service  = OpenOrderService(client)
        snapshot = service.get_open_orders("ICPUSDT")

        out      = tmp_path / "ICPUSDT_openOrders.csv"
        exporter = OpenOrdersExporter(out)
        exporter.export(snapshot)

        rows = list(csv.reader(out.read_text(encoding="utf-8").splitlines()))
        assert rows[0] == HEADERS
        assert rows[1][HEADERS.index("order_id")]  == "int-001"
        assert rows[1][HEADERS.index("price")]     == "4.567"
        assert rows[1][HEADERS.index("qty")]       == "10"
        assert rows[1][HEADERS.index("side")]      == "Sell"

    def test_decimal_price_not_corrupted_through_pipeline(
        self, tmp_path: pathlib.Path
    ) -> None:
        """A price that floats would mangle (e.g. 0.1 + 0.2 != 0.3)."""
        raw     = _raw_order(price="0.1")
        client  = _StubClient([raw])
        snap    = OpenOrderService(client).get_open_orders("ICPUSDT")
        out     = tmp_path / "out.csv"
        OpenOrdersExporter(out).export(snap)

        rows = _read_csv(out)
        # Float would give "0.1" but also risk "0.10000000000000001" — Decimal never does
        assert rows[1][HEADERS.index("price")] == "0.1"

    def test_multiple_orders_all_written(self, tmp_path: pathlib.Path) -> None:
        raws = [_raw_order(f"ord-{i}", price=str(i)) for i in range(1, 6)]
        snap = OpenOrderService(_StubClient(raws)).get_open_orders("ICPUSDT")
        out  = tmp_path / "out.csv"
        OpenOrdersExporter(out).export(snap)
        rows = _read_csv(out)
        assert len(rows) == 6  # header + 5
