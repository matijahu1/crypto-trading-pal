"""
Unit tests for OrderHistoryService and OrderHistoryExporter.

The API client is replaced by a plain stub — no real HTTP calls,
no unittest.mock. Each test follows arrange → act → assert.
"""

import csv
import pathlib

import pytest

from services.order_history import (
    OrderHistoryService, OrderHistory, Order,
    LOOKBACK_DAYS, MAX_WINDOW_DAYS, _MS_PER_DAY,
)
from exporters.order_history_exporter import (
    OrderHistoryExporter,
    make_exporter,
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
    Records the arguments of the most recent call.
    """

    def __init__(
        self,
        orders: list[dict] | None = None,
        raise_error: bool = False,
    ) -> None:
        self._orders = orders or []
        self._raise_error = raise_error
        self.last_symbol: str | None = None
        self.last_category: str | None = None
        self.last_limit: int | None = None
        self.last_start_time: int | None = None
        self.last_end_time: int | None = None
        self.call_count: int = 0

    def get_order_history(
        self,
        symbol: str,
        category: str,
        limit: int,
        start_time: int | None = None,
        end_time: int | None = None,
    ) -> list[dict]:
        self.last_symbol     = symbol
        self.last_category   = category
        self.last_limit      = limit
        self.last_start_time = start_time
        self.last_end_time   = end_time
        self.call_count     += 1
        if self._raise_error:
            raise BybitAPIError("Bybit API error [10003]: Invalid api_key")
        return self._orders if self.call_count == 1 else []


class SequentialStubClient:
    """
    Returns pages[0] on call 1, pages[1] on call 2, [] once exhausted.
    Records every (start_time, end_time) pair in call_log.
    """

    def __init__(self, pages: list[list[dict]]) -> None:
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
    ) -> list[dict]:
        self.call_log.append((start_time, end_time))
        if self._idx < len(self._pages):
            page = self._pages[self._idx]
            self._idx += 1
            return page
        return []


# ---------------------------------------------------------------------------
# Realistic sample data and helpers
# ---------------------------------------------------------------------------

NOW_FIXED    = 1_700_000_000_000   # 2023-11-14 22:13:20 UTC
WINDOW_MS    = MAX_WINDOW_DAYS * _MS_PER_DAY
GLOBAL_START = NOW_FIXED - LOOKBACK_DAYS * _MS_PER_DAY

# Exact window boundaries (pre-computed from a loop trace with NOW_FIXED):
W1_START = 1_699_395_200_000;  W1_END = 1_700_000_000_000
W2_START = 1_698_790_399_999;  W2_END = 1_699_395_199_999
W3_START = 1_698_185_599_998;  W3_END = 1_698_790_399_998
W4_START = 1_697_580_799_997;  W4_END = 1_698_185_599_997
W5_START = 1_697_408_000_000;  W5_END = 1_697_580_799_996   # clamped

# Full SAMPLE_ORDERS uses timestamps inside W1 so they are always in-window
SAMPLE_ORDERS = [
    {
        "orderId":     "ord-001",
        "symbol":      "ZECUSDT",
        "side":        "Buy",
        "orderType":   "Limit",
        "price":       "30.50",
        "qty":         "10",
        "orderStatus": "Filled",
        "createdTime": str(W1_END - 1_000),
        "updatedTime": str(W1_END - 500),
    },
    {
        "orderId":     "ord-002",
        "symbol":      "ZECUSDT",
        "side":        "Sell",
        "orderType":   "Market",
        "price":       "0",
        "qty":         "5",
        "orderStatus": "Filled",
        "createdTime": str(W1_END - 2_000),
        "updatedTime": str(W1_END - 1_500),
    },
    {
        "orderId":     "ord-003",
        "symbol":      "ZECUSDT",
        "side":        "Buy",
        "orderType":   "Limit",
        "price":       "29.00",
        "qty":         "20",
        "orderStatus": "Cancelled",
        "createdTime": str(W1_END - 3_000),
        "updatedTime": str(W1_END - 2_500),
    },
]


def raw(order_id: str, created_time: int, status: str = "Filled") -> dict:
    """Build a minimal raw order dict for window/pagination tests."""
    return {
        "orderId":     order_id,
        "symbol":      "ZECUSDT",
        "side":        "Buy",
        "orderType":   "Limit",
        "price":       "30.0",
        "qty":         "1",
        "orderStatus": status,
        "createdTime": str(created_time),
        "updatedTime": str(created_time + 1_000),
    }


def read_csv(path: pathlib.Path) -> list[list[str]]:
    return list(csv.reader(path.read_text(encoding="utf-8").splitlines()))


def _make_history(*orders: tuple) -> OrderHistory:
    """
    Build an OrderHistory from
    (order_id, symbol, side, order_type, price, qty, order_status,
     created_date, created_time, updated_date, updated_time) tuples.
    """
    return OrderHistory(
        symbol="ZECUSDT",
        category="linear",
        orders=[
            Order(
                order_id=oid, symbol=sym, side=sd, order_type=ot,
                price=p, qty=q, order_status=st,
                created_date=cd, created_time=ct,
                updated_date=ud, updated_time=ut,
            )
            for oid, sym, sd, ot, p, q, st, cd, ct, ud, ut in orders
        ],
    )


SAMPLE_HISTORY = _make_history(
    ("ord-001", "ZECUSDT", "Buy",  "Limit",  30.50, 10.0, "Filled",
     "2023-11-14", "22:13:19", "2023-11-14", "22:13:19"),
    ("ord-002", "ZECUSDT", "Sell", "Market",  0.0,   5.0, "Filled",
     "2023-11-14", "22:13:18", "2023-11-14", "22:13:18"),
    ("ord-003", "ZECUSDT", "Buy",  "Limit",  29.00, 20.0, "Cancelled",
     "2023-11-14", "22:13:17", "2023-11-14", "22:13:17"),
)


# ===========================================================================
# OrderHistoryService — field mapping and basic behaviour
# ===========================================================================

class TestOrderHistoryService:
    """Basic field-level tests. StubOrderClient returns orders on call 1."""

    @pytest.fixture(autouse=True)
    def freeze_time(self, monkeypatch):
        import services.order_history as m
        monkeypatch.setattr(m, "_now_ms", lambda: NOW_FIXED)

    # -----------------------------------------------------------------------
    # Return types and structure
    # -----------------------------------------------------------------------

    def test_returns_order_history_dataclass(self):
        client = StubOrderClient(orders=SAMPLE_ORDERS)
        result = OrderHistoryService(client=client).get_history("ZECUSDT")
        assert isinstance(result, OrderHistory)

    def test_orders_are_order_instances(self):
        client = StubOrderClient(orders=SAMPLE_ORDERS)
        result = OrderHistoryService(client=client).get_history("ZECUSDT")
        assert all(isinstance(o, Order) for o in result.orders)

    def test_symbol_is_uppercased(self):
        client = StubOrderClient(orders=SAMPLE_ORDERS)
        result = OrderHistoryService(client=client).get_history("zecusdt")
        assert result.symbol == "ZECUSDT"

    def test_symbol_uppercased_before_passing_to_client(self):
        client = StubOrderClient(orders=SAMPLE_ORDERS)
        OrderHistoryService(client=client).get_history("zecusdt")
        assert client.last_symbol == "ZECUSDT"

    def test_category_passed_to_client(self):
        client = StubOrderClient(orders=SAMPLE_ORDERS)
        OrderHistoryService(client=client, category="inverse").get_history("ZECUSDT")
        assert client.last_category == "inverse"

    def test_category_preserved_in_result(self):
        client = StubOrderClient(orders=SAMPLE_ORDERS)
        result = OrderHistoryService(client=client, category="inverse").get_history("ZECUSDT")
        assert result.category == "inverse"

    def test_limit_passed_to_client(self):
        client = StubOrderClient(orders=SAMPLE_ORDERS)
        OrderHistoryService(client=client, limit=25).get_history("ZECUSDT")
        assert client.last_limit == 25

    def test_correct_number_of_orders_returned(self):
        client = StubOrderClient(orders=SAMPLE_ORDERS)
        result = OrderHistoryService(client=client).get_history("ZECUSDT")
        assert len(result.orders) == 3

    # -----------------------------------------------------------------------
    # Field mapping
    # -----------------------------------------------------------------------

    def test_order_id_mapped(self):
        client = StubOrderClient(orders=SAMPLE_ORDERS)
        result = OrderHistoryService(client=client).get_history("ZECUSDT")
        assert result.orders[0].order_id == "ord-001"

    def test_side_mapped(self):
        client = StubOrderClient(orders=SAMPLE_ORDERS)
        result = OrderHistoryService(client=client).get_history("ZECUSDT")
        assert result.orders[0].side == "Buy"
        assert result.orders[1].side == "Sell"

    def test_order_type_mapped(self):
        client = StubOrderClient(orders=SAMPLE_ORDERS)
        result = OrderHistoryService(client=client).get_history("ZECUSDT")
        assert result.orders[0].order_type == "Limit"
        assert result.orders[1].order_type == "Market"

    def test_price_mapped_as_float(self):
        client = StubOrderClient(orders=SAMPLE_ORDERS)
        result = OrderHistoryService(client=client).get_history("ZECUSDT")
        assert isinstance(result.orders[0].price, float)
        assert result.orders[0].price == pytest.approx(30.50)

    def test_market_order_price_is_zero(self):
        client = StubOrderClient(orders=SAMPLE_ORDERS)
        result = OrderHistoryService(client=client).get_history("ZECUSDT")
        assert result.orders[1].price == pytest.approx(0.0)

    def test_qty_mapped_as_float(self):
        client = StubOrderClient(orders=SAMPLE_ORDERS)
        result = OrderHistoryService(client=client).get_history("ZECUSDT")
        assert isinstance(result.orders[0].qty, float)
        assert result.orders[0].qty == pytest.approx(10.0)

    def test_order_status_mapped(self):
        client = StubOrderClient(orders=SAMPLE_ORDERS)
        result = OrderHistoryService(client=client).get_history("ZECUSDT")
        assert result.orders[0].order_status == "Filled"
        assert result.orders[2].order_status == "Cancelled"

    def test_created_date_converted(self):
        client = StubOrderClient(orders=SAMPLE_ORDERS)
        result = OrderHistoryService(client=client).get_history("ZECUSDT")
        assert result.orders[0].created_date == "2023-11-14"

    def test_created_time_converted(self):
        client = StubOrderClient(orders=SAMPLE_ORDERS)
        result = OrderHistoryService(client=client).get_history("ZECUSDT")
        assert result.orders[0].created_time == "22:13:19"

    def test_updated_date_converted(self):
        client = StubOrderClient(orders=SAMPLE_ORDERS)
        result = OrderHistoryService(client=client).get_history("ZECUSDT")
        assert result.orders[0].updated_date == "2023-11-14"

    def test_updated_time_converted(self):
        client = StubOrderClient(orders=SAMPLE_ORDERS)
        result = OrderHistoryService(client=client).get_history("ZECUSDT")
        # updatedTime = createdTime + 500 ms = W1_END - 1000 + 500 = W1_END - 500
        assert result.orders[0].updated_time == "22:13:19"

    def test_missing_optional_fields_default_gracefully(self):
        minimal = [{"side": "Buy", "orderType": "Limit", "price": "30.0", "qty": "1",
                    "createdTime": str(W1_END - 1_000)}]
        client = StubOrderClient(orders=minimal)
        result = OrderHistoryService(client=client).get_history("ZECUSDT")
        o = result.orders[0]
        assert o.order_id == ""
        assert o.order_status == ""
        assert o.price == pytest.approx(30.0)

    def test_original_order_preserved(self):
        client = StubOrderClient(orders=SAMPLE_ORDERS)
        result = OrderHistoryService(client=client).get_history("ZECUSDT")
        assert [o.order_id for o in result.orders] == ["ord-001", "ord-002", "ord-003"]

    # -----------------------------------------------------------------------
    # Empty response and errors
    # -----------------------------------------------------------------------

    def test_empty_response_returns_empty_orders_list(self):
        client = StubOrderClient(orders=[])
        result = OrderHistoryService(client=client).get_history("ZECUSDT")
        assert result.orders == []

    def test_empty_response_still_returns_order_history(self):
        client = StubOrderClient(orders=[])
        result = OrderHistoryService(client=client).get_history("ZECUSDT")
        assert isinstance(result, OrderHistory)

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
    """
    Tests for the outer-window loop and inner-page logic.

    Reference (NOW_FIXED = 1_700_000_000_000):
      LOOKBACK = 30d, WINDOW = 7d → 5 windows (4 full + 1 partial)

    Exact window boundaries (pre-computed):
      W1: [1_699_395_200_000,  1_700_000_000_000]  7.00d
      W2: [1_698_790_399_999,  1_699_395_199_999]  7.00d
      W3: [1_698_185_599_998,  1_698_790_399_998]  7.00d
      W4: [1_697_580_799_997,  1_698_185_599_997]  7.00d
      W5: [1_697_408_000_000,  1_697_580_799_996]  2.00d  ← clamped
    """

    @pytest.fixture(autouse=True)
    def freeze_time(self, monkeypatch):
        import services.order_history as m
        monkeypatch.setattr(m, "_now_ms", lambda: NOW_FIXED)

    # -----------------------------------------------------------------------
    # Window boundaries
    # -----------------------------------------------------------------------

    def test_first_window_end_is_now(self):
        # Arrange
        client = SequentialStubClient(pages=[
            [raw("o1", W1_END - 1_000)],
        ])

        # Act
        OrderHistoryService(client).get_history("ZECUSDT")

        # Assert
        assert client.call_log[0][1] == NOW_FIXED

    def test_first_window_start_is_now_minus_7_days(self):
        # Arrange
        client = SequentialStubClient(pages=[
            [raw("o1", W1_END - 1_000)],
        ])

        # Act
        OrderHistoryService(client).get_history("ZECUSDT")

        # Assert
        assert client.call_log[0][0] == W1_START

    def test_second_window_end_is_w1_start_minus_one(self):
        # W1 ends, then W2 should have end_time = W1_START - 1.
        # Provide: W1 page, W1 empty page (ends inner loop), W2 page.
        client = SequentialStubClient(pages=[
            [raw("o1", W1_END - 1_000)],   # W1 page 1
            [],                             # W1 page 2 — inner loop ends
            [raw("o2", W2_END - 1_000)],   # W2 page 1
        ])

        # Act
        OrderHistoryService(client).get_history("ZECUSDT")

        # Assert — W1_START - 1 must appear as an end_time in the log
        end_times = [e for _, e in client.call_log]
        assert (W1_START - 1) in end_times

    def test_last_window_start_is_clamped_to_global_start(self):
        # One non-empty page per window so the outer loop runs all 5
        client = SequentialStubClient(pages=[
            [raw(f"o{i}", W1_END - i * 1_000)] for i in range(1, 6)
        ])

        # Act
        OrderHistoryService(client).get_history("ZECUSDT")

        # Assert
        starts = [s for s, _ in client.call_log]
        assert GLOBAL_START in starts

    def test_window_span_does_not_exceed_7_days(self):
        # Every API call must cover ≤ 7 days
        client = SequentialStubClient(pages=[
            [raw(f"o{i}", W1_END - i * 1_000)] for i in range(1, 6)
        ])

        # Act
        OrderHistoryService(client).get_history("ZECUSDT")

        # Assert
        for start, end in client.call_log:
            if start is not None and end is not None:
                assert (end - start) <= MAX_WINDOW_DAYS * _MS_PER_DAY

    # -----------------------------------------------------------------------
    # Empty windows do NOT stop the outer loop
    # -----------------------------------------------------------------------

    def test_empty_window_does_not_stop_outer_loop(self):
        # W1 is empty; W2 has an order — outer loop must continue
        client = SequentialStubClient(pages=[
            [],                             # W1 empty
            [raw("o2", W2_END - 1_000)],   # W2 has data
        ])

        # Act
        result = OrderHistoryService(client).get_history("ZECUSDT")

        # Assert
        assert any(o.order_id == "o2" for o in result.orders)

    def test_two_empty_windows_then_data(self):
        # W1 and W2 are empty; W3 has an order
        client = SequentialStubClient(pages=[
            [],                             # W1 empty
            [],                             # W2 empty
            [raw("o3", W3_END - 1_000)],   # W3 has data
        ])

        # Act
        result = OrderHistoryService(client).get_history("ZECUSDT")

        # Assert
        assert any(o.order_id == "o3" for o in result.orders)

    def test_all_empty_windows_return_empty_history(self):
        # Arrange
        client = SequentialStubClient(pages=[])

        # Act
        result = OrderHistoryService(client).get_history("ZECUSDT")

        # Assert
        assert result.orders == []

    def test_all_empty_windows_make_at_most_5_calls(self):
        # 30d / 7d = 5 windows; each makes exactly 1 call when empty
        client = SequentialStubClient(pages=[])

        # Act
        OrderHistoryService(client).get_history("ZECUSDT")

        # Assert
        assert len(client.call_log) <= 5

    # -----------------------------------------------------------------------
    # Data aggregation across windows
    # -----------------------------------------------------------------------

    def test_orders_from_two_windows_are_combined(self):
        # Arrange
        client = SequentialStubClient(pages=[
            [raw("o1", W1_END - 1_000)],
            [raw("o2", W2_END - 1_000)],
        ])

        # Act
        result = OrderHistoryService(client).get_history("ZECUSDT")

        # Assert
        assert len(result.orders) == 2
        assert {o.order_id for o in result.orders} == {"o1", "o2"}

    def test_orders_from_all_five_windows_are_combined(self):
        # Arrange
        client = SequentialStubClient(pages=[
            [raw(f"o{i}", W1_END - i * 1_000)] for i in range(1, 6)
        ])

        # Act
        result = OrderHistoryService(client).get_history("ZECUSDT")

        # Assert
        assert len(result.orders) == 5

    def test_gap_in_order_activity_handled(self):
        # W1 and W3 have orders; W2 is empty (trading gap)
        client = SequentialStubClient(pages=[
            [raw("o1", W1_END - 1_000)],   # W1
            [],                             # W2: gap
            [raw("o3", W3_END - 1_000)],   # W3
        ])

        # Act
        result = OrderHistoryService(client).get_history("ZECUSDT")

        # Assert
        ids = {o.order_id for o in result.orders}
        assert "o1" in ids and "o3" in ids

    # -----------------------------------------------------------------------
    # Inner-loop paging within one window
    # -----------------------------------------------------------------------

    def test_multiple_pages_within_one_window_combined(self):
        # W1 has two pages of orders
        T1, T2, T3 = W1_END - 1_000, W1_END - 2_000, W1_END - 3_000
        client = SequentialStubClient(pages=[
            [raw("o1", T1), raw("o2", T2)],   # W1 page 1
            [raw("o3", T3)],                   # W1 page 2
        ])

        # Act
        result = OrderHistoryService(client).get_history("ZECUSDT")

        # Assert
        assert len(result.orders) == 3

    def test_inner_page_end_time_advances_to_oldest_minus_one(self):
        # After page 1, the next call's end_time should be oldest_in_page - 1
        T1, T2 = W1_END - 1_000, W1_END - 50_000   # T2 is oldest on page 1
        client = SequentialStubClient(pages=[
            [raw("o1", T1), raw("o2", T2)],   # W1 page 1
            [],                                # W1 page 2 — empty, inner loop ends
        ])

        # Act
        OrderHistoryService(client).get_history("ZECUSDT")

        # Assert — second call should have end_time = T2 - 1
        assert client.call_log[1][1] == T2 - 1

    # -----------------------------------------------------------------------
    # Deduplication across window boundaries
    # -----------------------------------------------------------------------

    def test_duplicate_order_id_across_windows_appears_once(self):
        # "dup" appears at the boundary between W1 and W2
        dup_time = W1_END - 1_000
        client = SequentialStubClient(pages=[
            [raw("dup", dup_time), raw("o1", W1_END - 2_000)],   # W1
            [raw("dup", dup_time), raw("o2", W2_END - 1_000)],   # W2
        ])

        # Act
        result = OrderHistoryService(client).get_history("ZECUSDT")

        # Assert
        ids = [o.order_id for o in result.orders]
        assert ids.count("dup") == 1

    # -----------------------------------------------------------------------
    # No-infinite-loop guarantee
    # -----------------------------------------------------------------------

    def test_terminates_with_all_empty_responses(self):
        # Arrange
        client = SequentialStubClient(pages=[])

        # Act
        result = OrderHistoryService(client).get_history("ZECUSDT")

        # Assert
        assert isinstance(result, OrderHistory)

    def test_return_type_is_always_order_history(self):
        client = SequentialStubClient(pages=[])
        result = OrderHistoryService(client).get_history("ZECUSDT")
        assert isinstance(result, OrderHistory)

    # -----------------------------------------------------------------------
    # Error propagation
    # -----------------------------------------------------------------------

    def test_error_from_any_window_propagates(self):
        class ErrClient:
            def get_order_history(self, symbol, category, limit,
                                  start_time=None, end_time=None):
                raise BybitAPIError("Bybit API error [10003]: boom")

        with pytest.raises(BybitAPIError, match="10003"):
            OrderHistoryService(ErrClient()).get_history("ZECUSDT")

    # -----------------------------------------------------------------------
    # Constants
    # -----------------------------------------------------------------------

    def test_lookback_days_is_30(self):
        assert LOOKBACK_DAYS == 30

    def test_max_window_days_is_7(self):
        assert MAX_WINDOW_DAYS == 7


# ===========================================================================
# OrderHistoryExporter tests — unchanged from before
# ===========================================================================

class TestOrderHistoryExporter:

    def test_file_is_created(self, tmp_path):
        exporter = OrderHistoryExporter(tmp_path / "ZECUSDT_orderHistory.csv")
        exporter.export(SAMPLE_HISTORY)
        assert (tmp_path / "ZECUSDT_orderHistory.csv").exists()

    def test_data_directory_created_automatically(self, tmp_path):
        output = tmp_path / "data" / "ZECUSDT_orderHistory.csv"
        OrderHistoryExporter(output).export(SAMPLE_HISTORY)
        assert output.parent.is_dir()

    def test_file_is_overwritten_not_appended(self, tmp_path):
        output = tmp_path / "out.csv"
        exporter = OrderHistoryExporter(output)
        exporter.export(SAMPLE_HISTORY)
        single = _make_history(
            ("ord-001", "ZECUSDT", "Buy", "Limit", 30.5, 10.0, "Filled",
             "2023-11-14", "22:13:19", "2023-11-14", "22:13:19")
        )
        exporter.export(single)
        rows = read_csv(output)
        assert len(rows) == 2  # header + 1 order

    def test_correct_headers_are_written(self, tmp_path):
        output = tmp_path / "out.csv"
        OrderHistoryExporter(output).export(SAMPLE_HISTORY)
        rows = read_csv(output)
        assert rows[0] == HEADERS
        assert rows[0] == [
            "order_id", "symbol", "side", "order_type",
            "price", "qty", "order_status",
            "created_date", "created_time",
            "updated_date", "updated_time",
        ]

    def test_correct_number_of_rows(self, tmp_path):
        output = tmp_path / "out.csv"
        OrderHistoryExporter(output).export(SAMPLE_HISTORY)
        rows = read_csv(output)
        assert len(rows) == 4  # 1 header + 3 orders

    def test_correct_values_first_row(self, tmp_path):
        output = tmp_path / "out.csv"
        OrderHistoryExporter(output).export(SAMPLE_HISTORY)
        rows = read_csv(output)
        assert rows[1] == [
            "ord-001", "ZECUSDT", "Buy", "Limit",
            "30.5", "10.0", "Filled",
            "2023-11-14", "22:13:19",
            "2023-11-14", "22:13:19",
        ]

    def test_correct_values_second_row(self, tmp_path):
        output = tmp_path / "out.csv"
        OrderHistoryExporter(output).export(SAMPLE_HISTORY)
        rows = read_csv(output)
        assert rows[2] == [
            "ord-002", "ZECUSDT", "Sell", "Market",
            "0.0", "5.0", "Filled",
            "2023-11-14", "22:13:18",
            "2023-11-14", "22:13:18",
        ]

    def test_cancelled_order_status_written(self, tmp_path):
        output = tmp_path / "out.csv"
        OrderHistoryExporter(output).export(SAMPLE_HISTORY)
        rows = read_csv(output)
        assert rows[3][6] == "Cancelled"

    def test_correct_column_count(self, tmp_path):
        output = tmp_path / "out.csv"
        OrderHistoryExporter(output).export(SAMPLE_HISTORY)
        rows = read_csv(output)
        assert len(rows[0]) == 11
        assert len(rows[1]) == 11

    def test_empty_history_writes_header_only(self, tmp_path):
        output = tmp_path / "out.csv"
        OrderHistoryExporter(output).export(_make_history())
        rows = read_csv(output)
        assert rows == [HEADERS]

    def test_make_exporter_builds_correct_filename(self, tmp_path):
        assert make_exporter("ZECUSDT", output_dir=tmp_path)._output_path == \
               tmp_path / "ZECUSDT_orderHistory.csv"

    def test_make_exporter_uppercases_symbol(self, tmp_path):
        assert make_exporter("zecusdt", output_dir=tmp_path)._output_path.name == \
               "ZECUSDT_orderHistory.csv"

    def test_make_exporter_default_dir_is_data(self):
        assert make_exporter("ZECUSDT")._output_path == \
               pathlib.Path("data") / "ZECUSDT_orderHistory.csv"
