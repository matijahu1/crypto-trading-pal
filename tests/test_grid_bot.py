"""
tests/test_grid_bot.py — unit tests for GridBotService, GridBotExporter,
and the helper functions in services/grid_bot.py.

No real HTTP calls are made.  The API client is replaced by plain stubs.
Each test follows arrange → act → assert.

Coverage areas:
  GridBotService.get_snapshot()
    - Field mapping from list endpoint
    - Decimal precision for all financial fields
    - grid_num mapped as int
    - Detail enrichment (unrealized_pnl, fill quantities, etc.)
    - Detail failure is best-effort: snapshot still written with defaults
    - Empty bot list returns empty snapshot
    - fetch_details=False skips detail calls
    - API error on list endpoint propagates
    - Symbol is uppercased before use
    - created_time conversion from ms timestamp

  GridBotExporter
    - File created at output path
    - Parent directory created automatically
    - Correct headers written (15 columns)
    - Correct column count in data rows
    - Correct values in a data row (spot-check key columns)
    - Empty snapshot writes headers only
    - File is overwritten on second export (not appended)
    - make_exporter builds correct filename and uppercases symbol
"""

from __future__ import annotations

import csv
import pathlib
from decimal import Decimal

import pytest

from api.bybit_client import BybitAPIError
from services.grid_bot import (
    GridBot,
    GridBotService,
    GridBotSnapshot,
    _ms_to_utc_string,
    _to_decimal,
    _to_int,
)
from exporters.grid_bot_exporter import (
    GridBotExporter,
    make_exporter,
    HEADERS,
)

# ---------------------------------------------------------------------------
# Stub clients
# ---------------------------------------------------------------------------


class StubGridBotClient:
    """
    Configurable stub for GridBotClientProtocol.

    - list_response:   returned by get_grid_bots()
    - detail_response: returned by get_grid_bot_detail()
    - raise_list:      if True, get_grid_bots() raises BybitAPIError
    - raise_detail:    if True, get_grid_bot_detail() raises BybitAPIError
    """

    def __init__(
        self,
        list_response: list[dict] | None = None,
        detail_response: dict | None = None,
        raise_list: bool = False,
        raise_detail: bool = False,
    ) -> None:
        self._list    = list_response or []
        self._detail  = detail_response or {}
        self._raise_list   = raise_list
        self._raise_detail = raise_detail

        self.list_calls:   list[dict] = []
        self.detail_calls: list[str]  = []

    def get_grid_bots(
        self,
        symbol: str,
        category: str,
        limit: int,
    ) -> list[dict]:
        self.list_calls.append({"symbol": symbol, "category": category, "limit": limit})
        if self._raise_list:
            raise BybitAPIError("Bybit API error [10003]: list failed")
        return self._list

    def get_grid_bot_detail(self, bot_id: str) -> dict:
        self.detail_calls.append(bot_id)
        if self._raise_detail:
            raise BybitAPIError("Bybit API error [10003]: detail failed")
        return self._detail


# ---------------------------------------------------------------------------
# Test fixtures / factories
# ---------------------------------------------------------------------------

# A realistic raw list-endpoint record
RAW_BOT: dict = {
    "botId":       "bot-001",
    "symbol":      "ICPUSDT",
    "status":      "Running",
    "direction":   "neutral",
    "upperPrice":  "12.50",
    "lowerPrice":  "8.00",
    "gridNum":     "20",
    "leverage":    "3",
    "investment":  "150.00",
    "gridProfit":  "4.25",
    "createdTime": "1700000000000",  # 2023-11-14 22:13:20 UTC
}

# A realistic raw detail-endpoint record for the same bot
RAW_DETAIL: dict = {
    "botId":         "bot-001",
    "unrealizedPnl": "1.80",
    "totalInvestment": "155.00",
    "filledOpenQty":   "12",
    "filledCloseQty":  "10",
    "investment":      "155.00",
}


def _make_snapshot(*bots: GridBot, symbol: str = "ICPUSDT") -> GridBotSnapshot:
    return GridBotSnapshot(symbol=symbol, category="future", bots=list(bots))


