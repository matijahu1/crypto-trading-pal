"""
Unit tests for OrderHistoryService and OrderHistoryExporter.

The API client is replaced by a plain stub — no real HTTP calls,
no unittest.mock. Each test follows arrange → act → assert.
"""

import csv
import pathlib

import pytest

from services.order_history import OrderHistoryService, OrderHistory, Order
from exporters.order_history_exporter import (
    OrderHistoryExporter,
    make_exporter,
    HEADERS,
)
from api.bybit_client import BybitAPIError


# ---------------------------------------------------------------------------
# Stub client
# ---------------------------------------------------------------------------

class StubOrderClient:
    """
    In-memory stub satisfying OrderHistoryClientProtocol.

    Pass `orders` to control what the API returns.
    Pass `raise_error=True` to simulate a network / auth failure.
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

    def get_order_history(self, symbol: str, category: str, limit: int) -> list[dict]:
        self.last_symbol = symbol
        self.last_category = category
        self.last_limit = limit
        if self._raise_error:
            raise BybitAPIError("Bybit API error [10003]: Invalid api_key")
        return self._orders


# ---------------------------------------------------------------------------
# Realistic sample data (mirrors actual Bybit V5 order history response fields)
# Timestamps:
#   1700000000000 -> 2023-11-14  22:13:20  (created)
#   1700000060000 -> 2023-11-14  22:14:20  (updated)
#   1700003600000 -> 2023-11-14  23:13:20  (created)
#   1700003700000 -> 2023-11-14  23:15:00  (updated)
# ---------------------------------------------------------------------------

SAMPLE_ORDERS = [
    {
        "orderId":     "ord-001",
        "symbol":      "ZECUSDT",
        "side":        "Buy",
        "orderType":   "Limit",
        "price":       "30.50",
        "qty":         "10",
        "orderStatus": "Filled",
        "createdTime": "1700000000000",
        "updatedTime": "1700000060000",
    },
    {
        "orderId":     "ord-002",
        "symbol":      "ZECUSDT",
        "side":        "Sell",
        "orderType":   "Market",
        "price":       "0",
        "qty":         "5",
        "orderStatus": "Filled",
        "createdTime": "1700003600000",
        "updatedTime": "1700003700000",
    },
    {
        "orderId":     "ord-003",
        "symbol":      "ZECUSDT",
        "side":        "Buy",
        "orderType":   "Limit",
        "price":       "29.00",
        "qty":         "20",
        "orderStatus": "Cancelled",
        "createdTime": "1700003600000",
        "updatedTime": "1700003700000",
    },
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def read_csv(path: pathlib.Path) -> list[list[str]]:
    """Return all rows (including the header) as a list of string lists."""
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
    ("ord-001", "ZECUSDT", "Buy",  "Limit",  30.50, 10.0, "Filled",    "2023-11-14", "22:13:20", "2023-11-14", "22:14:20"),
    ("ord-002", "ZECUSDT", "Sell", "Market",  0.0,   5.0, "Filled",    "2023-11-14", "23:13:20", "2023-11-14", "23:15:00"),
    ("ord-003", "ZECUSDT", "Buy",  "Limit",  29.00, 20.0, "Cancelled", "2023-11-14", "23:13:20", "2023-11-14", "23:15:00"),
)


# ===========================================================================
# OrderHistoryService tests
# ===========================================================================

class TestOrderHistoryService:

    # -----------------------------------------------------------------------
    # Return types and structure
    # -----------------------------------------------------------------------

    def test_returns_order_history_dataclass(self):
        # Arrange
        client = StubOrderClient(orders=SAMPLE_ORDERS)
        service = OrderHistoryService(client=client)

        # Act
        result = service.get_history("ZECUSDT")

        # Assert
        assert isinstance(result, OrderHistory)

    def test_orders_are_order_instances(self):
        # Arrange
        client = StubOrderClient(orders=SAMPLE_ORDERS)
        service = OrderHistoryService(client=client)

        # Act
        result = service.get_history("ZECUSDT")

        # Assert
        assert all(isinstance(o, Order) for o in result.orders)

    def test_symbol_is_uppercased(self):
        # Arrange
        client = StubOrderClient(orders=SAMPLE_ORDERS)
        service = OrderHistoryService(client=client)

        # Act
        result = service.get_history("zecusdt")

        # Assert
        assert result.symbol == "ZECUSDT"

    def test_symbol_uppercased_before_passing_to_client(self):
        # Arrange
        client = StubOrderClient(orders=SAMPLE_ORDERS)
        service = OrderHistoryService(client=client)

        # Act
        service.get_history("zecusdt")

        # Assert
        assert client.last_symbol == "ZECUSDT"

    def test_category_passed_to_client(self):
        # Arrange
        client = StubOrderClient(orders=SAMPLE_ORDERS)
        service = OrderHistoryService(client=client, category="inverse")

        # Act
        service.get_history("ZECUSDT")

        # Assert
        assert client.last_category == "inverse"

    def test_category_preserved_in_result(self):
        # Arrange
        client = StubOrderClient(orders=SAMPLE_ORDERS)
        service = OrderHistoryService(client=client, category="inverse")

        # Act
        result = service.get_history("ZECUSDT")

        # Assert
        assert result.category == "inverse"

    def test_limit_passed_to_client(self):
        # Arrange
        client = StubOrderClient(orders=SAMPLE_ORDERS)
        service = OrderHistoryService(client=client, limit=25)

        # Act
        service.get_history("ZECUSDT")

        # Assert
        assert client.last_limit == 25

    def test_correct_number_of_orders_returned(self):
        # Arrange — SAMPLE_ORDERS has 3 entries
        client = StubOrderClient(orders=SAMPLE_ORDERS)
        service = OrderHistoryService(client=client)

        # Act
        result = service.get_history("ZECUSDT")

        # Assert
        assert len(result.orders) == 3

    # -----------------------------------------------------------------------
    # Field mapping
    # -----------------------------------------------------------------------

    def test_order_id_mapped(self):
        # Arrange
        client = StubOrderClient(orders=SAMPLE_ORDERS)
        service = OrderHistoryService(client=client)

        # Act
        result = service.get_history("ZECUSDT")

        # Assert
        assert result.orders[0].order_id == "ord-001"

    def test_side_mapped(self):
        # Arrange
        client = StubOrderClient(orders=SAMPLE_ORDERS)
        service = OrderHistoryService(client=client)

        # Act
        result = service.get_history("ZECUSDT")

        # Assert
        assert result.orders[0].side == "Buy"
        assert result.orders[1].side == "Sell"

    def test_order_type_mapped(self):
        # Arrange
        client = StubOrderClient(orders=SAMPLE_ORDERS)
        service = OrderHistoryService(client=client)

        # Act
        result = service.get_history("ZECUSDT")

        # Assert
        assert result.orders[0].order_type == "Limit"
        assert result.orders[1].order_type == "Market"

    def test_price_mapped_as_float(self):
        # Arrange
        client = StubOrderClient(orders=SAMPLE_ORDERS)
        service = OrderHistoryService(client=client)

        # Act
        result = service.get_history("ZECUSDT")

        # Assert
        assert isinstance(result.orders[0].price, float)
        assert result.orders[0].price == pytest.approx(30.50)

    def test_market_order_price_is_zero(self):
        # Arrange — ord-002 is a Market order with price "0"
        client = StubOrderClient(orders=SAMPLE_ORDERS)
        service = OrderHistoryService(client=client)

        # Act
        result = service.get_history("ZECUSDT")

        # Assert
        assert result.orders[1].price == pytest.approx(0.0)

    def test_qty_mapped_as_float(self):
        # Arrange
        client = StubOrderClient(orders=SAMPLE_ORDERS)
        service = OrderHistoryService(client=client)

        # Act
        result = service.get_history("ZECUSDT")

        # Assert
        assert isinstance(result.orders[0].qty, float)
        assert result.orders[0].qty == pytest.approx(10.0)

    def test_order_status_mapped(self):
        # Arrange
        client = StubOrderClient(orders=SAMPLE_ORDERS)
        service = OrderHistoryService(client=client)

        # Act
        result = service.get_history("ZECUSDT")

        # Assert
        assert result.orders[0].order_status == "Filled"
        assert result.orders[2].order_status == "Cancelled"

    def test_created_date_converted_from_created_time(self):
        # Arrange
        client = StubOrderClient(orders=SAMPLE_ORDERS)
        service = OrderHistoryService(client=client)

        # Act
        result = service.get_history("ZECUSDT")

        # Assert
        assert result.orders[0].created_date == "2023-11-14"

    def test_created_time_converted_from_created_time(self):
        # Arrange
        client = StubOrderClient(orders=SAMPLE_ORDERS)
        service = OrderHistoryService(client=client)

        # Act
        result = service.get_history("ZECUSDT")

        # Assert
        assert result.orders[0].created_time == "22:13:20"

    def test_updated_date_converted_from_updated_time(self):
        # Arrange
        client = StubOrderClient(orders=SAMPLE_ORDERS)
        service = OrderHistoryService(client=client)

        # Act
        result = service.get_history("ZECUSDT")

        # Assert
        assert result.orders[0].updated_date == "2023-11-14"

    def test_updated_time_converted_from_updated_time(self):
        # Arrange
        client = StubOrderClient(orders=SAMPLE_ORDERS)
        service = OrderHistoryService(client=client)

        # Act
        result = service.get_history("ZECUSDT")

        # Assert
        assert result.orders[0].updated_time == "22:14:20"

    def test_original_order_preserved(self):
        # Arrange — API returns newest-first; service must not reorder
        client = StubOrderClient(orders=SAMPLE_ORDERS)
        service = OrderHistoryService(client=client)

        # Act
        result = service.get_history("ZECUSDT")

        # Assert
        ids = [o.order_id for o in result.orders]
        assert ids == ["ord-001", "ord-002", "ord-003"]

    def test_missing_optional_fields_default_gracefully(self):
        # Arrange — only mandatory-ish fields present
        minimal = [{"side": "Buy", "orderType": "Limit", "price": "30.0", "qty": "1"}]
        client = StubOrderClient(orders=minimal)
        service = OrderHistoryService(client=client)

        # Act
        result = service.get_history("ZECUSDT")

        # Assert — no exception; empty strings for missing str fields
        o = result.orders[0]
        assert o.order_id == ""
        assert o.order_status == ""
        assert o.created_date == ""
        assert o.created_time == ""
        assert o.updated_date == ""
        assert o.updated_time == ""
        assert o.price == pytest.approx(30.0)

    # -----------------------------------------------------------------------
    # Empty response
    # -----------------------------------------------------------------------

    def test_empty_response_returns_empty_orders_list(self):
        # Arrange
        client = StubOrderClient(orders=[])
        service = OrderHistoryService(client=client)

        # Act
        result = service.get_history("ZECUSDT")

        # Assert
        assert result.orders == []

    def test_empty_response_still_returns_order_history(self):
        # Arrange
        client = StubOrderClient(orders=[])
        service = OrderHistoryService(client=client)

        # Act
        result = service.get_history("ZECUSDT")

        # Assert
        assert isinstance(result, OrderHistory)
        assert result.symbol == "ZECUSDT"

    # -----------------------------------------------------------------------
    # Error handling
    # -----------------------------------------------------------------------

    def test_api_error_is_propagated(self):
        # Arrange
        client = StubOrderClient(raise_error=True)
        service = OrderHistoryService(client=client)

        # Act / Assert
        with pytest.raises(BybitAPIError):
            service.get_history("ZECUSDT")

    def test_api_error_message_is_preserved(self):
        # Arrange
        client = StubOrderClient(raise_error=True)
        service = OrderHistoryService(client=client)

        # Act / Assert
        with pytest.raises(BybitAPIError, match="10003"):
            service.get_history("ZECUSDT")


# ===========================================================================
# OrderHistoryExporter tests
# ===========================================================================

class TestOrderHistoryExporter:

    def test_file_is_created(self, tmp_path):
        # Arrange
        exporter = OrderHistoryExporter(tmp_path / "ZECUSDT_orderHistory.csv")

        # Act
        exporter.export(SAMPLE_HISTORY)

        # Assert
        assert (tmp_path / "ZECUSDT_orderHistory.csv").exists()

    def test_data_directory_created_automatically(self, tmp_path):
        # Arrange — data/ sub-dir does not yet exist
        output = tmp_path / "data" / "ZECUSDT_orderHistory.csv"
        exporter = OrderHistoryExporter(output)

        # Act
        exporter.export(SAMPLE_HISTORY)

        # Assert
        assert output.parent.is_dir()

    def test_file_is_overwritten_not_appended(self, tmp_path):
        # Arrange — export 3 orders, then re-export 1 order
        output = tmp_path / "out.csv"
        exporter = OrderHistoryExporter(output)
        exporter.export(SAMPLE_HISTORY)
        single = _make_history(
            ("ord-001", "ZECUSDT", "Buy", "Limit", 30.5, 10.0, "Filled",
             "2023-11-14", "22:13:20", "2023-11-14", "22:14:20")
        )

        # Act
        exporter.export(single)

        # Assert — 1 data row, not 3
        rows = read_csv(output)
        assert len(rows) == 2  # header + 1 order

    def test_correct_headers_are_written(self, tmp_path):
        # Arrange
        output = tmp_path / "out.csv"
        exporter = OrderHistoryExporter(output)

        # Act
        exporter.export(SAMPLE_HISTORY)

        # Assert
        rows = read_csv(output)
        assert rows[0] == HEADERS
        assert rows[0] == [
            "order_id", "symbol", "side", "order_type",
            "price", "qty", "order_status",
            "created_date", "created_time",
            "updated_date", "updated_time",
        ]

    def test_correct_number_of_rows(self, tmp_path):
        # Arrange — SAMPLE_HISTORY has 3 orders
        output = tmp_path / "out.csv"
        exporter = OrderHistoryExporter(output)

        # Act
        exporter.export(SAMPLE_HISTORY)

        # Assert — 1 header + 3 data rows
        rows = read_csv(output)
        assert len(rows) == 4

    def test_correct_values_first_row(self, tmp_path):
        # Arrange
        output = tmp_path / "out.csv"
        exporter = OrderHistoryExporter(output)

        # Act
        exporter.export(SAMPLE_HISTORY)

        # Assert
        rows = read_csv(output)
        assert rows[1] == [
            "ord-001", "ZECUSDT", "Buy", "Limit",
            "30.5", "10.0", "Filled",
            "2023-11-14", "22:13:20",
            "2023-11-14", "22:14:20",
        ]

    def test_correct_values_second_row(self, tmp_path):
        # Arrange
        output = tmp_path / "out.csv"
        exporter = OrderHistoryExporter(output)

        # Act
        exporter.export(SAMPLE_HISTORY)

        # Assert
        rows = read_csv(output)
        assert rows[2] == [
            "ord-002", "ZECUSDT", "Sell", "Market",
            "0.0", "5.0", "Filled",
            "2023-11-14", "23:13:20",
            "2023-11-14", "23:15:00",
        ]

    def test_cancelled_order_status_written(self, tmp_path):
        # Arrange
        output = tmp_path / "out.csv"
        exporter = OrderHistoryExporter(output)

        # Act
        exporter.export(SAMPLE_HISTORY)

        # Assert
        rows = read_csv(output)
        assert rows[3][6] == "Cancelled"

    def test_correct_column_count(self, tmp_path):
        # Arrange — 11 columns expected
        output = tmp_path / "out.csv"
        exporter = OrderHistoryExporter(output)

        # Act
        exporter.export(SAMPLE_HISTORY)

        # Assert
        rows = read_csv(output)
        assert len(rows[0]) == 11
        assert len(rows[1]) == 11

    def test_empty_history_writes_header_only(self, tmp_path):
        # Arrange
        output = tmp_path / "out.csv"
        exporter = OrderHistoryExporter(output)
        empty = _make_history()

        # Act
        exporter.export(empty)

        # Assert
        rows = read_csv(output)
        assert len(rows) == 1
        assert rows[0] == HEADERS

    def test_make_exporter_builds_correct_filename(self, tmp_path):
        # Arrange / Act
        exporter = make_exporter("ZECUSDT", output_dir=tmp_path)

        # Assert
        assert exporter._output_path == tmp_path / "ZECUSDT_orderHistory.csv"

    def test_make_exporter_uppercases_symbol(self, tmp_path):
        # Arrange / Act
        exporter = make_exporter("zecusdt", output_dir=tmp_path)

        # Assert
        assert exporter._output_path.name == "ZECUSDT_orderHistory.csv"

    def test_make_exporter_default_dir_is_data(self):
        # Arrange / Act
        exporter = make_exporter("ZECUSDT")

        # Assert
        assert exporter._output_path == pathlib.Path("data") / "ZECUSDT_orderHistory.csv"
