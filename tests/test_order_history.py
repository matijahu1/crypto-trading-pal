"""
Unit tests for OrderHistoryService and OrderHistoryExporter.

The API client is replaced by a plain stub — no real HTTP calls,
no unittest.mock. Each test follows arrange → act → assert.
"""

import csv
import pathlib
from typing import Any, TypeAlias

import pytest

from services.order_history import (
    OrderHistoryService, OrderHistory, Order,
    LOOKBACK_DAYS, MAX_WINDOW_DAYS, MS_PER_DAY,
)
from exporters.order_history_exporter import (
    OrderHistoryExporter,    
    HEADERS,
)

from api.bybit_client import BybitAPIError


# ---------------------------------------------------------------------------
# Stub clients
# ---------------------------------------------------------------------------

class StubOrderClient:
    """
    Simple stub: returns the same list on the first call, [] on subsequent
    calls (so the inner page-loop terminates after one page).
    """

    def __init__(
        self,
        orders: list[dict[str, Any]] | None = None,
        raise_error: bool = False,
    ) -> None:
        self._orders     = orders or []
        self._raise_error = raise_error
        self.last_symbol: str | None = None
        self.last_category: str | None = None
        self.last_limit: int | None = None
        self.call_count: int = 0

    def get_order_history(
        self,
        symbol: str,
        category: str,
        limit: int,
        start_time: int | None = None,
        end_time: int | None = None,
        order_status: str | None = None,
    ) -> list[dict[str, Any]]:
        self.last_symbol   = symbol
        self.last_category = category
        self.last_limit    = limit
        self.call_count   += 1
        if self._raise_error:
            raise BybitAPIError("Bybit API error [10003]: Invalid api_key")
        return self._orders if self.call_count == 1 else []


class SequentialStubClient:
    """Returns pages in order; [] once exhausted. Records call timestamps."""

    def __init__(self, pages: list[list[dict[str, Any]]]) -> None:
        self._pages = list(pages)
        self._idx   = 0
        self.call_log: list[tuple[int | None, int | None]] = []

    def get_order_history(
        self,
        symbol: str,
        category: str,
        limit: int,
        start_time: int | None = None,
        end_time: int | None = None,
        order_status: str | None = None,
    ) -> list[dict[str, Any]]:
        self.call_log.append((start_time, end_time))
        if self._idx < len(self._pages):
            page = self._pages[self._idx]
            self._idx += 1
            return page
        return []


# ---------------------------------------------------------------------------
# Shared constants and helpers
# ---------------------------------------------------------------------------

NOW_FIXED    = 1_700_000_000_000   # 2023-11-14 22:13:20 UTC
GLOBAL_START = NOW_FIXED - LOOKBACK_DAYS * MS_PER_DAY
W1_START     = 1_699_395_200_000
W1_END       = 1_700_000_000_000
W2_START     = 1_698_790_399_999
W2_END       = 1_699_395_199_999
W3_START     = 1_698_185_599_998
W3_END       = 1_698_790_399_998


def raw(order_id: str, created_ms: int, updated_ms: int | None = None,
        status: str = "Filled") -> dict[str, Any]:
    """Build a minimal raw order dict for tests."""
    if updated_ms is None:
        updated_ms = created_ms + 1_000
    return {
        "orderId":     order_id,
        "symbol":      "ZECUSDT",
        "side":        "Buy",
        "orderType":   "Limit",
        "price":       "30.0",
        "qty":         "1",
        "orderStatus": status,
        "createdTime": str(created_ms),
        "updatedTime": str(updated_ms),
    }


def read_csv(path: pathlib.Path) -> list[list[str]]:
    return list(csv.reader(path.read_text(encoding="utf-8").splitlines()))


OrderRow: TypeAlias = tuple[
    str,    # order_id
    str,    # symbol
    str,    # side
    str,    # order_type
    float,  # price
    float,  # qty
    str,    # order_status
    str,    # created_ts
    str,    # updated_ts
    str,    # created_date
    str,    # created_time
    str,    # updated_date
    str,    # updated_time
]


def _make_history(*orders: OrderRow) -> OrderHistory:
    """
    Build an OrderHistory from
    (order_id, symbol, side, order_type, price, qty, order_status,
     created_ts, updated_ts,
     created_date, created_time, updated_date, updated_time) tuples.
    """
    return OrderHistory(
        symbol="ZECUSDT",
        category="linear",
        orders=[
            Order(
                order_id=oid, symbol=sym, side=sd, order_type=ot,
                price=p, qty=q, order_status=st,
                created_ts=cts, updated_ts=uts,
                created_date=cd, created_time=ct,
                updated_date=ud, updated_time=ut,
            )
            for oid, sym, sd, ot, p, q, st, cts, uts, cd, ct, ud, ut in orders
        ],
    )