def _make_bot(**kwargs) -> GridBot:
    """Build a GridBot with sensible defaults; override via kwargs."""
    defaults = dict(
        bot_id="bot-001",
        symbol="ICPUSDT",
        status="Running",
        direction="neutral",
        upper_price=Decimal("12.50"),
        lower_price=Decimal("8.00"),
        grid_num=20,
        leverage=Decimal("3"),
        investment=Decimal("150.00"),
        grid_profit=Decimal("4.25"),
        unrealized_pnl=Decimal("1.80"),
        total_investment=Decimal("155.00"),
        filled_open_qty=Decimal("12"),
        filled_close_qty=Decimal("10"),
        created_time="2023-11-14 22:13:20",
    )
    defaults.update(kwargs)
    return GridBot(**defaults)


SAMPLE_SNAPSHOT = _make_snapshot(_make_bot())


def read_csv(path: pathlib.Path) -> list[list[str]]:
    return list(csv.reader(path.read_text(encoding="utf-8").splitlines()))


# ===========================================================================
# Helper function tests
# ===========================================================================


class TestToDecimal:
    def test_string_value(self):
        assert _to_decimal("12.50") == Decimal("12.50")

    def test_integer_string(self):
        assert _to_decimal("3") == Decimal("3")

    def test_negative_string(self):
        assert _to_decimal("-4.25") == Decimal("-4.25")

    def test_none_returns_zero(self):
        assert _to_decimal(None) == Decimal("0")

    def test_empty_string_returns_zero(self):
        assert _to_decimal("") == Decimal("0")

    def test_invalid_string_returns_zero(self):
        assert _to_decimal("not-a-number") == Decimal("0")

    def test_returns_decimal_type(self):
        assert isinstance(_to_decimal("5.00"), Decimal)


class TestToInt:
    def test_string_integer(self):
        assert _to_int("20") == 20

    def test_numeric_integer(self):
        assert _to_int(20) == 20

    def test_none_returns_zero(self):
        assert _to_int(None) == 0

    def test_invalid_returns_zero(self):
        assert _to_int("abc") == 0


class TestMsToUtcString:
    def test_known_timestamp(self):
        # 1700000000000 ms → 2023-11-14 22:13:20 UTC
        assert _ms_to_utc_string("1700000000000") == "2023-11-14 22:13:20"

    def test_integer_input(self):
        assert _ms_to_utc_string(1700000000000) == "2023-11-14 22:13:20"

    def test_none_returns_empty(self):
        assert _ms_to_utc_string(None) == ""

    def test_zero_returns_empty(self):
        assert _ms_to_utc_string(0) == ""

    def test_zero_string_returns_empty(self):
        assert _ms_to_utc_string("0") == ""

    def test_invalid_returns_empty(self):
        assert _ms_to_utc_string("not-a-timestamp") == ""

    def test_format_is_yyyy_mm_dd_hh_mm_ss(self):
        result = _ms_to_utc_string("1700000000000")
        parts = result.split(" ")
        assert len(parts) == 2
        date_parts = parts[0].split("-")
        time_parts = parts[1].split(":")
        assert len(date_parts) == 3 and len(time_parts) == 3


# ===========================================================================
# GridBotService tests
# ===========================================================================


