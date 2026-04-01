"""
Unit tests for TradeHistoryService, TradeHistoryExporter, and
the ms_timestamp_to_date_time helper.

The API client is replaced by a plain stub — no real HTTP calls,
no unittest.mock. Each test follows arrange → act → assert.
"""

import csv
import pathlib

import pytest

from utils.time_utils import ms_timestamp_to_date_time
from services.trade_history import TradeHistoryService, TradeHistory, Trade
from exporters.trade_history_exporter import (
    TradeHistoryExporter,
    make_exporter,
    HEADERS,
)
from api.bybit_client import BybitAPIError


# ---------------------------------------------------------------------------
# Stub client
# ---------------------------------------------------------------------------

class StubTradeClient:
    """
    In-memory stub satisfying TradeHistoryClientProtocol.

    Pass `trades` to control what the API returns.
    Pass `raise_error=True` to simulate a network / auth failure.
    """

    def __init__(
        self,
        trades: list[dict] | None = None,
        raise_error: bool = False,
    ) -> None:
        self._trades = trades or []
        self._raise_error = raise_error
        self.last_symbol: str | None = None
        self.last_category: str | None = None
        self.last_limit: int | None = None

    def get_trade_history(self, symbol: str, category: str, limit: int) -> list[dict]:
        self.last_symbol = symbol
        self.last_category = category
        self.last_limit = limit
        if self._raise_error:
            raise BybitAPIError("Bybit API error [10003]: Invalid api_key")
        return self._trades


# ---------------------------------------------------------------------------
# Realistic sample data (mirrors actual Bybit V5 execution response fields)
# Timestamps:
#   1700000000000 -> 2023-11-14  22:13:20
#   1700003600000 -> 2023-11-14  23:13:20
#   1700007200000 -> 2023-11-15  00:13:20
# ---------------------------------------------------------------------------

