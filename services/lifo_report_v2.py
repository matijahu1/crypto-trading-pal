"""
services/lifo_report_v2.py — LIFO inventory and realized PnL calculator
based on trade-level fill data.

Reads an existing ``{SYMBOL}_tradeType_Trade.csv`` (produced by the
``get_trade_type_trade`` action) and produces the same lot report as
``LifoReportService``, with one meaningful enhancement: because trade-level
data carries a ``trading_fee`` per fill, fees are tracked per lot and the
output includes ``total_fees`` and ``net_pnl``.

Differences from services/lifo_report.py (v1)
----------------------------------------------
  Source file     : {SYMBOL}_tradeType_Trade.csv  (was: orderHistory.csv)
  ID column       : trade_id                       (was: order_id)
  Quantity column : size                           (was: qty)
  Sort key        : "date time" ISO string         (was: updated_ts integer ms)
  Date column     : date                           (was: updated_date)
  Extra output    : total_fees, net_pnl            (fees unavailable in v1)

Algorithm (LIFO — Last In, First Out):
  1. Load all trade rows from the CSV; sort chronologically oldest-first by
     (date, time).  ISO strings "YYYY-MM-DD" + "HH:MM:SS" sort correctly.
  2. Maintain a stack of _Lot objects; each represents one Buy fill.
  3. For every Sell fill, pop from the top of the stack and match quantities
     until the sell is fully consumed.
  4. Realized PnL per match = (exit_price − entry_price) × matched_qty.
  5. Fees: the Buy trade's fee is stored at push time.  Each Sell's fee is
     distributed proportionally across the lots it matches:
       lot_sell_fee += sell_fee × (matched_qty / total_sell_qty)
  6. net_pnl = realized_pnl − total_fees  (buy_fee + accumulated sell_fees).

Public surface:
  LotRecordV2         — row in the output CSV (same as v1 + total_fees, net_pnl)
  LifoReportV2Service — loads CSV → runs LIFO → returns list[LotRecordV2]
"""

from __future__ import annotations

import csv
import logging
import pathlib
from dataclasses import dataclass, field
from typing import Optional

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Output row dataclass
# ---------------------------------------------------------------------------


@dataclass
class LotRecordV2:
    """One row in the LIFO v2 report CSV."""

    # Timing
    entry_date: str          # date of the Buy trade
    exit_date: str           # date of the last matching Sell, or ""

    # Quantities
    total_qty: float         # original Buy size
    matched_qty: float       # qty matched against Sell(s) so far
    open_qty: float          # remaining unmatched qty

    # Status
    status: str              # "OPEN" | "PARTIAL" | "CLOSED"

    # Prices
    entry_price: float       # Buy execution price
    exit_price: Optional[float]  # VWAP across all matching Sells, or None

    # PnL
    realized_pnl: float      # cumulative gross realized PnL for this lot

    # Fees (new in v2 — not available from order-history data)
    total_fees: float        # buy_fee + proportional sell fees for this lot
    net_pnl: float           # realized_pnl − total_fees


# ---------------------------------------------------------------------------
# Internal stack item
# ---------------------------------------------------------------------------


@dataclass
class _Lot:
    """Working representation of one Buy trade on the LIFO stack."""

    trade_id: str
    entry_date: str
    entry_price: float
    total_qty: float
    open_qty: float          # starts equal to total_qty; decreases on match

    # Accumulated during matching
    matched_qty: float = 0.0
    realized_pnl: float = 0.0
    exit_date: str = ""
    exit_price_sum: float = 0.0   # accumulated (price × qty) for VWAP

    # Fee tracking (new in v2)
    buy_fee: float = 0.0          # fee from the original Buy trade
    sell_fees: float = 0.0        # accumulated proportional sell fees

    @property
    def total_fees(self) -> float:
        return self.buy_fee + self.sell_fees


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------