class TestGridBotServiceFieldMapping:
    """Verify that raw API dicts are correctly mapped to GridBot dataclass fields."""

    def _get_result(self, raw_bot=None, raw_detail=None, fetch_details=True):
        client = StubGridBotClient(
            list_response=[raw_bot or RAW_BOT],
            detail_response=raw_detail or RAW_DETAIL,
        )
        return GridBotService(client).get_snapshot("ICPUSDT", fetch_details=fetch_details)

    def test_returns_grid_bot_snapshot(self):
        assert isinstance(self._get_result(), GridBotSnapshot)

    def test_bots_are_grid_bot_instances(self):
        snap = self._get_result()
        assert all(isinstance(b, GridBot) for b in snap.bots)

    def test_bot_id_mapped(self):
        snap = self._get_result()
        assert snap.bots[0].bot_id == "bot-001"

    def test_symbol_mapped(self):
        snap = self._get_result()
        assert snap.bots[0].symbol == "ICPUSDT"

    def test_status_mapped(self):
        snap = self._get_result()
        assert snap.bots[0].status == "Running"

    def test_direction_mapped(self):
        snap = self._get_result()
        assert snap.bots[0].direction == "neutral"

    def test_upper_price_is_decimal(self):
        snap = self._get_result()
        assert isinstance(snap.bots[0].upper_price, Decimal)
        assert snap.bots[0].upper_price == Decimal("12.50")

    def test_lower_price_is_decimal(self):
        snap = self._get_result()
        assert isinstance(snap.bots[0].lower_price, Decimal)
        assert snap.bots[0].lower_price == Decimal("8.00")

    def test_grid_num_is_int(self):
        snap = self._get_result()
        assert isinstance(snap.bots[0].grid_num, int)
        assert snap.bots[0].grid_num == 20

    def test_leverage_is_decimal(self):
        snap = self._get_result()
        assert isinstance(snap.bots[0].leverage, Decimal)
        assert snap.bots[0].leverage == Decimal("3")

    def test_investment_is_decimal(self):
        snap = self._get_result()
        assert isinstance(snap.bots[0].investment, Decimal)

    def test_grid_profit_is_decimal(self):
        snap = self._get_result()
        assert isinstance(snap.bots[0].grid_profit, Decimal)
        assert snap.bots[0].grid_profit == Decimal("4.25")

    def test_created_time_converted(self):
        snap = self._get_result()
        assert snap.bots[0].created_time == "2023-11-14 22:13:20"

    def test_created_time_missing_gives_empty_string(self):
        raw = {**RAW_BOT}
        raw.pop("createdTime", None)
        snap = self._get_result(raw_bot=raw)
        assert snap.bots[0].created_time == ""


class TestGridBotServiceDetailEnrichment:
    """Verify that detail-endpoint data is merged into the GridBot correctly."""

    def _enriched_bot(self):
        client = StubGridBotClient(
            list_response=[RAW_BOT],
            detail_response=RAW_DETAIL,
        )
        return GridBotService(client).get_snapshot("ICPUSDT").bots[0]

    def test_unrealized_pnl_enriched(self):
        bot = self._enriched_bot()
        assert isinstance(bot.unrealized_pnl, Decimal)
        assert bot.unrealized_pnl == Decimal("1.80")

    def test_total_investment_enriched(self):
        bot = self._enriched_bot()
        assert bot.total_investment == Decimal("155.00")

    def test_filled_open_qty_enriched(self):
        bot = self._enriched_bot()
        assert bot.filled_open_qty == Decimal("12")

    def test_filled_close_qty_enriched(self):
        bot = self._enriched_bot()
        assert bot.filled_close_qty == Decimal("10")

    def test_investment_overridden_by_detail(self):
        # detail has investment "155.00" vs list "150.00"
        bot = self._enriched_bot()
        assert bot.investment == Decimal("155.00")

    def test_detail_called_once_per_bot(self):
        client = StubGridBotClient(
            list_response=[RAW_BOT, {**RAW_BOT, "botId": "bot-002"}],
            detail_response=RAW_DETAIL,
        )
        GridBotService(client).get_snapshot("ICPUSDT")
        assert len(client.detail_calls) == 2

    def test_fetch_details_false_skips_detail_calls(self):
        client = StubGridBotClient(list_response=[RAW_BOT])
        GridBotService(client).get_snapshot("ICPUSDT", fetch_details=False)
        assert client.detail_calls == []

    def test_fetch_details_false_leaves_detail_fields_at_zero(self):
        client = StubGridBotClient(list_response=[RAW_BOT])
        snap = GridBotService(client).get_snapshot("ICPUSDT", fetch_details=False)
        bot = snap.bots[0]
        assert bot.unrealized_pnl == Decimal("0")
        assert bot.total_investment == Decimal("0")

    def test_detail_failure_does_not_abort_snapshot(self):
        # Detail raises, but the bot should still appear in the snapshot
        client = StubGridBotClient(
            list_response=[RAW_BOT],
            raise_detail=True,
        )
        snap = GridBotService(client).get_snapshot("ICPUSDT")
        assert len(snap.bots) == 1

    def test_detail_failure_leaves_detail_fields_at_defaults(self):
        client = StubGridBotClient(
            list_response=[RAW_BOT],
            raise_detail=True,
        )
        snap = GridBotService(client).get_snapshot("ICPUSDT")
        bot = snap.bots[0]
        assert bot.unrealized_pnl == Decimal("0")
        assert bot.filled_open_qty == Decimal("0")


