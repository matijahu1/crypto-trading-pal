"""
tests/test_lifo_report.py — comprehensive unit tests for the LIFO report feature.

Covers:
  LifoReportService:
    - Simple Buy → Sell full match
    - Partial fill (one Sell matches only part of a Buy lot)
    - Multiple Buys closed by a single large Sell
    - LIFO ordering (newest Buy closed first)
    - Empty input file
    - Missing input file
    - File with no trades / header-only
    - Sells without matching Buys (warn-and-continue)
    - Realized PnL accuracy

  LifoReportExporter:
    - File creation
    - Correct headers
    - Correct column count
    - Correct row count
    - Correct field values for OPEN / PARTIAL / CLOSED lots
    - Empty exit_price written as ""
    - Overwrites existing file

Each test uses tmp_path so no real filesystem state leaks between runs.
No mocking of the Bybit API — this feature reads a local CSV only.
"""

from __future__ import annotations

import csv
import pathlib
from typing import Any

import pytest

from services.lifo_report import LifoReportService, LotRecord
from exporters.lifo_report_exporter import LifoReportExporter, HEADERS


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _write_order_csv(path: pathlib.Path, rows: list[dict[str, Any]]) -> None:
    """Write a minimal order-history CSV at *path*."""
    fieldnames = [
        "order_id", "symbol", "side", "order_type",
        "price", "qty", "order_status",
        "created_ts", "updated_ts",
        "created_date", "created_time",
        "updated_date", "updated_time",
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            full = {k: "" for k in fieldnames}
            full.update(row)
            writer.writerow(full)


def _buy(order_id: str, price: float, qty: float,
         updated_ts: int, updated_date: str = "2024-01-01") -> dict:
    return {
        "order_id":    order_id,
        "side":        "Buy",
        "price":       str(price),
        "qty":         str(qty),
        "updated_ts":  str(updated_ts),
        "updated_date": updated_date,
        "order_status": "Filled",
    }


def _sell(order_id: str, price: float, qty: float,
          updated_ts: int, updated_date: str = "2024-01-02") -> dict:
    return {
        "order_id":    order_id,
        "side":        "Sell",
        "price":       str(price),
        "qty":         str(qty),
        "updated_ts":  str(updated_ts),
        "updated_date": updated_date,
        "order_status": "Filled",
    }


def _read_csv(path: pathlib.Path) -> list[list[str]]:
    return list(csv.reader(path.read_text(encoding="utf-8").splitlines()))


# ---------------------------------------------------------------------------
# LifoReportService — core matching
# ---------------------------------------------------------------------------

class TestSimpleBuySellMatch:
    """One Buy fully matched by one Sell at a higher price."""

    def test_single_closed_lot_returned(self, tmp_path: pathlib.Path) -> None:
        csv_path = tmp_path / "XUSDT_orderHistory.csv"
        _write_order_csv(csv_path, [
            _buy("b1",  10.0, 5.0, 1_000),
            _sell("s1", 12.0, 5.0, 2_000),
        ])
        records = LifoReportService(csv_path).generate()
        assert len(records) == 1

    def test_status_is_closed(self, tmp_path: pathlib.Path) -> None:
        csv_path = tmp_path / "XUSDT_orderHistory.csv"
        _write_order_csv(csv_path, [
            _buy("b1",  10.0, 5.0, 1_000),
            _sell("s1", 12.0, 5.0, 2_000),
        ])
        records = LifoReportService(csv_path).generate()
        assert records[0].status == "CLOSED"

    def test_realized_pnl_is_correct(self, tmp_path: pathlib.Path) -> None:
        csv_path = tmp_path / "XUSDT_orderHistory.csv"
        _write_order_csv(csv_path, [
            _buy("b1",  10.0, 5.0, 1_000),
            _sell("s1", 12.0, 5.0, 2_000),
        ])
        records = LifoReportService(csv_path).generate()
        # (12 - 10) * 5 = 10.0
        assert records[0].realized_pnl == pytest.approx(10.0)

    def test_open_qty_is_zero(self, tmp_path: pathlib.Path) -> None:
        csv_path = tmp_path / "XUSDT_orderHistory.csv"
        _write_order_csv(csv_path, [
            _buy("b1",  10.0, 5.0, 1_000),
            _sell("s1", 12.0, 5.0, 2_000),
        ])
        records = LifoReportService(csv_path).generate()
        assert records[0].open_qty == 0.0

    def test_matched_qty_equals_total_qty(self, tmp_path: pathlib.Path) -> None:
        csv_path = tmp_path / "XUSDT_orderHistory.csv"
        _write_order_csv(csv_path, [
            _buy("b1",  10.0, 5.0, 1_000),
            _sell("s1", 12.0, 5.0, 2_000),
        ])
        records = LifoReportService(csv_path).generate()
        r = records[0]
        assert r.matched_qty == r.total_qty

    def test_exit_price_equals_sell_price(self, tmp_path: pathlib.Path) -> None:
        csv_path = tmp_path / "XUSDT_orderHistory.csv"
        _write_order_csv(csv_path, [
            _buy("b1",  10.0, 5.0, 1_000),
            _sell("s1", 12.0, 5.0, 2_000),
        ])
        records = LifoReportService(csv_path).generate()
        assert records[0].exit_price == pytest.approx(12.0)

    def test_negative_pnl_on_loss(self, tmp_path: pathlib.Path) -> None:
        csv_path = tmp_path / "XUSDT_orderHistory.csv"
        _write_order_csv(csv_path, [
            _buy("b1",  15.0, 10.0, 1_000),
            _sell("s1", 12.0, 10.0, 2_000),
        ])
        records = LifoReportService(csv_path).generate()
        # (12 - 15) * 10 = -30
        assert records[0].realized_pnl == pytest.approx(-30.0)


# ---------------------------------------------------------------------------
# LifoReportService — partial fills
# ---------------------------------------------------------------------------

class TestPartialFill:
    """A Sell that matches only part of a Buy lot."""

    def test_status_is_partial(self, tmp_path: pathlib.Path) -> None:
        csv_path = tmp_path / "XUSDT_orderHistory.csv"
        _write_order_csv(csv_path, [
            _buy("b1",  10.0, 10.0, 1_000),
            _sell("s1", 12.0,  4.0, 2_000),
        ])
        records = LifoReportService(csv_path).generate()
        assert records[0].status == "PARTIAL"

    def test_open_qty_is_remaining(self, tmp_path: pathlib.Path) -> None:
        csv_path = tmp_path / "XUSDT_orderHistory.csv"
        _write_order_csv(csv_path, [
            _buy("b1",  10.0, 10.0, 1_000),
            _sell("s1", 12.0,  4.0, 2_000),
        ])
        records = LifoReportService(csv_path).generate()
        assert records[0].open_qty == pytest.approx(6.0)

    def test_matched_qty_equals_sell_qty(self, tmp_path: pathlib.Path) -> None:
        csv_path = tmp_path / "XUSDT_orderHistory.csv"
        _write_order_csv(csv_path, [
            _buy("b1",  10.0, 10.0, 1_000),
            _sell("s1", 12.0,  4.0, 2_000),
        ])
        records = LifoReportService(csv_path).generate()
        assert records[0].matched_qty == pytest.approx(4.0)

    def test_pnl_only_on_matched_portion(self, tmp_path: pathlib.Path) -> None:
        csv_path = tmp_path / "XUSDT_orderHistory.csv"
        _write_order_csv(csv_path, [
            _buy("b1",  10.0, 10.0, 1_000),
            _sell("s1", 12.0,  4.0, 2_000),
        ])
        records = LifoReportService(csv_path).generate()
        # (12 - 10) * 4 = 8
        assert records[0].realized_pnl == pytest.approx(8.0)

    def test_single_lot_returned_for_partial(self, tmp_path: pathlib.Path) -> None:
        csv_path = tmp_path / "XUSDT_orderHistory.csv"
        _write_order_csv(csv_path, [
            _buy("b1",  10.0, 10.0, 1_000),
            _sell("s1", 12.0,  4.0, 2_000),
        ])
        records = LifoReportService(csv_path).generate()
        assert len(records) == 1


# ---------------------------------------------------------------------------
# LifoReportService — multiple buys, one large sell
# ---------------------------------------------------------------------------

class TestMultipleBuysSingleSell:
    """One large Sell that closes multiple Buy lots (LIFO order)."""

    def _make_csv(self, tmp_path: pathlib.Path) -> pathlib.Path:
        csv_path = tmp_path / "XUSDT_orderHistory.csv"
        _write_order_csv(csv_path, [
            _buy("b1", 10.0, 5.0, 1_000),   # oldest
            _buy("b2", 11.0, 5.0, 2_000),   # middle
            _buy("b3", 12.0, 5.0, 3_000),   # newest (LIFO: closed first)
            _sell("s1", 15.0, 8.0, 4_000),  # closes b3 (5) fully + b2 (3) partially
        ])
        return csv_path

    def test_three_lots_returned(self, tmp_path: pathlib.Path) -> None:
        records = LifoReportService(self._make_csv(tmp_path)).generate()
        assert len(records) == 3

    def test_b3_is_closed(self, tmp_path: pathlib.Path) -> None:
        records = LifoReportService(self._make_csv(tmp_path)).generate()
        closed = [r for r in records if r.entry_price == 12.0]
        assert len(closed) == 1
        assert closed[0].status == "CLOSED"

    def test_b2_is_partial(self, tmp_path: pathlib.Path) -> None:
        records = LifoReportService(self._make_csv(tmp_path)).generate()
        partial = [r for r in records if r.entry_price == 11.0]
        assert len(partial) == 1
        assert partial[0].status == "PARTIAL"

    def test_b1_is_open(self, tmp_path: pathlib.Path) -> None:
        records = LifoReportService(self._make_csv(tmp_path)).generate()
        open_lots = [r for r in records if r.entry_price == 10.0]
        assert len(open_lots) == 1
        assert open_lots[0].status == "OPEN"

    def test_total_realized_pnl(self, tmp_path: pathlib.Path) -> None:
        records = LifoReportService(self._make_csv(tmp_path)).generate()
        # b3 fully: (15-12)*5=15; b2 partial: (15-11)*3=12
        total = sum(r.realized_pnl for r in records)
        assert total == pytest.approx(27.0)

    def test_b1_open_qty_unchanged(self, tmp_path: pathlib.Path) -> None:
        records = LifoReportService(self._make_csv(tmp_path)).generate()
        b1 = next(r for r in records if r.entry_price == 10.0)
        assert b1.open_qty == pytest.approx(5.0)


# ---------------------------------------------------------------------------
# LifoReportService — LIFO ordering verification
# ---------------------------------------------------------------------------

class TestLifoOrdering:
    """Explicit verification that the NEWEST Buy is matched first."""

    def test_newer_buy_closed_before_older(self, tmp_path: pathlib.Path) -> None:
        csv_path = tmp_path / "XUSDT_orderHistory.csv"
        _write_order_csv(csv_path, [
            _buy("old",    9.0, 10.0, 1_000),   # older Buy
            _buy("newest", 8.0, 10.0, 2_000),   # newer Buy — should be matched first
            _sell("s1",   12.0, 10.0, 3_000),
        ])
        records = LifoReportService(csv_path).generate()
        newest = next(r for r in records if r.entry_price == 8.0)
        older  = next(r for r in records if r.entry_price == 9.0)
        assert newest.status == "CLOSED"
        assert older.status  == "OPEN"

    def test_lifo_pnl_uses_newest_entry_price(self, tmp_path: pathlib.Path) -> None:
        csv_path = tmp_path / "XUSDT_orderHistory.csv"
        _write_order_csv(csv_path, [
            _buy("old",    9.0, 5.0, 1_000),
            _buy("newest", 8.0, 5.0, 2_000),
            _sell("s1",   10.0, 5.0, 3_000),
        ])
        records = LifoReportService(csv_path).generate()
        closed = next(r for r in records if r.status == "CLOSED")
        # Should be (10 - 8) * 5 = 10, NOT (10 - 9) * 5 = 5
        assert closed.realized_pnl == pytest.approx(10.0)
        assert closed.entry_price == pytest.approx(8.0)

    def test_three_buys_lifo_order(self, tmp_path: pathlib.Path) -> None:
        csv_path = tmp_path / "XUSDT_orderHistory.csv"
        _write_order_csv(csv_path, [
            _buy("b1", 10.0, 5.0, 1_000),  # oldest
            _buy("b2", 11.0, 5.0, 2_000),
            _buy("b3", 12.0, 5.0, 3_000),  # newest
            _sell("s1", 15.0, 5.0, 4_000), # should close b3 only
        ])
        records = LifoReportService(csv_path).generate()
        closed = [r for r in records if r.status == "CLOSED"]
        assert len(closed) == 1
        assert closed[0].entry_price == pytest.approx(12.0)


# ---------------------------------------------------------------------------
# LifoReportService — edge cases
# ---------------------------------------------------------------------------

class TestEdgeCases:

    def test_missing_file_raises_file_not_found(self, tmp_path: pathlib.Path) -> None:
        missing = tmp_path / "nonexistent_orderHistory.csv"
        with pytest.raises(FileNotFoundError, match="Order history file not found"):
            LifoReportService(missing).generate()

    def test_header_only_file_returns_empty_list(self, tmp_path: pathlib.Path) -> None:
        csv_path = tmp_path / "XUSDT_orderHistory.csv"
        _write_order_csv(csv_path, [])   # writes only the header row
        records = LifoReportService(csv_path).generate()
        assert records == []

    def test_only_buys_all_open(self, tmp_path: pathlib.Path) -> None:
        csv_path = tmp_path / "XUSDT_orderHistory.csv"
        _write_order_csv(csv_path, [
            _buy("b1", 10.0, 5.0, 1_000),
            _buy("b2", 11.0, 5.0, 2_000),
        ])
        records = LifoReportService(csv_path).generate()
        assert all(r.status == "OPEN" for r in records)

    def test_only_buys_exit_price_is_none(self, tmp_path: pathlib.Path) -> None:
        csv_path = tmp_path / "XUSDT_orderHistory.csv"
        _write_order_csv(csv_path, [
            _buy("b1", 10.0, 5.0, 1_000),
        ])
        records = LifoReportService(csv_path).generate()
        assert records[0].exit_price is None

    def test_only_buys_realized_pnl_is_zero(self, tmp_path: pathlib.Path) -> None:
        csv_path = tmp_path / "XUSDT_orderHistory.csv"
        _write_order_csv(csv_path, [
            _buy("b1", 10.0, 5.0, 1_000),
        ])
        records = LifoReportService(csv_path).generate()
        assert records[0].realized_pnl == 0.0

    def test_sell_without_buy_does_not_crash(self, tmp_path: pathlib.Path) -> None:
        csv_path = tmp_path / "XUSDT_orderHistory.csv"
        _write_order_csv(csv_path, [
            _sell("s1", 12.0, 5.0, 1_000),
        ])
        records = LifoReportService(csv_path).generate()
        assert records == []

    def test_output_sorted_by_entry_date_asc(self, tmp_path: pathlib.Path) -> None:
        csv_path = tmp_path / "XUSDT_orderHistory.csv"
        _write_order_csv(csv_path, [
            _buy("b1", 10.0, 5.0, 1_000, updated_date="2024-01-01"),
            _buy("b2", 11.0, 5.0, 2_000, updated_date="2024-01-02"),
            _sell("s1", 13.0, 5.0, 3_000, updated_date="2024-01-03"),
        ])
        records = LifoReportService(csv_path).generate()
        dates = [r.entry_date for r in records]
        assert dates == sorted(dates)

    def test_missing_columns_raises_value_error(self, tmp_path: pathlib.Path) -> None:
        csv_path = tmp_path / "bad.csv"
        # Write a CSV missing required columns
        csv_path.write_text("order_id,symbol\nb1,XUSDT\n", encoding="utf-8")
        with pytest.raises(ValueError, match="missing required columns"):
            LifoReportService(csv_path).generate()

    def test_exact_match_leaves_zero_open_qty(self, tmp_path: pathlib.Path) -> None:
        csv_path = tmp_path / "XUSDT_orderHistory.csv"
        _write_order_csv(csv_path, [
            _buy("b1", 10.0, 7.5, 1_000),
            _sell("s1", 11.0, 7.5, 2_000),
        ])
        records = LifoReportService(csv_path).generate()
        assert records[0].open_qty == pytest.approx(0.0)

    def test_multiple_sells_accumulate_pnl(self, tmp_path: pathlib.Path) -> None:
        csv_path = tmp_path / "XUSDT_orderHistory.csv"
        _write_order_csv(csv_path, [
            _buy("b1",  10.0, 10.0, 1_000),
            _sell("s1", 12.0,  5.0, 2_000),  # pnl = 10
            _sell("s2", 14.0,  5.0, 3_000),  # pnl = 20
        ])
        records = LifoReportService(csv_path).generate()
        assert len(records) == 1
        assert records[0].status == "CLOSED"
        assert records[0].realized_pnl == pytest.approx(30.0)

    def test_weighted_average_exit_price(self, tmp_path: pathlib.Path) -> None:
        csv_path = tmp_path / "XUSDT_orderHistory.csv"
        _write_order_csv(csv_path, [
            _buy("b1",  10.0, 10.0, 1_000),
            _sell("s1", 12.0,  4.0, 2_000),  # 4 units @ 12
            _sell("s2", 16.0,  6.0, 3_000),  # 6 units @ 16
        ])
        records = LifoReportService(csv_path).generate()
        # VWAP = (4*12 + 6*16) / 10 = (48+96)/10 = 14.4
        assert records[0].exit_price == pytest.approx(14.4)


# ---------------------------------------------------------------------------
# LifoReportExporter tests
# ---------------------------------------------------------------------------

def _make_open_lot() -> LotRecord:
    return LotRecord(
        entry_date="2024-01-01", exit_date="",
        total_qty=10.0, matched_qty=0.0, open_qty=10.0,
        status="OPEN",
        entry_price=10.0, exit_price=None,
        realized_pnl=0.0,
    )


def _make_closed_lot() -> LotRecord:
    return LotRecord(
        entry_date="2024-01-01", exit_date="2024-01-02",
        total_qty=10.0, matched_qty=10.0, open_qty=0.0,
        status="CLOSED",
        entry_price=10.0, exit_price=12.0,
        realized_pnl=20.0,
    )


def _make_partial_lot() -> LotRecord:
    return LotRecord(
        entry_date="2024-01-01", exit_date="2024-01-02",
        total_qty=10.0, matched_qty=4.0, open_qty=6.0,
        status="PARTIAL",
        entry_price=10.0, exit_price=12.0,
        realized_pnl=8.0,
    )


class TestLifoReportExporter:

    def test_file_is_created(self, tmp_path: pathlib.Path) -> None:
        out = tmp_path / "out.csv"
        LifoReportExporter(out).export([_make_closed_lot()])
        assert out.exists()

    def test_parent_directory_created_automatically(self, tmp_path: pathlib.Path) -> None:
        out = tmp_path / "nested" / "dir" / "out.csv"
        LifoReportExporter(out).export([_make_closed_lot()])
        assert out.parent.is_dir()

    def test_correct_headers(self, tmp_path: pathlib.Path) -> None:
        out = tmp_path / "out.csv"
        LifoReportExporter(out).export([])
        rows = _read_csv(out)
        assert rows[0] == HEADERS

    def test_column_count_matches_headers(self, tmp_path: pathlib.Path) -> None:
        out = tmp_path / "out.csv"
        LifoReportExporter(out).export([_make_closed_lot()])
        rows = _read_csv(out)
        assert len(rows[1]) == len(HEADERS)

    def test_correct_row_count(self, tmp_path: pathlib.Path) -> None:
        out = tmp_path / "out.csv"
        LifoReportExporter(out).export([_make_open_lot(), _make_closed_lot()])
        rows = _read_csv(out)
        assert len(rows) == 3  # header + 2 data rows

    def test_empty_records_writes_header_only(self, tmp_path: pathlib.Path) -> None:
        out = tmp_path / "out.csv"
        LifoReportExporter(out).export([])
        rows = _read_csv(out)
        assert rows == [HEADERS]

    def test_open_lot_values(self, tmp_path: pathlib.Path) -> None:
        out = tmp_path / "out.csv"
        LifoReportExporter(out).export([_make_open_lot()])
        rows = _read_csv(out)
        row = rows[1]
        assert row[HEADERS.index("status")]      == "OPEN"
        assert row[HEADERS.index("exit_price")]  == ""
        assert row[HEADERS.index("realized_pnl")] == "0.0"

    def test_closed_lot_values(self, tmp_path: pathlib.Path) -> None:
        out = tmp_path / "out.csv"
        LifoReportExporter(out).export([_make_closed_lot()])
        rows = _read_csv(out)
        row = rows[1]
        assert row[HEADERS.index("status")]       == "CLOSED"
        assert row[HEADERS.index("exit_price")]   == "12.0"
        assert row[HEADERS.index("realized_pnl")] == "20.0"

    def test_partial_lot_values(self, tmp_path: pathlib.Path) -> None:
        out = tmp_path / "out.csv"
        LifoReportExporter(out).export([_make_partial_lot()])
        rows = _read_csv(out)
        row = rows[1]
        assert row[HEADERS.index("status")]      == "PARTIAL"
        assert row[HEADERS.index("open_qty")]    == "6.0"
        assert row[HEADERS.index("matched_qty")] == "4.0"

    def test_none_exit_price_written_as_empty_string(self, tmp_path: pathlib.Path) -> None:
        out = tmp_path / "out.csv"
        LifoReportExporter(out).export([_make_open_lot()])
        rows = _read_csv(out)
        assert rows[1][HEADERS.index("exit_price")] == ""

    def test_overwrites_existing_file(self, tmp_path: pathlib.Path) -> None:
        out = tmp_path / "out.csv"
        exporter = LifoReportExporter(out)
        exporter.export([_make_open_lot(), _make_closed_lot()])
        exporter.export([_make_closed_lot()])  # only 1 record now
        rows = _read_csv(out)
        assert len(rows) == 2  # header + 1

    def test_returns_output_path(self, tmp_path: pathlib.Path) -> None:
        out = tmp_path / "out.csv"
        result = LifoReportExporter(out).export([_make_open_lot()])
        assert result == out

    def test_entry_date_written_correctly(self, tmp_path: pathlib.Path) -> None:
        out = tmp_path / "out.csv"
        LifoReportExporter(out).export([_make_closed_lot()])
        rows = _read_csv(out)
        assert rows[1][HEADERS.index("entry_date")] == "2024-01-01"

    def test_exit_date_written_correctly(self, tmp_path: pathlib.Path) -> None:
        out = tmp_path / "out.csv"
        LifoReportExporter(out).export([_make_closed_lot()])
        rows = _read_csv(out)
        assert rows[1][HEADERS.index("exit_date")] == "2024-01-02"