SAMPLE_TRADES = [
    {
        "execId":    "exec-001",
        "symbol":    "ZECUSDT",
        "side":      "Buy",
        "execPrice": "30.50",
        "execQty":   "10",
        "execTime":  "1700000000000",
    },
    {
        "execId":    "exec-002",
        "symbol":    "ZECUSDT",
        "side":      "Sell",
        "execPrice": "31.00",
        "execQty":   "5",
        "execTime":  "1700003600000",
    },
    {
        "execId":    "exec-003",
        "symbol":    "ZECUSDT",
        "side":      "Buy",
        "execPrice": "29.75",
        "execQty":   "20",
        "execTime":  "1700007200000",
    },
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def read_csv(path: pathlib.Path) -> list[list[str]]:
    """Return all rows (including the header) as a list of string lists."""
    return list(csv.reader(path.read_text(encoding="utf-8").splitlines()))


# ===========================================================================
# ms_timestamp_to_date_time tests
# ===========================================================================

class TestMsTimestampToDateTime:

    def test_returns_tuple_of_two_strings(self):
        result = ms_timestamp_to_date_time("1700000000000")

        assert isinstance(result, tuple)
        assert len(result) == 2

    def test_known_timestamp_date(self):
        date, _ = ms_timestamp_to_date_time("1700000000000")

        assert date == "2023-11-14"

    def test_known_timestamp_time(self):
        _, time = ms_timestamp_to_date_time("1700000000000")

        assert time == "22:13:20"

    def test_date_format_is_yyyy_mm_dd(self):
        date, _ = ms_timestamp_to_date_time("1700000000000")

        parts = date.split("-")
        assert len(parts) == 3
        assert len(parts[0]) == 4  # year
        assert len(parts[1]) == 2  # month
        assert len(parts[2]) == 2  # day

    def test_time_format_is_hh_mm_ss(self):
        _, time = ms_timestamp_to_date_time("1700000000000")

        parts = time.split(":")
        assert len(parts) == 3
        assert all(len(p) == 2 for p in parts)

    def test_second_timestamp_date(self):
        # 1700003600000 = 1700000000000 + 1 hour
        date, _ = ms_timestamp_to_date_time("1700003600000")

        assert date == "2023-11-14"

    def test_second_timestamp_time(self):
        _, time = ms_timestamp_to_date_time("1700003600000")

        assert time == "23:13:20"

    def test_midnight_rollover_date(self):
        # 1700007200000 = 1700000000000 + 2 hours  →  rolls over to next day
        date, _ = ms_timestamp_to_date_time("1700007200000")

        assert date == "2023-11-15"

    def test_midnight_rollover_time(self):
        _, time = ms_timestamp_to_date_time("1700007200000")

        assert time == "00:13:20"

    def test_accepts_integer_input(self):
        date, time = ms_timestamp_to_date_time(1700000000000)

        assert date == "2023-11-14"
        assert time == "22:13:20"

    def test_empty_string_returns_empty_pair(self):
        date, time = ms_timestamp_to_date_time("")

        assert date == ""
        assert time == ""

    def test_zero_string_returns_empty_pair(self):
        date, time = ms_timestamp_to_date_time("0")

        assert date == ""
        assert time == ""

    def test_zero_int_returns_empty_pair(self):
        date, time = ms_timestamp_to_date_time(0)

        assert date == ""
        assert time == ""


# ===========================================================================
# TradeHistoryService tests
# ===========================================================================

class TestTradeHistoryService:

    # -----------------------------------------------------------------------
    # Return types and structure
    # -----------------------------------------------------------------------

    def test_returns_trade_history_dataclass(self):
        # Arrange
        client = StubTradeClient(trades=SAMPLE_TRADES)
        service = TradeHistoryService(client=client)

        # Act
        result = service.get_history("ZECUSDT")

        # Assert
        assert isinstance(result, TradeHistory)

    def test_trades_are_trade_instances(self):
        # Arrange
        client = StubTradeClient(trades=SAMPLE_TRADES)
        service = TradeHistoryService(client=client)

        # Act
        result = service.get_history("ZECUSDT")

        # Assert
        assert all(isinstance(t, Trade) for t in result.trades)

    def test_symbol_is_uppercased(self):
        # Arrange
        client = StubTradeClient(trades=SAMPLE_TRADES)
        service = TradeHistoryService(client=client)

        # Act
        result = service.get_history("zecusdt")

        # Assert
        assert result.symbol == "ZECUSDT"

    def test_symbol_uppercased_before_passing_to_client(self):
        # Arrange
        client = StubTradeClient(trades=SAMPLE_TRADES)
        service = TradeHistoryService(client=client)

        # Act
        service.get_history("zecusdt")

        # Assert
        assert client.last_symbol == "ZECUSDT"

    def test_category_passed_to_client(self):
        # Arrange
        client = StubTradeClient(trades=SAMPLE_TRADES)
        service = TradeHistoryService(client=client, category="inverse")

        # Act
        service.get_history("ZECUSDT")

        # Assert
        assert client.last_category == "inverse"

    def test_category_preserved_in_result(self):
        # Arrange
        client = StubTradeClient(trades=SAMPLE_TRADES)
        service = TradeHistoryService(client=client, category="inverse")

        # Act
        result = service.get_history("ZECUSDT")

        # Assert
        assert result.category == "inverse"

    def test_limit_passed_to_client(self):
        # Arrange
        client = StubTradeClient(trades=SAMPLE_TRADES)
        service = TradeHistoryService(client=client, limit=50)

        # Act
        service.get_history("ZECUSDT")

        # Assert
        assert client.last_limit == 50

    def test_correct_number_of_trades_returned(self):
        # Arrange — SAMPLE_TRADES has 3 entries
        client = StubTradeClient(trades=SAMPLE_TRADES)
        service = TradeHistoryService(client=client)

        # Act
        result = service.get_history("ZECUSDT")

        # Assert
        assert len(result.trades) == 3

    # -----------------------------------------------------------------------
    # Field mapping
    # -----------------------------------------------------------------------

    def test_trade_id_mapped_from_exec_id(self):
        # Arrange
        client = StubTradeClient(trades=SAMPLE_TRADES)
        service = TradeHistoryService(client=client)

        # Act
        result = service.get_history("ZECUSDT")

        # Assert
        assert result.trades[0].trade_id == "exec-001"

    def test_side_mapped(self):
        # Arrange
        client = StubTradeClient(trades=SAMPLE_TRADES)
        service = TradeHistoryService(client=client)

        # Act
        result = service.get_history("ZECUSDT")

        # Assert
        assert result.trades[0].side == "Buy"
        assert result.trades[1].side == "Sell"

    def test_price_mapped_from_exec_price_as_float(self):
        # Arrange
        client = StubTradeClient(trades=SAMPLE_TRADES)
        service = TradeHistoryService(client=client)

        # Act
        result = service.get_history("ZECUSDT")

        # Assert
        assert isinstance(result.trades[0].price, float)
        assert result.trades[0].price == pytest.approx(30.50)

    def test_size_mapped_from_exec_qty_as_float(self):
        # Arrange
        client = StubTradeClient(trades=SAMPLE_TRADES)
        service = TradeHistoryService(client=client)

        # Act
        result = service.get_history("ZECUSDT")

        # Assert
        assert isinstance(result.trades[0].size, float)
        assert result.trades[0].size == pytest.approx(10.0)

    def test_date_converted_from_exec_time(self):
        # Arrange
        client = StubTradeClient(trades=SAMPLE_TRADES)
        service = TradeHistoryService(client=client)

        # Act
        result = service.get_history("ZECUSDT")

        # Assert
        assert result.trades[0].date == "2023-11-14"

    def test_time_converted_from_exec_time(self):
        # Arrange
        client = StubTradeClient(trades=SAMPLE_TRADES)
        service = TradeHistoryService(client=client)

        # Act
        result = service.get_history("ZECUSDT")

        # Assert
        assert result.trades[0].time == "22:13:20"

    def test_date_rolls_over_correctly_at_midnight(self):
        # 1700007200000 is 2 hours after 1700000000000, crossing into the next day
        client = StubTradeClient(trades=SAMPLE_TRADES)
        service = TradeHistoryService(client=client)

        result = service.get_history("ZECUSDT")

        assert result.trades[2].date == "2023-11-15"
        assert result.trades[2].time == "00:13:20"

    def test_original_order_preserved(self):
        # Arrange — API returns newest-first; service must not reorder
        client = StubTradeClient(trades=SAMPLE_TRADES)
        service = TradeHistoryService(client=client)

        # Act
        result = service.get_history("ZECUSDT")

        # Assert
        ids = [t.trade_id for t in result.trades]
        assert ids == ["exec-001", "exec-002", "exec-003"]

    def test_missing_exec_time_gives_empty_date_and_time(self):
        # Arrange — execId and execTime absent
        minimal = [{"side": "Buy", "execPrice": "30.0", "execQty": "1"}]
        client = StubTradeClient(trades=minimal)
        service = TradeHistoryService(client=client)

        # Act
        result = service.get_history("ZECUSDT")

        # Assert
        t = result.trades[0]
        assert t.trade_id == ""
        assert t.date == ""
        assert t.time == ""
        assert t.price == pytest.approx(30.0)

    # -----------------------------------------------------------------------
    # Empty response
    # -----------------------------------------------------------------------

    def test_empty_response_returns_empty_trades_list(self):
        # Arrange
        client = StubTradeClient(trades=[])
        service = TradeHistoryService(client=client)

        # Act
        result = service.get_history("ZECUSDT")

        # Assert
        assert result.trades == []

    def test_empty_response_still_returns_trade_history(self):
        # Arrange
        client = StubTradeClient(trades=[])
        service = TradeHistoryService(client=client)

        # Act
        result = service.get_history("ZECUSDT")

        # Assert
        assert isinstance(result, TradeHistory)
        assert result.symbol == "ZECUSDT"

    # -----------------------------------------------------------------------
    # Error handling
    # -----------------------------------------------------------------------

    def test_api_error_is_propagated(self):
        # Arrange
        client = StubTradeClient(raise_error=True)
        service = TradeHistoryService(client=client)

        # Act / Assert
        with pytest.raises(BybitAPIError):
            service.get_history("ZECUSDT")

    def test_api_error_message_is_preserved(self):
        # Arrange
        client = StubTradeClient(raise_error=True)
        service = TradeHistoryService(client=client)

        # Act / Assert
        with pytest.raises(BybitAPIError, match="10003"):
            service.get_history("ZECUSDT")


# ===========================================================================
# TradeHistoryExporter tests
# ===========================================================================

def _make_history(*trades: tuple) -> TradeHistory:
    """
    Helper: build a TradeHistory from
    (trade_id, symbol, side, price, size, date, time) tuples.
    """
    return TradeHistory(
        symbol="ZECUSDT",
        category="linear",
        trades=[
            Trade(trade_id=tid, symbol=sym, side=sd, price=p, size=sz, date=d, time=t)
            for tid, sym, sd, p, sz, d, t in trades
        ],
    )


SAMPLE_HISTORY = _make_history(
    ("exec-001", "ZECUSDT", "Buy",  30.50, 10.0, "2023-11-14", "22:13:20"),
    ("exec-002", "ZECUSDT", "Sell", 31.00,  5.0, "2023-11-14", "23:13:20"),
    ("exec-003", "ZECUSDT", "Buy",  29.75, 20.0, "2023-11-15", "00:13:20"),
)


class TestTradeHistoryExporter:

    def test_file_is_created(self, tmp_path):
        # Arrange
        exporter = TradeHistoryExporter(tmp_path / "ZECUSDT_tradeHistory.csv")

        # Act
        exporter.export(SAMPLE_HISTORY)

        # Assert
        assert (tmp_path / "ZECUSDT_tradeHistory.csv").exists()

    def test_data_directory_created_automatically(self, tmp_path):
        # Arrange — data/ sub-dir does not yet exist
        output = tmp_path / "data" / "ZECUSDT_tradeHistory.csv"
        exporter = TradeHistoryExporter(output)

        # Act
        exporter.export(SAMPLE_HISTORY)

        # Assert
        assert output.parent.is_dir()

    def test_file_is_overwritten_not_appended(self, tmp_path):
        # Arrange — export 3 trades, then re-export 1 trade
        output = tmp_path / "out.csv"
        exporter = TradeHistoryExporter(output)
        exporter.export(SAMPLE_HISTORY)
        single = _make_history(("exec-001", "ZECUSDT", "Buy", 30.5, 10.0, "2023-11-14", "22:13:20"))

        # Act
        exporter.export(single)

        # Assert — 1 data row, not 3
        rows = read_csv(output)
        assert len(rows) == 2  # header + 1 trade

    def test_correct_headers_are_written(self, tmp_path):
        # Arrange
        output = tmp_path / "out.csv"
        exporter = TradeHistoryExporter(output)

        # Act
        exporter.export(SAMPLE_HISTORY)

        # Assert
        rows = read_csv(output)
        assert rows[0] == HEADERS
        assert rows[0] == ["trade_id", "symbol", "side", "price", "size", "date", "time"]

    def test_no_timestamp_column_in_headers(self, tmp_path):
        # Arrange
        output = tmp_path / "out.csv"
        exporter = TradeHistoryExporter(output)

        # Act
        exporter.export(SAMPLE_HISTORY)

        # Assert
        rows = read_csv(output)
        assert "timestamp" not in rows[0]

    def test_correct_number_of_rows(self, tmp_path):
        # Arrange — SAMPLE_HISTORY has 3 trades
        output = tmp_path / "out.csv"
        exporter = TradeHistoryExporter(output)

        # Act
        exporter.export(SAMPLE_HISTORY)

        # Assert — 1 header + 3 data rows
        rows = read_csv(output)
        assert len(rows) == 4

    def test_correct_values_first_row(self, tmp_path):
        # Arrange
        output = tmp_path / "out.csv"
        exporter = TradeHistoryExporter(output)

        # Act
        exporter.export(SAMPLE_HISTORY)

        # Assert
        rows = read_csv(output)
        assert rows[1] == ["exec-001", "ZECUSDT", "Buy", "30.5", "10.0", "2023-11-14", "22:13:20"]

    def test_correct_values_second_row(self, tmp_path):
        # Arrange
        output = tmp_path / "out.csv"
        exporter = TradeHistoryExporter(output)

        # Act
        exporter.export(SAMPLE_HISTORY)

        # Assert
        rows = read_csv(output)
        assert rows[2] == ["exec-002", "ZECUSDT", "Sell", "31.0", "5.0", "2023-11-14", "23:13:20"]

    def test_date_and_time_are_separate_columns(self, tmp_path):
        # Arrange
        output = tmp_path / "out.csv"
        exporter = TradeHistoryExporter(output)

        # Act
        exporter.export(SAMPLE_HISTORY)

        # Assert — 7 columns total
        rows = read_csv(output)
        assert len(rows[0]) == 7
        assert len(rows[1]) == 7

    def test_empty_history_writes_header_only(self, tmp_path):
        # Arrange
        output = tmp_path / "out.csv"
        exporter = TradeHistoryExporter(output)
        empty = _make_history()  # no trades

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
        assert exporter._output_path == tmp_path / "ZECUSDT_tradeHistory.csv"

    def test_make_exporter_uppercases_symbol(self, tmp_path):
        # Arrange / Act
        exporter = make_exporter("zecusdt", output_dir=tmp_path)

        # Assert
        assert exporter._output_path.name == "ZECUSDT_tradeHistory.csv"

    def test_make_exporter_default_dir_is_data(self):
        # Arrange / Act
        exporter = make_exporter("ZECUSDT")

        # Assert
        assert exporter._output_path == pathlib.Path("data") / "ZECUSDT_tradeHistory.csv"