class TestGridBotServiceBehaviours:
    """Behavioural and edge-case tests for GridBotService."""

    def test_symbol_uppercased(self):
        client = StubGridBotClient(list_response=[RAW_BOT])
        snap = GridBotService(client).get_snapshot("icpusdt")
        assert snap.symbol == "ICPUSDT"

    def test_symbol_uppercased_before_passing_to_client(self):
        client = StubGridBotClient(list_response=[RAW_BOT])
        GridBotService(client).get_snapshot("icpusdt")
        assert client.list_calls[0]["symbol"] == "ICPUSDT"

    def test_empty_list_returns_empty_snapshot(self):
        client = StubGridBotClient(list_response=[])
        snap = GridBotService(client).get_snapshot("ICPUSDT")
        assert snap.bots == []

    def test_empty_list_returns_grid_bot_snapshot(self):
        client = StubGridBotClient(list_response=[])
        snap = GridBotService(client).get_snapshot("ICPUSDT")
        assert isinstance(snap, GridBotSnapshot)

    def test_multiple_bots_all_returned(self):
        client = StubGridBotClient(
            list_response=[
                RAW_BOT,
                {**RAW_BOT, "botId": "bot-002"},
                {**RAW_BOT, "botId": "bot-003"},
            ],
            detail_response=RAW_DETAIL,
        )
        snap = GridBotService(client).get_snapshot("ICPUSDT")
        assert len(snap.bots) == 3

    def test_list_api_error_propagates(self):
        client = StubGridBotClient(raise_list=True)
        with pytest.raises(BybitAPIError):
            GridBotService(client).get_snapshot("ICPUSDT")

    def test_list_api_error_message_preserved(self):
        client = StubGridBotClient(raise_list=True)
        with pytest.raises(BybitAPIError, match="10003"):
            GridBotService(client).get_snapshot("ICPUSDT")

    def test_category_passed_to_client(self):
        client = StubGridBotClient(list_response=[])
        GridBotService(client, category="future").get_snapshot("ICPUSDT")
        assert client.list_calls[0]["category"] == "future"

    def test_category_stored_in_snapshot(self):
        client = StubGridBotClient(list_response=[])
        snap = GridBotService(client, category="future").get_snapshot("ICPUSDT")
        assert snap.category == "future"

    def test_missing_optional_fields_default_gracefully(self):
        # A minimal raw record with only botId and symbol
        minimal = {"botId": "x", "symbol": "ICPUSDT"}
        client = StubGridBotClient(list_response=[minimal])
        snap = GridBotService(client).get_snapshot("ICPUSDT", fetch_details=False)
        bot = snap.bots[0]
        assert bot.upper_price == Decimal("0")
        assert bot.grid_num == 0
        assert bot.created_time == ""


# ===========================================================================
# GridBotExporter tests
# ===========================================================================


