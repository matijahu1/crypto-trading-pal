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
from services.trade_history import (
    TradeHistoryService, TradeHistory, Trade,
    LOOKBACK_DAYS, MAX_WINDOW_DAYS, _MS_PER_DAY,
)
from exporters.trade_history_exporter import (
    TradeHistoryExporter,
    make_exporter,
    HEADERS,
)
from api.bybit_client import BybitAPIError


# ---------------------------------------------------------------------------
# Stub clients
# ---------------------------------------------------------------------------

class StubTradeClient:
    """
    Simple stub: returns the same list on the first call, [] on all subsequent
    calls (so the inner page-loop terminates).  Records call arguments.
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
        self.last_start_time: int | None = None
        self.last_end_time: int | None = None
        self.call_count: int = 0

    def get_trade_history(
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
        return self._trades if self.call_count == 1 else []


class SequentialStubClient:
    """
    Returns pages[0] on call 1, pages[1] on call 2, [] once exhausted.
    Records every (start_time, end_time) pair in call_log.
    """

    def __init__(self, pages: list[list[dict]]) -> None:
        self._pages = list(pages)
        self._idx   = 0
        self.call_log: list[tuple[int | None, int | None]] = []

    def get_trade_history(
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
# Helpers
# ---------------------------------------------------------------------------

NOW_FIXED    = 1_700_000_000_000   # 2023-11-14 22:13:20 UTC
WINDOW_MS    = MAX_WINDOW_DAYS * _MS_PER_DAY
GLOBAL_START = NOW_FIXED - LOOKBACK_DAYS * _MS_PER_DAY

# Exact window boundaries produced by the loop (derived from trace run):
W1_START = 1_699_395_200_000;  W1_END = 1_700_000_000_000
W2_START = 1_698_790_399_999;  W2_END = 1_699_395_199_999
W3_START = 1_698_185_599_998;  W3_END = 1_698_790_399_998
W4_START = 1_697_580_799_997;  W4_END = 1_698_185_599_997
W5_START = 1_697_408_000_000;  W5_END = 1_697_580_799_996  # clamped


def raw(exec_id: str, exec_time: int, exec_type: str = "Trade") -> dict:
    """Minimal raw trade dict for tests."""
    return {
        "execId":    exec_id,
        "symbol":    "ZECUSDT",
        "side":      "Buy",
        "execPrice": "30.0",
        "execQty":   "1",
        "execType":  exec_type,
        "execTime":  str(exec_time),
    }


def read_csv(path: pathlib.Path) -> list[list[str]]:
    return list(csv.reader(path.read_text(encoding="utf-8").splitlines()))


# ===========================================================================
# ms_timestamp_to_date_time tests
# ===========================================================================

class TestMsTimestampToDateTime:

    def test_returns_tuple_of_two_strings(self):
        result = ms_timestamp_to_date_time("1700000000000")
        assert isinstance(result, tuple) and len(result) == 2

    def test_known_timestamp_date(self):
        date, _ = ms_timestamp_to_date_time("1700000000000")
        assert date == "2023-11-14"

    def test_known_timestamp_time(self):
        _, time = ms_timestamp_to_date_time("1700000000000")
        assert time == "22:13:20"

    def test_date_format_is_yyyy_mm_dd(self):
        date, _ = ms_timestamp_to_date_time("1700000000000")
        parts = date.split("-")
        assert len(parts) == 3 and len(parts[0]) == 4

    def test_time_format_is_hh_mm_ss(self):
        _, time = ms_timestamp_to_date_time("1700000000000")
        parts = time.split(":")
        assert len(parts) == 3 and all(len(p) == 2 for p in parts)

    def test_accepts_integer_input(self):
        date, time = ms_timestamp_to_date_time(1700000000000)
        assert date == "2023-11-14" and time == "22:13:20"

    def test_empty_string_returns_empty_pair(self):
        assert ms_timestamp_to_date_time("") == ("", "")

    def test_zero_string_returns_empty_pair(self):
        assert ms_timestamp_to_date_time("0") == ("", "")

    def test_zero_int_returns_empty_pair(self):
        assert ms_timestamp_to_date_time(0) == ("", "")


# ===========================================================================
# TradeHistoryService — field mapping
# ===========================================================================

class TestTradeHistoryService:
    """Field-level tests. StubTradeClient returns trades on call 1, [] on call 2+."""

    @pytest.fixture(autouse=True)
    def freeze_time(self, monkeypatch):
        import services.trade_history as m
        monkeypatch.setattr(m, "_now_ms", lambda: NOW_FIXED)

    TRADES = [
        raw("exec-001", W1_END - 1_000, "Trade"),
        raw("exec-002", W1_END - 2_000, "Trade"),
        raw("exec-003", W1_END - 3_000, "Funding"),
    ]

    def test_returns_trade_history_dataclass(self):
        result = TradeHistoryService(StubTradeClient(self.TRADES)).get_history("ZECUSDT")
        assert isinstance(result, TradeHistory)

    def test_trades_are_trade_instances(self):
        result = TradeHistoryService(StubTradeClient(self.TRADES)).get_history("ZECUSDT")
        assert all(isinstance(t, Trade) for t in result.trades)

    def test_symbol_is_uppercased(self):
        result = TradeHistoryService(StubTradeClient(self.TRADES)).get_history("zecusdt")
        assert result.symbol == "ZECUSDT"

    def test_symbol_uppercased_before_passing_to_client(self):
        client = StubTradeClient(self.TRADES)
        TradeHistoryService(client).get_history("zecusdt")
        assert client.last_symbol == "ZECUSDT"

    def test_category_passed_to_client(self):
        client = StubTradeClient(self.TRADES)
        TradeHistoryService(client, category="inverse").get_history("ZECUSDT")
        assert client.last_category == "inverse"

    def test_category_preserved_in_result(self):
        result = TradeHistoryService(StubTradeClient(self.TRADES), category="inverse").get_history("ZECUSDT")
        assert result.category == "inverse"

    def test_correct_number_of_trades_returned(self):
        result = TradeHistoryService(StubTradeClient(self.TRADES)).get_history("ZECUSDT")
        assert len(result.trades) == 3

    def test_trade_id_mapped_from_exec_id(self):
        result = TradeHistoryService(StubTradeClient(self.TRADES)).get_history("ZECUSDT")
        assert result.trades[0].trade_id == "exec-001"

    def test_side_mapped(self):
        result = TradeHistoryService(StubTradeClient(self.TRADES)).get_history("ZECUSDT")
        assert result.trades[0].side == "Buy"

    def test_price_mapped_as_float(self):
        result = TradeHistoryService(StubTradeClient(self.TRADES)).get_history("ZECUSDT")
        assert isinstance(result.trades[0].price, float)
        assert result.trades[0].price == pytest.approx(30.0)

    def test_size_mapped_as_float(self):
        result = TradeHistoryService(StubTradeClient(self.TRADES)).get_history("ZECUSDT")
        assert isinstance(result.trades[0].size, float)
        assert result.trades[0].size == pytest.approx(1.0)

    def test_exec_type_trade_mapped(self):
        result = TradeHistoryService(StubTradeClient(self.TRADES)).get_history("ZECUSDT")
        assert result.trades[0].exec_type == "Trade"

    def test_exec_type_funding_mapped(self):
        result = TradeHistoryService(StubTradeClient(self.TRADES)).get_history("ZECUSDT")
        assert result.trades[2].exec_type == "Funding"

    def test_exec_type_missing_defaults_to_empty_string(self):
        no_type = [{"execId": "x", "side": "Buy", "execPrice": "1",
                    "execQty": "1", "execTime": str(W1_END - 1_000)}]
        result = TradeHistoryService(StubTradeClient(no_type)).get_history("ZECUSDT")
        assert result.trades[0].exec_type == ""

    def test_date_converted_from_exec_time(self):
        result = TradeHistoryService(StubTradeClient(self.TRADES)).get_history("ZECUSDT")
        assert result.trades[0].date == "2023-11-14"

    def test_missing_exec_time_gives_empty_date_and_time(self):
        minimal = [{"side": "Buy", "execPrice": "30.0", "execQty": "1"}]
        result = TradeHistoryService(StubTradeClient(minimal)).get_history("ZECUSDT")
        t = result.trades[0]
        assert t.date == "" and t.time == ""

    def test_empty_response_returns_empty_trades_list(self):
        result = TradeHistoryService(StubTradeClient([])).get_history("ZECUSDT")
        assert result.trades == []

    def test_empty_response_still_returns_trade_history(self):
        result = TradeHistoryService(StubTradeClient([])).get_history("ZECUSDT")
        assert isinstance(result, TradeHistory)

    def test_api_error_is_propagated(self):
        with pytest.raises(BybitAPIError):
            TradeHistoryService(StubTradeClient(raise_error=True)).get_history("ZECUSDT")

    def test_api_error_message_is_preserved(self):
        with pytest.raises(BybitAPIError, match="10003"):
            TradeHistoryService(StubTradeClient(raise_error=True)).get_history("ZECUSDT")


# ===========================================================================
# TradeHistoryService — 7-day window iteration
# ===========================================================================

class TestTradeHistoryWindowIteration:
    """
    Tests for the outer-window loop logic.

    Reference (NOW_FIXED = 1_700_000_000_000):
      LOOKBACK = 30d, WINDOW = 7d → 5 windows (4 full + 1 partial)

    Exact window boundaries (derived from loop trace):
      W1: [1_699_395_200_000,  1_700_000_000_000]  7.00d
      W2: [1_698_790_399_999,  1_699_395_199_999]  7.00d
      W3: [1_698_185_599_998,  1_698_790_399_998]  7.00d
      W4: [1_697_580_799_997,  1_698_185_599_997]  7.00d
      W5: [1_697_408_000_000,  1_697_580_799_996]  2.00d  ← clamped
    """

    @pytest.fixture(autouse=True)
    def freeze_time(self, monkeypatch):
        import services.trade_history as m
        monkeypatch.setattr(m, "_now_ms", lambda: NOW_FIXED)

    # -----------------------------------------------------------------------
    # Window boundaries
    # -----------------------------------------------------------------------

    def test_first_window_end_is_now(self):
        # Arrange — one trade in W1, everything else returns []
        client = SequentialStubClient(pages=[
            [raw("t1", W1_END - 1_000)],  # W1 page
        ])
        TradeHistoryService(client).get_history("ZECUSDT")

        assert client.call_log[0][1] == NOW_FIXED

    def test_first_window_start_is_now_minus_7_days(self):
        client = SequentialStubClient(pages=[
            [raw("t1", W1_END - 1_000)],
        ])
        TradeHistoryService(client).get_history("ZECUSDT")

        assert client.call_log[0][0] == W1_START

    def test_second_window_end_is_w1_start_minus_one(self):
        # After W1 is fully paged through, W2 should start with end=W1_START-1.
        # We provide enough pages to cover W1's inner loop (page + empty) and W2.
        client = SequentialStubClient(pages=[
            [raw("t1", W1_END - 1_000)],  # W1 page 1 (inner loop fetches t1)
            [],                            # W1 page 2 (inner loop ends)
            [raw("t2", W2_END - 1_000)],  # W2 page 1
        ])
        TradeHistoryService(client).get_history("ZECUSDT")

        # The first W2 call must have end_time == W1_START - 1
        end_times = [e for _, e in client.call_log]
        assert (W1_START - 1) in end_times

    def test_last_window_start_is_clamped_to_global_start(self):
        # Feed 5 pages (one per window) so all windows are visited
        client = SequentialStubClient(pages=[
            [raw(f"t{i}", W1_END - i * 1_000)] for i in range(1, 6)
        ])
        TradeHistoryService(client).get_history("ZECUSDT")

        starts = [s for s, _ in client.call_log]
        assert GLOBAL_START in starts

    def test_window_span_does_not_exceed_7_days(self):
        # Every API call must cover <= 7 days
        client = SequentialStubClient(pages=[
            [raw(f"t{i}", W1_END - i * 1_000)] for i in range(1, 6)
        ])
        TradeHistoryService(client).get_history("ZECUSDT")

        for start, end in client.call_log:
            if start is not None and end is not None:
                assert (end - start) <= MAX_WINDOW_DAYS * _MS_PER_DAY

    # -----------------------------------------------------------------------
    # Empty windows do NOT stop the outer loop
    # -----------------------------------------------------------------------

    def test_empty_window_does_not_stop_outer_loop(self):
        # W1 is empty ([] on call 1), W2 has a trade (call 2)
        client = SequentialStubClient(pages=[
            [],                             # W1: empty → outer loop continues
            [raw("t2", W2_END - 1_000)],   # W2: has a trade
        ])
        result = TradeHistoryService(client).get_history("ZECUSDT")

        assert any(t.trade_id == "t2" for t in result.trades)

    def test_two_empty_windows_followed_by_one_with_trades(self):
        client = SequentialStubClient(pages=[
            [],                             # W1 empty
            [],                             # W2 empty
            [raw("t3", W3_END - 1_000)],   # W3 has trades
        ])
        result = TradeHistoryService(client).get_history("ZECUSDT")

        assert any(t.trade_id == "t3" for t in result.trades)

    def test_all_empty_windows_return_empty_history(self):
        client = SequentialStubClient(pages=[])
        result = TradeHistoryService(client).get_history("ZECUSDT")

        assert result.trades == []

    def test_all_empty_windows_make_at_most_5_calls(self):
        # 30d / 7d = 5 windows; each makes exactly 1 call when empty
        client = SequentialStubClient(pages=[])
        TradeHistoryService(client).get_history("ZECUSDT")

        assert len(client.call_log) <= 5

    # -----------------------------------------------------------------------
    # Data aggregation across windows
    # -----------------------------------------------------------------------

    def test_trades_from_two_windows_are_combined(self):
        client = SequentialStubClient(pages=[
            [raw("t1", W1_END - 1_000)],
            [raw("t2", W2_END - 1_000)],
        ])
        result = TradeHistoryService(client).get_history("ZECUSDT")

        assert len(result.trades) == 2
        assert {t.trade_id for t in result.trades} == {"t1", "t2"}

    def test_trades_from_all_five_windows_are_combined(self):
        client = SequentialStubClient(pages=[
            [raw(f"t{i}", W1_END - i * 1_000)] for i in range(1, 6)
        ])
        result = TradeHistoryService(client).get_history("ZECUSDT")

        assert len(result.trades) == 5

    def test_gap_in_trading_activity_handled(self):
        # W1 and W3 have trades; W2 is empty (trading gap) — all must be collected
        client = SequentialStubClient(pages=[
            [raw("t1", W1_END - 1_000)],   # W1
            [],                             # W2: gap in activity
            [raw("t3", W3_END - 1_000)],   # W3
        ])
        result = TradeHistoryService(client).get_history("ZECUSDT")

        ids = {t.trade_id for t in result.trades}
        assert "t1" in ids and "t3" in ids

    # -----------------------------------------------------------------------
    # Inner-loop paging within one window
    # -----------------------------------------------------------------------

    def test_multiple_pages_within_one_window_are_combined(self):
        # W1 has two pages: service should fetch both before moving to W2
        T1, T2, T3 = W1_END - 1_000, W1_END - 2_000, W1_END - 3_000
        client = SequentialStubClient(pages=[
            [raw("t1", T1), raw("t2", T2)],   # W1 page 1
            [raw("t3", T3)],                   # W1 page 2 (inner loop)
            # W2 and beyond return [] implicitly
        ])
        result = TradeHistoryService(client).get_history("ZECUSDT")

        assert len(result.trades) == 3

    def test_inner_page_end_time_advances_to_oldest_minus_one(self):
        T1, T2 = W1_END - 1_000, W1_END - 50_000
        client = SequentialStubClient(pages=[
            [raw("t1", T1), raw("t2", T2)],   # W1 page 1 — T2 is oldest
            [],                                # W1 page 2 — empty → window done
        ])
        TradeHistoryService(client).get_history("ZECUSDT")

        # Second call should have end_time = T2 - 1
        assert client.call_log[1][1] == T2 - 1

    # -----------------------------------------------------------------------
    # Deduplication across window boundaries
    # -----------------------------------------------------------------------

    def test_duplicate_trade_id_across_windows_appears_once(self):
        # "dup" appears in both W1 and W2 (boundary overlap)
        client = SequentialStubClient(pages=[
            [raw("dup", W1_END - 1_000), raw("t1", W1_END - 2_000)],
            [raw("dup", W1_END - 1_000), raw("t2", W2_END - 1_000)],
        ])
        result = TradeHistoryService(client).get_history("ZECUSDT")

        ids = [t.trade_id for t in result.trades]
        assert ids.count("dup") == 1

    # -----------------------------------------------------------------------
    # exec_type field across windows
    # -----------------------------------------------------------------------

    def test_exec_type_preserved_from_first_window(self):
        client = SequentialStubClient(pages=[
            [raw("t1", W1_END - 1_000, "Trade")],
        ])
        result = TradeHistoryService(client).get_history("ZECUSDT")
        assert result.trades[0].exec_type == "Trade"

    def test_exec_type_preserved_from_later_window(self):
        client = SequentialStubClient(pages=[
            [],                                               # W1 empty
            [raw("f1", W2_END - 1_000, "Funding")],         # W2
        ])
        result = TradeHistoryService(client).get_history("ZECUSDT")
        assert result.trades[0].exec_type == "Funding"

    # -----------------------------------------------------------------------
    # No-infinite-loop guarantees
    # -----------------------------------------------------------------------

    def test_terminates_with_all_empty_responses(self):
        client = SequentialStubClient(pages=[])
        result = TradeHistoryService(client).get_history("ZECUSDT")
        assert isinstance(result, TradeHistory)

    def test_error_from_any_window_propagates(self):
        class ErrClient:
            def get_trade_history(self, symbol, category, limit,
                                  start_time=None, end_time=None):
                raise BybitAPIError("Bybit API error [10003]: boom")
        with pytest.raises(BybitAPIError, match="10003"):
            TradeHistoryService(ErrClient()).get_history("ZECUSDT")


# ===========================================================================
# TradeHistoryExporter tests
# ===========================================================================

def _make_history(*trades: tuple) -> TradeHistory:
    """
    Build a TradeHistory from
    (trade_id, symbol, side, price, size, exec_type, date, time) tuples.
    """
    return TradeHistory(
        symbol="ZECUSDT",
        category="linear",
        trades=[
            Trade(trade_id=tid, symbol=sym, side=sd, price=p, size=sz,
                  exec_type=et, date=d, time=t)
            for tid, sym, sd, p, sz, et, d, t in trades
        ],
    )


SAMPLE_HISTORY = _make_history(
    ("exec-001", "ZECUSDT", "Buy",  30.50, 10.0, "Trade",   "2023-11-14", "22:13:19"),
    ("exec-002", "ZECUSDT", "Sell", 31.00,  5.0, "Trade",   "2023-11-14", "22:13:18"),
    ("exec-003", "ZECUSDT", "Buy",  29.75, 20.0, "Funding", "2023-11-14", "22:13:17"),
)


class TestTradeHistoryExporter:

    def test_file_is_created(self, tmp_path):
        TradeHistoryExporter(tmp_path / "ZECUSDT_tradeHistory.csv").export(SAMPLE_HISTORY)
        assert (tmp_path / "ZECUSDT_tradeHistory.csv").exists()

    def test_data_directory_created_automatically(self, tmp_path):
        output = tmp_path / "data" / "ZECUSDT_tradeHistory.csv"
        TradeHistoryExporter(output).export(SAMPLE_HISTORY)
        assert output.parent.is_dir()

    def test_file_is_overwritten_not_appended(self, tmp_path):
        output = tmp_path / "out.csv"
        exp = TradeHistoryExporter(output)
        exp.export(SAMPLE_HISTORY)
        exp.export(_make_history(("e1", "ZECUSDT", "Buy", 30.5, 10.0, "Trade",
                                  "2023-11-14", "22:13:19")))
        rows = read_csv(output)
        assert len(rows) == 2  # header + 1 trade

    def test_correct_headers_are_written(self, tmp_path):
        output = tmp_path / "out.csv"
        TradeHistoryExporter(output).export(SAMPLE_HISTORY)
        rows = read_csv(output)
        assert rows[0] == HEADERS
        assert rows[0] == ["trade_id", "symbol", "side", "price", "size",
                           "exec_type", "date", "time"]

    def test_exec_type_column_present_in_headers(self, tmp_path):
        output = tmp_path / "out.csv"
        TradeHistoryExporter(output).export(SAMPLE_HISTORY)
        rows = read_csv(output)
        assert "exec_type" in rows[0]

    def test_no_timestamp_column_in_headers(self, tmp_path):
        output = tmp_path / "out.csv"
        TradeHistoryExporter(output).export(SAMPLE_HISTORY)
        rows = read_csv(output)
        assert "timestamp" not in rows[0]

    def test_correct_number_of_rows(self, tmp_path):
        output = tmp_path / "out.csv"
        TradeHistoryExporter(output).export(SAMPLE_HISTORY)
        rows = read_csv(output)
        assert len(rows) == 4  # 1 header + 3 trades

    def test_correct_values_first_row(self, tmp_path):
        output = tmp_path / "out.csv"
        TradeHistoryExporter(output).export(SAMPLE_HISTORY)
        rows = read_csv(output)
        assert rows[1] == ["exec-001", "ZECUSDT", "Buy", "30.5", "10.0",
                           "Trade", "2023-11-14", "22:13:19"]

    def test_funding_exec_type_written_correctly(self, tmp_path):
        output = tmp_path / "out.csv"
        TradeHistoryExporter(output).export(SAMPLE_HISTORY)
        rows = read_csv(output)
        assert rows[3][5] == "Funding"   # exec_type is column index 5

    def test_correct_column_count_is_eight(self, tmp_path):
        output = tmp_path / "out.csv"
        TradeHistoryExporter(output).export(SAMPLE_HISTORY)
        rows = read_csv(output)
        assert len(rows[0]) == 8
        assert len(rows[1]) == 8

    def test_empty_history_writes_header_only(self, tmp_path):
        output = tmp_path / "out.csv"
        TradeHistoryExporter(output).export(_make_history())
        rows = read_csv(output)
        assert rows == [HEADERS]

    def test_make_exporter_builds_correct_filename(self, tmp_path):
        assert make_exporter("ZECUSDT", output_dir=tmp_path)._output_path == \
               tmp_path / "ZECUSDT_tradeHistory.csv"

    def test_make_exporter_uppercases_symbol(self, tmp_path):
        assert make_exporter("zecusdt", output_dir=tmp_path)._output_path.name == \
               "ZECUSDT_tradeHistory.csv"

    def test_make_exporter_default_dir_is_data(self):
        assert make_exporter("ZECUSDT")._output_path == \
               pathlib.Path("data") / "ZECUSDT_tradeHistory.csv"
