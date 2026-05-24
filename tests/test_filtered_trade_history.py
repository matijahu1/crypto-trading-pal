"""
tests/test_filtered_trade_history.py — unit tests for
FilteredTradeHistoryService and the filtered_trade_history_exporter factories.

Structure
---------
  StubTradeClient           — plain stub, no unittest.mock
  TestFilteredByTrade       — service filtering for execType "Trade"
  TestFilteredByFunding     — service filtering for execType "Funding"
  TestFilteredEdgeCases     — empty results, error propagation, passthrough args
  TestFilteredExporterFactories — filename / path assertions for the factories

All tests follow arrange → act → assert.
The API client is never called for real; the stub controls the response.
"""

from __future__ import annotations

import csv
import pathlib
from decimal import Decimal

import pytest

from api.bybit_client import BybitAPIError
from exporters.filtered_trade_history_exporter import (
    make_funding_exporter,
    make_trade_exporter,
    make_trade_type_exporter,
)
from exporters.trade_history_exporter import HEADERS, TradeHistoryExporter
from services.filtered_trade_history import FilteredTradeHistoryService
from services.trade_history import (
    _MS_PER_DAY,
    Trade,
    TradeHistory,
)

# ---------------------------------------------------------------------------
# Constants mirrored from test_trade_history.py
# ---------------------------------------------------------------------------

NOW_FIXED = 1_700_000_000_000  # 2023-11-14 22:13:20 UTC
W1_END = 1_700_000_000_000
W1_START = 1_699_395_200_000


# ---------------------------------------------------------------------------
# Stub clients
# ---------------------------------------------------------------------------


class StubTradeClient:
    """
    Returns *trades* on the first call, [] on every subsequent call so the
    inner page-loop in TradeHistoryService terminates cleanly.

    Setting raise_error=True makes every call raise BybitAPIError instead,
    which lets us verify error propagation through FilteredTradeHistoryService.
    """

    def __init__(
        self,
        trades: list[dict] | None = None,
        raise_error: bool = False,
    ) -> None:
        self._trades = trades or []
        self._raise_error = raise_error
        self.call_count = 0
        self.last_symbol: str | None = None
        self.last_category: str | None = None
        self.last_lookback_used: int | None = None  # captured via start_time arg

    def get_trade_history(
        self,
        symbol: str,
        category: str,
        limit: int,
        start_time: int | None = None,
        end_time: int | None = None,
    ) -> list[dict]:
        self.last_symbol = symbol
        self.last_category = category
        self.last_lookback_used = start_time
        self.call_count += 1
        if self._raise_error:
            raise BybitAPIError("Bybit API error [10003]: boom")
        return self._trades if self.call_count == 1 else []


# ---------------------------------------------------------------------------
# Helper: build a raw API dict
# ---------------------------------------------------------------------------


def raw(
    exec_id: str,
    exec_time: int,
    exec_type: str = "Trade",
    exec_fee: str = "0.05",
) -> dict:
    """Minimal raw API trade dict."""
    return {
        "execId": exec_id,
        "symbol": "ZECUSDT",
        "side": "Buy",
        "execPrice": "30.0",
        "execQty": "1",
        "execType": exec_type,
        "execTime": str(exec_time),
        "execFee": exec_fee,
    }


def read_csv(path: pathlib.Path) -> list[list[str]]:
    return list(csv.reader(path.read_text(encoding="utf-8").splitlines()))


# ---------------------------------------------------------------------------
# Mixed source data used across multiple test classes
# ---------------------------------------------------------------------------

MIXED_TRADES = [
    raw("t1", W1_END - 1_000, "Trade", "0.10"),
    raw("t2", W1_END - 2_000, "Trade", "0.08"),
    raw("f1", W1_END - 3_000, "Funding", "-0.02"),
    raw("f2", W1_END - 4_000, "Funding", "-0.01"),
    raw("b1", W1_END - 5_000, "BustTrade", "0.00"),
]


# ===========================================================================
# FilteredTradeHistoryService — filtering for execType "Trade"
# ===========================================================================