SAMPLE_HISTORY = _make_history(
    ("ord-001", "ZECUSDT", "Buy",  "Limit",  30.50, 10.0, "Filled",
     "1700000001000", "1700000002000",
     "2023-11-14", "22:13:21", "2023-11-14", "22:13:22"),
    ("ord-002", "ZECUSDT", "Sell", "Market",  0.0,   5.0, "Filled",
     "1700000003000", "1700000004000",
     "2023-11-14", "22:13:23", "2023-11-14", "22:13:24"),
    ("ord-003", "ZECUSDT", "Buy",  "Limit",  29.00, 20.0, "Cancelled",
     "1700000005000", "1700000006000",
     "2023-11-14", "22:13:25", "2023-11-14", "22:13:26"),
)


# ===========================================================================
# OrderHistoryService — field mapping and basic behaviour
# ===========================================================================

class TestOrderHistoryService:

    @pytest.fixture(autouse=True)
    def freeze_time(self, monkeypatch: pytest.MonkeyPatch):
        import services.order_history as m
        monkeypatch.setattr(m, "_now_ms", lambda: NOW_FIXED)

    SAMPLE_RAW = [
        raw("ord-001", W1_END - 3_000, W1_END - 2_000, "Filled"),
        raw("ord-002", W1_END - 5_000, W1_END - 4_000, "Filled"),
        raw("ord-003", W1_END - 7_000, W1_END - 6_000, "Cancelled"),
    ]

    def test_returns_order_history_dataclass(self):
        result = OrderHistoryService(StubOrderClient(self.SAMPLE_RAW)).get_history("ZECUSDT")
        assert isinstance(result, OrderHistory)

    def test_orders_are_order_instances(self):
        result = OrderHistoryService(StubOrderClient(self.SAMPLE_RAW)).get_history("ZECUSDT")
        assert all(isinstance(o, Order) for o in result.orders)

    def test_symbol_is_uppercased(self):
        result = OrderHistoryService(StubOrderClient(self.SAMPLE_RAW)).get_history("zecusdt")
        assert result.symbol == "ZECUSDT"

    def test_category_passed_to_client(self):
        client = StubOrderClient(self.SAMPLE_RAW)
        OrderHistoryService(client, category="inverse").get_history("ZECUSDT")
        assert client.last_category == "inverse"

    def test_correct_number_of_orders(self):
        result = OrderHistoryService(StubOrderClient(self.SAMPLE_RAW)).get_history("ZECUSDT")
        assert len(result.orders) == 3

    # -----------------------------------------------------------------------
    # Field mapping — including raw timestamps
    # -----------------------------------------------------------------------

    def test_order_id_mapped(self):
        result = OrderHistoryService(StubOrderClient(self.SAMPLE_RAW)).get_history("ZECUSDT")
        order_ids = {o.order_id for o in result.orders}
        assert "ord-001" in order_ids

    def test_created_ts_is_raw_string(self):
        result = OrderHistoryService(StubOrderClient(self.SAMPLE_RAW)).get_history("ZECUSDT")
        o = next(o for o in result.orders if o.order_id == "ord-001")
        assert o.created_ts == str(W1_END - 3_000)

    def test_updated_ts_is_raw_string(self):
        result = OrderHistoryService(StubOrderClient(self.SAMPLE_RAW)).get_history("ZECUSDT")
        o = next(o for o in result.orders if o.order_id == "ord-001")
        assert o.updated_ts == str(W1_END - 2_000)

    def test_created_date_derived_from_created_ts(self):
        result = OrderHistoryService(StubOrderClient(self.SAMPLE_RAW)).get_history("ZECUSDT")
        o = next(o for o in result.orders if o.order_id == "ord-001")
        assert o.created_date == "2023-11-14"

    def test_created_time_derived_from_created_ts(self):
        result = OrderHistoryService(StubOrderClient(self.SAMPLE_RAW)).get_history("ZECUSDT")
        o = next(o for o in result.orders if o.order_id == "ord-001")
        # W1_END - 3_000 = 1699999997000 → check it's a valid HH:MM:SS
        assert len(o.created_time.split(":")) == 3

    def test_updated_date_derived_from_updated_ts(self):
        result = OrderHistoryService(StubOrderClient(self.SAMPLE_RAW)).get_history("ZECUSDT")
        o = next(o for o in result.orders if o.order_id == "ord-001")
        assert o.updated_date == "2023-11-14"

    def test_order_status_mapped(self):
        result = OrderHistoryService(StubOrderClient(self.SAMPLE_RAW)).get_history("ZECUSDT")
        cancelled = next(o for o in result.orders if o.order_id == "ord-003")
        assert cancelled.order_status == "Cancelled"

    def test_missing_timestamps_default_to_empty_strings(self):
        minimal = [{"side": "Buy", "orderType": "Limit", "price": "30.0", "qty": "1",
                    "createdTime": str(W1_END - 1_000), "updatedTime": ""}]
        result = OrderHistoryService(StubOrderClient(minimal)).get_history("ZECUSDT")
        o = result.orders[0]
        assert o.updated_ts == ""
        assert o.updated_date == ""
        assert o.updated_time == ""

    # -----------------------------------------------------------------------
    # Sorting by updatedTime DESC
    # -----------------------------------------------------------------------

    def test_orders_sorted_by_updated_ts_descending(self):
        # Arrange — provide 3 orders with different updatedTime values
        orders_raw = [
            raw("old",    W1_END - 9_000, W1_END - 8_000),
            raw("newest", W1_END - 1_000, W1_END - 500),
            raw("middle", W1_END - 5_000, W1_END - 4_000),
        ]
        result = OrderHistoryService(StubOrderClient(orders_raw)).get_history("ZECUSDT")

        # Assert — newest updated first
        ids = [o.order_id for o in result.orders]
        assert ids == ["newest", "middle", "old"]

    def test_sort_is_by_updated_ts_not_created_ts(self):
        # created order: A created first but updated last
        orders_raw = [
            raw("A", W1_END - 9_000, W1_END - 500),   # created early, updated latest
            raw("B", W1_END - 1_000, W1_END - 8_000), # created late, updated early
        ]
        result = OrderHistoryService(StubOrderClient(orders_raw)).get_history("ZECUSDT")

        ids = [o.order_id for o in result.orders]
        assert ids[0] == "A"   # A has the newest updatedTime
        assert ids[1] == "B"

    def test_equal_updated_ts_does_not_crash(self):
        # Two orders with identical updatedTime should not raise
        orders_raw = [
            raw("x", W1_END - 2_000, W1_END - 1_000),
            raw("y", W1_END - 3_000, W1_END - 1_000),
        ]
        result = OrderHistoryService(StubOrderClient(orders_raw)).get_history("ZECUSDT")
        assert len(result.orders) == 2

    # -----------------------------------------------------------------------
    # lookback_days is configurable via constructor
    # -----------------------------------------------------------------------

    def test_lookback_days_overridden_via_constructor(self):
        client = StubOrderClient(self.SAMPLE_RAW)
        # Use a 1-day lookback instead of the default 30
        svc = OrderHistoryService(client, lookback_days=1)
        result = svc.get_history("ZECUSDT")
        # The important thing: it doesn't crash and returns an OrderHistory
        assert isinstance(result, OrderHistory)

    # -----------------------------------------------------------------------
    # Empty response and errors
    # -----------------------------------------------------------------------

    def test_empty_response_returns_empty_orders_list(self):
        result = OrderHistoryService(StubOrderClient([])).get_history("ZECUSDT")
        assert result.orders == []

    def test_api_error_is_propagated(self):
        with pytest.raises(BybitAPIError):
            OrderHistoryService(StubOrderClient(raise_error=True)).get_history("ZECUSDT")

    def test_api_error_message_is_preserved(self):
        with pytest.raises(BybitAPIError, match="10003"):
            OrderHistoryService(StubOrderClient(raise_error=True)).get_history("ZECUSDT")