class TestGridBotExporter:

    def test_file_is_created(self, tmp_path):
        GridBotExporter(tmp_path / "out.csv").export(SAMPLE_SNAPSHOT)
        assert (tmp_path / "out.csv").exists()

    def test_parent_directory_created_automatically(self, tmp_path):
        output = tmp_path / "deep" / "nested" / "out.csv"
        GridBotExporter(output).export(SAMPLE_SNAPSHOT)
        assert output.parent.is_dir()

    def test_correct_headers_written(self, tmp_path):
        output = tmp_path / "out.csv"
        GridBotExporter(output).export(SAMPLE_SNAPSHOT)
        rows = read_csv(output)
        assert rows[0] == HEADERS

    def test_headers_contain_all_required_columns(self, tmp_path):
        output = tmp_path / "out.csv"
        GridBotExporter(output).export(SAMPLE_SNAPSHOT)
        rows = read_csv(output)
        for col in [
            "bot_id", "symbol", "status", "direction",
            "upper_price", "lower_price", "grid_num", "leverage",
            "investment", "total_investment", "grid_profit",
            "unrealized_pnl", "filled_open_qty", "filled_close_qty",
            "created_time",
        ]:
            assert col in rows[0], f"Missing column: {col}"

    def test_column_count_is_fifteen(self, tmp_path):
        output = tmp_path / "out.csv"
        GridBotExporter(output).export(SAMPLE_SNAPSHOT)
        rows = read_csv(output)
        assert len(rows[0]) == 15
        assert len(rows[1]) == 15

    def test_correct_number_of_data_rows(self, tmp_path):
        output = tmp_path / "out.csv"
        GridBotExporter(output).export(SAMPLE_SNAPSHOT)
        rows = read_csv(output)
        assert len(rows) == 2  # header + 1 bot

    def test_bot_id_written_correctly(self, tmp_path):
        output = tmp_path / "out.csv"
        GridBotExporter(output).export(SAMPLE_SNAPSHOT)
        rows = read_csv(output)
        assert rows[1][HEADERS.index("bot_id")] == "bot-001"

    def test_symbol_written_correctly(self, tmp_path):
        output = tmp_path / "out.csv"
        GridBotExporter(output).export(SAMPLE_SNAPSHOT)
        rows = read_csv(output)
        assert rows[1][HEADERS.index("symbol")] == "ICPUSDT"

    def test_upper_price_written_as_decimal_string(self, tmp_path):
        output = tmp_path / "out.csv"
        GridBotExporter(output).export(SAMPLE_SNAPSHOT)
        rows = read_csv(output)
        assert rows[1][HEADERS.index("upper_price")] == "12.50"

    def test_grid_num_written_as_integer_string(self, tmp_path):
        output = tmp_path / "out.csv"
        GridBotExporter(output).export(SAMPLE_SNAPSHOT)
        rows = read_csv(output)
        assert rows[1][HEADERS.index("grid_num")] == "20"

    def test_grid_profit_written_correctly(self, tmp_path):
        output = tmp_path / "out.csv"
        GridBotExporter(output).export(SAMPLE_SNAPSHOT)
        rows = read_csv(output)
        assert rows[1][HEADERS.index("grid_profit")] == "4.25"

    def test_unrealized_pnl_written_correctly(self, tmp_path):
        output = tmp_path / "out.csv"
        GridBotExporter(output).export(SAMPLE_SNAPSHOT)
        rows = read_csv(output)
        assert rows[1][HEADERS.index("unrealized_pnl")] == "1.80"

    def test_created_time_written_correctly(self, tmp_path):
        output = tmp_path / "out.csv"
        GridBotExporter(output).export(SAMPLE_SNAPSHOT)
        rows = read_csv(output)
        assert rows[1][HEADERS.index("created_time")] == "2023-11-14 22:13:20"

    def test_empty_snapshot_writes_headers_only(self, tmp_path):
        output = tmp_path / "out.csv"
        GridBotExporter(output).export(_make_snapshot())
        rows = read_csv(output)
        assert rows == [HEADERS]

    def test_file_is_overwritten_not_appended(self, tmp_path):
        output = tmp_path / "out.csv"
        exp = GridBotExporter(output)
        exp.export(SAMPLE_SNAPSHOT)                    # 1 bot
        exp.export(_make_snapshot(_make_bot(), _make_bot(bot_id="bot-002")))  # 2 bots
        rows = read_csv(output)
        assert len(rows) == 3  # header + 2 bots (not 4)

    def test_multiple_bots_all_written(self, tmp_path):
        output = tmp_path / "out.csv"
        snap = _make_snapshot(
            _make_bot(bot_id="bot-001"),
            _make_bot(bot_id="bot-002"),
            _make_bot(bot_id="bot-003"),
        )
        GridBotExporter(output).export(snap)
        rows = read_csv(output)
        assert len(rows) == 4  # header + 3

    def test_make_exporter_builds_correct_filename(self, tmp_path):
        assert make_exporter("ICPUSDT", output_dir=tmp_path)._output_path == \
               tmp_path / "ICPUSDT_gridBots.csv"

    def test_make_exporter_uppercases_symbol(self, tmp_path):
        assert make_exporter("icpusdt", output_dir=tmp_path)._output_path.name == \
               "ICPUSDT_gridBots.csv"

    def test_make_exporter_default_dir_is_data(self):
        assert make_exporter("ICPUSDT")._output_path == \
               pathlib.Path("data") / "ICPUSDT_gridBots.csv"