class TestFilteredByTrade:
    """Verify that only Trade rows pass through the filter."""

    @pytest.fixture(autouse=True)
    def freeze_time(self, monkeypatch):
        import services.trade_history as m

        monkeypatch.setattr(m, "_now_ms", lambda: NOW_FIXED)

    def _svc(self, trades=MIXED_TRADES):
        return FilteredTradeHistoryService(
            StubTradeClient(trades),
            exec_type="Trade",
            lookback_days=7,
        )

    def test_returns_trade_history_dataclass(self):
        result = self._svc().get_history("ZECUSDT")
        assert isinstance(result, TradeHistory)

    def test_only_trade_exec_types_returned(self):
        result = self._svc().get_history("ZECUSDT")
        assert all(t.exec_type == "Trade" for t in result.trades)

    def test_correct_count_of_trade_rows(self):
        result = self._svc().get_history("ZECUSDT")
        # MIXED_TRADES has 2 Trade rows
        assert len(result.trades) == 2

    def test_funding_rows_excluded(self):
        result = self._svc().get_history("ZECUSDT")
        ids = {t.trade_id for t in result.trades}
        assert "f1" not in ids and "f2" not in ids

    def test_bust_trade_rows_excluded(self):
        result = self._svc().get_history("ZECUSDT")
        ids = {t.trade_id for t in result.trades}
        assert "b1" not in ids

    def test_symbol_preserved_in_result(self):
        result = self._svc().get_history("ZECUSDT")
        assert result.symbol == "ZECUSDT"

    def test_category_preserved_in_result(self):
        result = self._svc(MIXED_TRADES).get_history("ZECUSDT")
        assert result.category == "linear"

    def test_symbol_is_uppercased(self):
        result = self._svc().get_history("zecusdt")
        assert result.symbol == "ZECUSDT"

    def test_symbol_uppercased_before_passing_to_client(self):
        client = StubTradeClient(MIXED_TRADES)
        FilteredTradeHistoryService(
            client, exec_type="Trade", lookback_days=7
        ).get_history("zecusdt")
        assert client.last_symbol == "ZECUSDT"

    def test_trades_are_trade_instances(self):
        result = self._svc().get_history("ZECUSDT")
        assert all(isinstance(t, Trade) for t in result.trades)

    def test_price_is_decimal(self):
        result = self._svc().get_history("ZECUSDT")
        assert all(isinstance(t.price, Decimal) for t in result.trades)

    def test_trading_fee_is_decimal(self):
        result = self._svc().get_history("ZECUSDT")
        assert all(isinstance(t.trading_fee, Decimal) for t in result.trades)

    def test_correct_trade_ids_in_result(self):
        result = self._svc().get_history("ZECUSDT")
        ids = {t.trade_id for t in result.trades}
        assert ids == {"t1", "t2"}


# ===========================================================================
# FilteredTradeHistoryService — filtering for execType "Funding"
# ===========================================================================


class TestFilteredByFunding:
    """Verify that only Funding rows pass through the filter."""

    @pytest.fixture(autouse=True)
    def freeze_time(self, monkeypatch):
        import services.trade_history as m

        monkeypatch.setattr(m, "_now_ms", lambda: NOW_FIXED)

    def _svc(self, trades=MIXED_TRADES):
        return FilteredTradeHistoryService(
            StubTradeClient(trades),
            exec_type="Funding",
            lookback_days=7,
        )

    def test_only_funding_exec_types_returned(self):
        result = self._svc().get_history("ZECUSDT")
        assert all(t.exec_type == "Funding" for t in result.trades)

    def test_correct_count_of_funding_rows(self):
        result = self._svc().get_history("ZECUSDT")
        # MIXED_TRADES has 2 Funding rows
        assert len(result.trades) == 2

    def test_trade_rows_excluded(self):
        result = self._svc().get_history("ZECUSDT")
        ids = {t.trade_id for t in result.trades}
        assert "t1" not in ids and "t2" not in ids

    def test_correct_funding_ids_in_result(self):
        result = self._svc().get_history("ZECUSDT")
        ids = {t.trade_id for t in result.trades}
        assert ids == {"f1", "f2"}

    def test_negative_fee_preserved(self):
        result = self._svc().get_history("ZECUSDT")
        fees = {t.trade_id: t.trading_fee for t in result.trades}
        assert fees["f1"] == Decimal("-0.02")
        assert fees["f2"] == Decimal("-0.01")

    def test_symbol_preserved(self):
        result = self._svc().get_history("ZECUSDT")
        assert result.symbol == "ZECUSDT"

    def test_returns_trade_history_dataclass(self):
        result = self._svc().get_history("ZECUSDT")
        assert isinstance(result, TradeHistory)


# ===========================================================================
# Edge cases shared across filter types
# ===========================================================================