# ===========================================================================
# OrderHistoryService — 7-day window iteration
# ===========================================================================

class TestOrderHistoryWindowIteration:

    @pytest.fixture(autouse=True)
    def freeze_time(self, monkeypatch: pytest.MonkeyPatch):
        import services.order_history as m
        monkeypatch.setattr(m, "_now_ms", lambda: NOW_FIXED)

    def test_first_window_end_is_now(self):
        client = SequentialStubClient(pages=[[raw("o1", W1_END - 1_000)]])
        OrderHistoryService(client).get_history("ZECUSDT")
        assert client.call_log[0][1] == NOW_FIXED

    def test_first_window_start_is_now_minus_7_days(self):
        client = SequentialStubClient(pages=[[raw("o1", W1_END - 1_000)]])
        OrderHistoryService(client).get_history("ZECUSDT")
        assert client.call_log[0][0] == W1_START

    def test_second_window_end_is_w1_start_minus_one(self):
        client = SequentialStubClient(pages=[
            [raw("o1", W1_END - 1_000)],
            [],
            [raw("o2", W2_END - 1_000)],
        ])
        OrderHistoryService(client).get_history("ZECUSDT")
        end_times = [e for _, e in client.call_log]
        assert (W1_START - 1) in end_times

    def test_last_window_start_is_clamped_to_global_start(self):
        client = SequentialStubClient(pages=[
            [raw(f"o{i}", W1_END - i * 1_000)] for i in range(1, 6)
        ])
        OrderHistoryService(client).get_history("ZECUSDT")
        starts = [s for s, _ in client.call_log]
        assert GLOBAL_START in starts

    def test_window_span_does_not_exceed_7_days(self):
        client = SequentialStubClient(pages=[
            [raw(f"o{i}", W1_END - i * 1_000)] for i in range(1, 6)
        ])
        OrderHistoryService(client).get_history("ZECUSDT")
        for start, end in client.call_log:
            if start is not None and end is not None:
                assert (end - start) <= MAX_WINDOW_DAYS * MS_PER_DAY

    def test_empty_window_does_not_stop_outer_loop(self):
        client = SequentialStubClient(pages=[
            [],
            [raw("o2", W2_END - 1_000)],
        ])
        result = OrderHistoryService(client).get_history("ZECUSDT")
        assert any(o.order_id == "o2" for o in result.orders)

    def test_all_empty_windows_return_empty_history(self):
        client = SequentialStubClient(pages=[])
        result = OrderHistoryService(client).get_history("ZECUSDT")
        assert result.orders == []

    def test_all_empty_windows_make_at_most_5_calls(self):
        # 30d / 7d-per-window = 5 windows; pin lookback so the assertion
        # stays correct regardless of the LOOKBACK_DAYS module constant.
        client = SequentialStubClient(pages=[])
        OrderHistoryService(client).get_history("ZECUSDT", lookback_days=30)
        assert len(client.call_log) <= 5

    def test_orders_from_two_windows_are_combined(self):
        client = SequentialStubClient(pages=[
            [raw("o1", W1_END - 1_000)],
            [raw("o2", W2_END - 1_000)],
        ])
        result = OrderHistoryService(client).get_history("ZECUSDT")
        assert len(result.orders) == 2

    def test_gap_in_activity_handled(self):
        client = SequentialStubClient(pages=[
            [raw("o1", W1_END - 1_000)],
            [],
            [raw("o3", W3_END - 1_000)],
        ])
        result = OrderHistoryService(client).get_history("ZECUSDT")
        ids = {o.order_id for o in result.orders}
        assert "o1" in ids and "o3" in ids

    def test_multiple_pages_within_one_window_combined(self):
        T1, T2, T3 = W1_END - 1_000, W1_END - 2_000, W1_END - 3_000
        client = SequentialStubClient(pages=[
            [raw("o1", T1), raw("o2", T2)],
            [raw("o3", T3)],
        ])
        result = OrderHistoryService(client).get_history("ZECUSDT")
        assert len(result.orders) == 3

    def test_inner_page_end_time_advances_to_oldest_minus_one(self):
        T1, T2 = W1_END - 1_000, W1_END - 50_000
        client = SequentialStubClient(pages=[
            [raw("o1", T1), raw("o2", T2)],
            [],
        ])
        OrderHistoryService(client).get_history("ZECUSDT")
        assert client.call_log[1][1] == T2 - 1

    def test_duplicate_order_id_across_windows_appears_once(self):
        client = SequentialStubClient(pages=[
            [raw("dup", W1_END - 1_000), raw("o1", W1_END - 2_000)],
            [raw("dup", W1_END - 1_000), raw("o2", W2_END - 1_000)],
        ])
        result = OrderHistoryService(client).get_history("ZECUSDT")
        ids = [o.order_id for o in result.orders]
        assert ids.count("dup") == 1

    def test_result_is_sorted_by_updated_ts_desc_across_windows(self):
        # o1 in W1, o2 in W2; o1 has a later updatedTime
        client = SequentialStubClient(pages=[
            [raw("o1", W1_END - 1_000, W1_END - 500)],
            [raw("o2", W2_END - 1_000, W2_END - 500)],
        ])
        result = OrderHistoryService(client).get_history("ZECUSDT")
        # o1 updated_ts > o2 updated_ts → o1 should be first
        assert result.orders[0].order_id == "o1"
        assert result.orders[1].order_id == "o2"

    def test_error_propagates(self):
        class ErrClient:
            def get_order_history(self, symbol, category, limit,
                                  start_time=None, end_time=None,
                                  order_status=None):
                raise BybitAPIError("Bybit API error [10003]: boom")
        with pytest.raises(BybitAPIError, match="10003"):
            OrderHistoryService(ErrClient()).get_history("ZECUSDT")