class LifoReportV2Service:
    """
    Processes a ``{SYMBOL}_tradeType_Trade.csv`` file using LIFO matching.

    Compared to LifoReportService (v1), this service works with trade-level
    fills rather than order-level records.  Because fills carry a per-trade
    fee, the output adds ``total_fees`` and ``net_pnl`` columns.

    Args:
        input_path: Full path to the tradeType_Trade CSV.  Typically
                    ``base_dir / f"{symbol}_tradeType_Trade.csv"``.
    """

    REQUIRED_COLUMNS = {
        "trade_id",
        "side",
        "price",
        "size",
        "date",
        "time",
        "trading_fee",
    }

    def __init__(self, input_path: str | pathlib.Path) -> None:
        self._input_path = pathlib.Path(input_path)

    # ------------------------------------------------------------------
    # Public
    # ------------------------------------------------------------------

    def generate(self) -> list[LotRecordV2]:
        """
        Load the trade CSV, run LIFO matching, and return lot records.

        Returns:
            A list of LotRecordV2 objects (all lots, open and closed),
            sorted by entry_date ascending.

        Raises:
            FileNotFoundError: if the input CSV does not exist.
            ValueError:        if required columns are missing.
        """
        trades = self._load()
        if not trades:
            log.warning(
                "No trades loaded from %s — returning empty report", self._input_path
            )
            return []

        return self._run_lifo(trades)

    # ------------------------------------------------------------------
    # Private — loading
    # ------------------------------------------------------------------

    def _load(self) -> list[dict]:
        """
        Read the CSV and return rows sorted chronologically (oldest first).

        Sort key: "date time" (e.g. "2025-04-27 06:58:16").  ISO-format
        strings sort lexicographically in the same order as chronologically,
        so no timestamp conversion is necessary.

        Raises:
            FileNotFoundError: if the input CSV does not exist.
            ValueError:        if required columns are missing.
        """
        if not self._input_path.exists():
            raise FileNotFoundError(
                f"Trade history file not found: {self._input_path}\n"
                "Run 'get_trade_type_trade' first to generate it."
            )

        with self._input_path.open(newline="", encoding="utf-8") as fh:
            reader = csv.DictReader(fh)
            rows = list(reader)

        if not rows:
            return []

        actual = set(rows[0].keys())
        missing = self.REQUIRED_COLUMNS - actual
        if missing:
            raise ValueError(
                f"Input CSV is missing required columns: {missing}. Found: {actual}"
            )

        # Sort oldest-first by (date, time) — lexicographic = chronological
        rows.sort(key=lambda r: (r.get("date", ""), r.get("time", "")))
        return rows

    # ------------------------------------------------------------------
    # Private — LIFO engine
    # ------------------------------------------------------------------

    def _run_lifo(self, trades: list[dict]) -> list[LotRecordV2]:
        """
        Core LIFO matching loop.

        Buy trades push a _Lot onto the stack.
        Sell trades consume from the top of the stack (newest Buy first).

        Fee distribution for Sells
        --------------------------
        A single Sell fill may be matched against multiple lots (when it
        exhausts the top lot and continues into the next).  Each lot
        receives a share of the Sell's fee proportional to the quantity
        it absorbed:
            lot.sell_fees += sell_fee × (matched_qty / total_sell_qty)
        """
        stack:  list[_Lot] = []   # index -1 is the newest Buy (LIFO top)
        closed: list[_Lot] = []   # fully consumed lots

        for row in trades:
            side        = row.get("side", "").strip()
            price       = _to_float(row.get("price", "0"))
            size        = _to_float(row.get("size", "0"))
            date        = row.get("date", "")
            trading_fee = _to_float(row.get("trading_fee", "0"))

            if side == "Buy":
                stack.append(
                    _Lot(
                        trade_id=row.get("trade_id", ""),
                        entry_date=date,
                        entry_price=price,
                        total_qty=size,
                        open_qty=size,
                        buy_fee=trading_fee,
                    )
                )

            elif side == "Sell":
                remaining_sell = size

                while remaining_sell > 0 and stack:
                    lot = stack[-1]

                    matched = min(lot.open_qty, remaining_sell)
                    pnl     = (price - lot.entry_price) * matched

                    # Proportional share of this sell's fee for this lot
                    proportional_sell_fee = trading_fee * (matched / size)

                    lot.open_qty    -= matched
                    lot.matched_qty += matched
                    lot.realized_pnl += pnl
                    lot.exit_date    = date
                    lot.exit_price_sum += price * matched
                    lot.sell_fees   += proportional_sell_fee
                    remaining_sell  -= matched

                    if lot.open_qty <= 0:
                        closed.append(stack.pop())

                if remaining_sell > 0:
                    log.warning(
                        "Sell of %.4f (trade_id=%s) could not be fully matched "
                        "(%.4f unmatched) — no remaining Buy lots on stack.",
                        size,
                        row.get("trade_id", "?"),
                        remaining_sell,
                    )

            else:
                log.debug("Skipping trade with side=%r (trade_id=%s)", side, row.get("trade_id", "?"))

        # Whatever is still on the stack is open or partially matched
        all_lots = closed + list(stack)   # closed first, then remaining open

        records: list[LotRecordV2] = []
        for lot in all_lots:
            if lot.matched_qty == 0:
                status = "OPEN"
            elif lot.open_qty <= 0:
                status = "CLOSED"
            else:
                status = "PARTIAL"

            exit_price: Optional[float] = (
                round(lot.exit_price_sum / lot.matched_qty, 8)
                if lot.matched_qty > 0
                else None
            )

            gross_pnl  = round(lot.realized_pnl, 8)
            total_fees = round(lot.total_fees, 8)

            records.append(
                LotRecordV2(
                    entry_date=lot.entry_date,
                    exit_date=lot.exit_date,
                    total_qty=lot.total_qty,
                    matched_qty=lot.matched_qty,
                    open_qty=max(0.0, lot.open_qty),
                    status=status,
                    entry_price=lot.entry_price,
                    exit_price=exit_price,
                    realized_pnl=gross_pnl,
                    total_fees=total_fees,
                    net_pnl=round(gross_pnl - total_fees, 8),
                )
            )

        records.sort(key=lambda r: r.entry_date)
        return records


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _to_float(value: str) -> float:
    try:
        return float(value)
    except (ValueError, TypeError):
        return 0.0