class TestFilteredEdgeCases:
    @pytest.fixture(autouse=True)
    def freeze_time(self, monkeypatch):
        import services.trade_history as m

        monkeypatch.setattr(m, "_now_ms", lambda: NOW_FIXED)

    def test_empty_source_returns_empty_trade_list(self):
        svc = FilteredTradeHistoryService(
            StubTradeClient([]), exec_type="Trade", lookback_days=7
        )
        result = svc.get_history("ZECUSDT")
        assert result.trades == []

    def test_no_matching_exec_type_returns_empty_trade_list(self):
        trades_only = [raw("t1", W1_END - 1_000, "Trade")]
        svc = FilteredTradeHistoryService(
            StubTradeClient(trades_only), exec_type="Funding", lookback_days=7
        )
        result = svc.get_history("ZECUSDT")
        assert result.trades == []

    def test_result_symbol_matches_when_no_trades(self):
        svc = FilteredTradeHistoryService(
            StubTradeClient([]), exec_type="Trade", lookback_days=7
        )
        result = svc.get_history("ZECUSDT")
        assert result.symbol == "ZECUSDT"

    def test_api_error_propagates(self):
        svc = FilteredTradeHistoryService(
            StubTradeClient(raise_error=True), exec_type="Trade", lookback_days=7
        )
        with pytest.raises(BybitAPIError):
            svc.get_history("ZECUSDT")

    def test_lookback_days_override_is_forwarded(self):
        """lookback_days passed to get_history must reach the API client."""
        client = StubTradeClient(MIXED_TRADES)
        svc = FilteredTradeHistoryService(client, exec_type="Trade", lookback_days=30)
        svc.get_history("ZECUSDT", lookback_days=3)
        # With lookback_days=3, the start_time sent to the API must be
        # approximately NOW_FIXED minus 3 days (within a 1-day tolerance).
        expected_start = NOW_FIXED - 3 * _MS_PER_DAY
        assert abs(client.last_lookback_used - expected_start) < _MS_PER_DAY

    def test_start_time_ms_takes_priority_over_lookback(self):
        """Explicit start_time_ms must reach the API client unchanged."""
        client = StubTradeClient(MIXED_TRADES)
        svc = FilteredTradeHistoryService(client, exec_type="Trade", lookback_days=7)
        explicit_start = NOW_FIXED - 2 * _MS_PER_DAY
        svc.get_history("ZECUSDT", start_time_ms=explicit_start)
        assert client.last_lookback_used == explicit_start

    def test_category_forwarded_to_client(self):
        client = StubTradeClient(MIXED_TRADES)
        FilteredTradeHistoryService(
            client, exec_type="Trade", category="inverse", lookback_days=7
        ).get_history("ZECUSDT")
        assert client.last_category == "inverse"

    def test_all_source_trades_with_matching_type_returned(self):
        """When every source trade matches, nothing is dropped."""
        all_trade = [raw(f"t{i}", W1_END - i * 1000, "Trade") for i in range(1, 6)]
        svc = FilteredTradeHistoryService(
            StubTradeClient(all_trade), exec_type="Trade", lookback_days=7
        )
        result = svc.get_history("ZECUSDT")
        assert len(result.trades) == 5

    def test_unknown_exec_type_filter_returns_empty(self):
        """A filter for an exec type not present in the data yields zero rows."""
        svc = FilteredTradeHistoryService(
            StubTradeClient(MIXED_TRADES), exec_type="Liquidation", lookback_days=7
        )
        result = svc.get_history("ZECUSDT")
        assert result.trades == []


# ===========================================================================
# Exporter factory tests
# ===========================================================================