# ===========================================================================
# OrderHistoryExporter tests
# ===========================================================================

class TestOrderHistoryExporter:

    def test_file_is_created(self, tmp_path: pathlib.Path):
        exporter = OrderHistoryExporter(tmp_path / "ZECUSDT_orderHistory.csv")
        exporter.export(SAMPLE_HISTORY)
        assert (tmp_path / "ZECUSDT_orderHistory.csv").exists()

    def test_data_directory_created_automatically(self, tmp_path: pathlib.Path):
        output = tmp_path / "data" / "ZECUSDT_orderHistory.csv"
        OrderHistoryExporter(output).export(SAMPLE_HISTORY)
        assert output.parent.is_dir()

    def test_file_is_overwritten_not_appended(self, tmp_path: pathlib.Path):
        output = tmp_path / "out.csv"
        exporter = OrderHistoryExporter(output)
        exporter.export(SAMPLE_HISTORY)
        single = _make_history(
            ("ord-001", "ZECUSDT", "Buy", "Limit", 30.5, 10.0, "Filled",
             "1700000001000", "1700000002000",
             "2023-11-14", "22:13:21", "2023-11-14", "22:13:22")
        )
        exporter.export(single)
        rows = read_csv(output)
        assert len(rows) == 2  # header + 1 order

    def test_correct_headers_are_written(self, tmp_path: pathlib.Path):
        output = tmp_path / "out.csv"
        OrderHistoryExporter(output).export(SAMPLE_HISTORY)
        rows = read_csv(output)
        assert rows[0] == HEADERS
        assert rows[0] == [
            "order_id", "symbol", "side", "order_type",
            "price", "qty", "order_status",
            "created_ts", "updated_ts",
            "created_date", "created_time",
            "updated_date", "updated_time",
        ]

    def test_raw_timestamp_columns_present(self, tmp_path: pathlib.Path):
        output = tmp_path / "out.csv"
        OrderHistoryExporter(output).export(SAMPLE_HISTORY)
        rows = read_csv(output)
        assert "created_ts" in rows[0]
        assert "updated_ts" in rows[0]

    def test_created_ts_before_created_date_in_header(self, tmp_path: pathlib.Path):
        output = tmp_path / "out.csv"
        OrderHistoryExporter(output).export(SAMPLE_HISTORY)
        headers = read_csv(output)[0]
        assert headers.index("created_ts") < headers.index("created_date")

    def test_updated_ts_before_updated_date_in_header(self, tmp_path: pathlib.Path):
        output = tmp_path / "out.csv"
        OrderHistoryExporter(output).export(SAMPLE_HISTORY)
        headers = read_csv(output)[0]
        assert headers.index("updated_ts") < headers.index("updated_date")

    def test_correct_number_of_rows(self, tmp_path: pathlib.Path):
        output = tmp_path / "out.csv"
        OrderHistoryExporter(output).export(SAMPLE_HISTORY)
        rows = read_csv(output)
        assert len(rows) == 4  # 1 header + 3 orders

    def test_correct_column_count_is_13(self, tmp_path: pathlib.Path):
        output = tmp_path / "out.csv"
        OrderHistoryExporter(output).export(SAMPLE_HISTORY)
        rows = read_csv(output)
        assert len(rows[0]) == 13
        assert len(rows[1]) == 13

    def test_raw_timestamps_written_to_csv(self, tmp_path: pathlib.Path):
        output = tmp_path / "out.csv"
        OrderHistoryExporter(output).export(SAMPLE_HISTORY)
        rows = read_csv(output)
        # created_ts is column index 7, updated_ts is index 8
        assert rows[1][7] == "1700000001000"
        assert rows[1][8] == "1700000002000"

    def test_correct_values_first_row(self, tmp_path: pathlib.Path):
        output = tmp_path / "out.csv"
        OrderHistoryExporter(output).export(SAMPLE_HISTORY)
        rows = read_csv(output)
        assert rows[1] == [
            "ord-001", "ZECUSDT", "Buy", "Limit",
            "30.5", "10.0", "Filled",
            "1700000001000", "1700000002000",
            "2023-11-14", "22:13:21",
            "2023-11-14", "22:13:22",
        ]

    def test_empty_history_writes_header_only(self, tmp_path: pathlib.Path):
        output = tmp_path / "out.csv"
        OrderHistoryExporter(output).export(_make_history())
        rows = read_csv(output)
        assert rows == [HEADERS]