class TestFilteredExporterFactories:
    """Verify filename construction for all three factory functions."""

    def test_make_trade_type_exporter_trade_filename(self, tmp_path):
        exp = make_trade_type_exporter("ZECUSDT", "Trade", output_dir=tmp_path)
        assert exp._output_path == tmp_path / "ZECUSDT_TradesTypeTrade.csv"

    def test_make_trade_type_exporter_funding_filename(self, tmp_path):
        exp = make_trade_type_exporter("ZECUSDT", "Funding", output_dir=tmp_path)
        assert exp._output_path == tmp_path / "ZECUSDT_TradesTypeFunding.csv"

    def test_make_trade_type_exporter_uppercases_symbol(self, tmp_path):
        exp = make_trade_type_exporter("zecusdt", "Trade", output_dir=tmp_path)
        assert exp._output_path.name == "ZECUSDT_TradesTypeTrade.csv"

    def test_make_trade_type_exporter_default_dir_is_data(self):
        exp = make_trade_type_exporter("ZECUSDT", "Trade")
        assert exp._output_path == pathlib.Path("data") / "ZECUSDT_TradesTypeTrade.csv"

    def test_make_trade_exporter_filename(self, tmp_path):
        exp = make_trade_exporter("ZECUSDT", output_dir=tmp_path)
        assert exp._output_path == tmp_path / "ZECUSDT_TradesTypeTrade.csv"

    def test_make_trade_exporter_default_dir(self):
        exp = make_trade_exporter("ZECUSDT")
        assert exp._output_path == pathlib.Path("data") / "ZECUSDT_TradesTypeTrade.csv"

    def test_make_funding_exporter_filename(self, tmp_path):
        exp = make_funding_exporter("ZECUSDT", output_dir=tmp_path)
        assert exp._output_path == tmp_path / "ZECUSDT_TradesTypeFunding.csv"

    def test_make_funding_exporter_default_dir(self):
        exp = make_funding_exporter("ZECUSDT")
        assert (
            exp._output_path == pathlib.Path("data") / "ZECUSDT_TradesTypeFunding.csv"
        )

    def test_make_trade_exporter_returns_trade_history_exporter(self, tmp_path):
        assert isinstance(
            make_trade_exporter("ZECUSDT", output_dir=tmp_path), TradeHistoryExporter
        )

    def test_make_funding_exporter_returns_trade_history_exporter(self, tmp_path):
        assert isinstance(
            make_funding_exporter("ZECUSDT", output_dir=tmp_path), TradeHistoryExporter
        )

    def test_make_trade_type_exporter_uppercases_symbol_alias(self, tmp_path):
        exp = make_trade_exporter("zecusdt", output_dir=tmp_path)
        assert exp._output_path.name == "ZECUSDT_TradesTypeTrade.csv"

    def test_make_funding_exporter_uppercases_symbol_alias(self, tmp_path):
        exp = make_funding_exporter("zecusdt", output_dir=tmp_path)
        assert exp._output_path.name == "ZECUSDT_TradesTypeFunding.csv"

    # -----------------------------------------------------------------------
    # End-to-end: factory → export → CSV content
    # -----------------------------------------------------------------------

    def _sample_history(self, exec_type: str) -> TradeHistory:
        return TradeHistory(
            symbol="ZECUSDT",
            category="linear",
            trades=[
                Trade(
                    trade_id="x-001",
                    symbol="ZECUSDT",
                    side="Buy",
                    price=Decimal("30.50"),
                    size=Decimal("1"),
                    exec_type=exec_type,
                    trading_fee=Decimal("0.05"),
                    date="2023-11-14",
                    time="22:13:20",
                )
            ],
        )

    def test_trade_exporter_writes_correct_headers(self, tmp_path):
        exp = make_trade_exporter("ZECUSDT", output_dir=tmp_path)
        exp.export(self._sample_history("Trade"))
        rows = read_csv(tmp_path / "ZECUSDT_TradesTypeTrade.csv")
        assert rows[0] == HEADERS

    def test_funding_exporter_writes_correct_headers(self, tmp_path):
        exp = make_funding_exporter("ZECUSDT", output_dir=tmp_path)
        exp.export(self._sample_history("Funding"))
        rows = read_csv(tmp_path / "ZECUSDT_TradesTypeFunding.csv")
        assert rows[0] == HEADERS

    def test_trade_exporter_writes_data_row(self, tmp_path):
        exp = make_trade_exporter("ZECUSDT", output_dir=tmp_path)
        exp.export(self._sample_history("Trade"))
        rows = read_csv(tmp_path / "ZECUSDT_TradesTypeTrade.csv")
        assert len(rows) == 2  # header + 1 trade

    def test_funding_exporter_writes_data_row(self, tmp_path):
        exp = make_funding_exporter("ZECUSDT", output_dir=tmp_path)
        exp.export(self._sample_history("Funding"))
        rows = read_csv(tmp_path / "ZECUSDT_TradesTypeFunding.csv")
        assert len(rows) == 2  # header + 1 funding

    def test_trade_exporter_correct_values(self, tmp_path):
        exp = make_trade_exporter("ZECUSDT", output_dir=tmp_path)
        exp.export(self._sample_history("Trade"))
        rows = read_csv(tmp_path / "ZECUSDT_TradesTypeTrade.csv")
        assert rows[1] == [
            "x-001",
            "ZECUSDT",
            "Buy",
            "30.50",
            "1",
            "Trade",
            "0.05",
            "2023-11-14",
            "22:13:20",
        ]

    def test_funding_exporter_correct_values(self, tmp_path):
        exp = make_funding_exporter("ZECUSDT", output_dir=tmp_path)
        exp.export(self._sample_history("Funding"))
        rows = read_csv(tmp_path / "ZECUSDT_TradesTypeFunding.csv")
        assert rows[1] == [
            "x-001",
            "ZECUSDT",
            "Buy",
            "30.50",
            "1",
            "Funding",
            "0.05",
            "2023-11-14",
            "22:13:20",
        ]

    def test_column_count_is_nine(self, tmp_path):
        exp = make_trade_exporter("ZECUSDT", output_dir=tmp_path)
        exp.export(self._sample_history("Trade"))
        rows = read_csv(tmp_path / "ZECUSDT_TradesTypeTrade.csv")
        assert all(len(r) == 9 for r in rows)

    def test_empty_history_writes_header_only(self, tmp_path):
        empty = TradeHistory(symbol="ZECUSDT", category="linear", trades=[])
        exp = make_trade_exporter("ZECUSDT", output_dir=tmp_path)
        exp.export(empty)
        rows = read_csv(tmp_path / "ZECUSDT_TradesTypeTrade.csv")
        assert rows == [HEADERS]
