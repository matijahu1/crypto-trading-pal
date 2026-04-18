"""
services/lifo_report.py — LIFO inventory and realized PnL calculator.

Reads an existing ``{SYMBOL}_orderHistory.csv`` (produced by the
``order_history`` action) and processes it into a unified lot report:

* Open lots     — Buy orders that have never been matched by a Sell.
* Partial lots  — Buy orders that have been partially consumed by Sell(s).
* Closed lots   — Buy orders fully consumed by one or more Sells.

Algorithm (LIFO — Last In, First Out):
  1. Load all orders from the CSV and sort chronologically (oldest first,
     by ``updated_ts``).
  2. Maintain a stack of Lot objects; each represents one Buy order.
  3. For every Sell order, pop from the top of the stack and match
     quantities until the sell is fully consumed.
  4. Realized PnL per match = (exit_price - entry_price) × matched_qty.

The result is a list of LotRecord dataclasses ready for CSV export.

Public surface:
  LotRecord           — row in the output CSV
  LifoReportService   — loads input CSV → runs LIFO → returns list[LotRecord]
"""

from __future__ import annotations

import csv
import logging
import pathlib
from dataclasses import dataclass
from typing import Optional

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Output row dataclass
# ---------------------------------------------------------------------------


@dataclass
class LotRecord:
    """One row in the LIFO inventory CSV output."""

    # Timing
    entry_date: str  # updated_date of the Buy order
    exit_date: str  # updated_date of the last matching Sell, or ""

    # Quantities
    total_qty: float  # original Buy qty
    matched_qty: float  # qty matched against Sell(s) so far
    open_qty: float  # remaining unmatched qty

    # Status
    status: str  # "OPEN" | "PARTIAL" | "CLOSED"

    # Prices
    entry_price: float  # Buy price
    exit_price: Optional[float]  # weighted-average exit price, or None

    # PnL
    realized_pnl: float  # cumulative realized PnL for this lot


# ---------------------------------------------------------------------------
# Internal stack item
# ---------------------------------------------------------------------------


@dataclass
class _Lot:
    """Working representation of one Buy order on the stack."""

    order_id: str
    entry_date: str
    entry_price: float
    total_qty: float
    open_qty: float  # starts equal to total_qty, decreases on match

    # Tracking for the output record
    matched_qty: float = 0.0
    realized_pnl: float = 0.0
    exit_date: str = ""
    exit_price_sum: float = 0.0  # accumulated (price × qty) for VWAP


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------


class LifoReportService:
    """
    Processes a ``{SYMBOL}_orderHistory.csv`` file using LIFO matching.

    Args:
        input_path: Full path to the order-history CSV.  Typically obtained
                    via ``PathProvider.order_history_path()``.
    """

    REQUIRED_COLUMNS = {
        "order_id",
        "side",
        "price",
        "qty",
        "updated_ts",
    }

    def __init__(self, input_path: str | pathlib.Path) -> None:
        self._input_path = pathlib.Path(input_path)

    # ------------------------------------------------------------------
    # Public
    # ------------------------------------------------------------------

    def generate(self) -> list[LotRecord]:
        """
        Load the order CSV, run LIFO matching, and return lot records.

        Returns:
            A list of LotRecord objects (all lots, open and closed),
            sorted by entry_date ascending.

        Raises:
            FileNotFoundError: propagated from _load(); caller (main.py)
                               is expected to catch and log this.
        """
        orders = self._load()
        if not orders:
            log.warning(
                "No orders loaded from %s — returning empty report", self._input_path
            )
            return []

        return self._run_lifo(orders)

    # ------------------------------------------------------------------
    # Private — loading
    # ------------------------------------------------------------------

    def _load(self) -> list[dict]:
        """
        Read the CSV and return rows sorted chronologically (oldest first).

        Raises:
            FileNotFoundError: if the input CSV does not exist.
            ValueError:        if required columns are missing.
        """
        if not self._input_path.exists():
            raise FileNotFoundError(f"Order history file not found: {self._input_path}")

        with self._input_path.open(newline="", encoding="utf-8") as fh:
            reader = csv.DictReader(fh)
            rows = list(reader)

        if not rows:
            return []

        # Validate columns
        actual = set(rows[0].keys())
        missing = self.REQUIRED_COLUMNS - actual
        if missing:
            raise ValueError(
                f"Input CSV is missing required columns: {missing}. Found: {actual}"
            )

        # Sort chronologically — oldest updated_ts first
        rows.sort(key=lambda r: int(r["updated_ts"]) if r["updated_ts"] else 0)
        return rows

    # ------------------------------------------------------------------
    # Private — LIFO engine
    # ------------------------------------------------------------------

    def _run_lifo(self, orders: list[dict]) -> list[LotRecord]:
        """
        Core LIFO matching loop.

        Buy orders push onto the stack.
        Sell orders consume from the top of the stack (newest first).
        """
        stack: list[_Lot] = []  # index -1 is the newest (LIFO top)
        closed: list[_Lot] = []  # fully consumed lots

        for row in orders:
            side = row.get("side", "").strip()
            price = _to_float(row.get("price", "0"))
            qty = _to_float(row.get("qty", "0"))
            date = row.get("updated_date", "")

            if side == "Buy":
                stack.append(
                    _Lot(
                        order_id=row.get("order_id", ""),
                        entry_date=date,
                        entry_price=price,
                        total_qty=qty,
                        open_qty=qty,
                    )
                )

            elif side == "Sell":
                remaining_sell = qty

                # Consume from the top of the stack (LIFO)
                while remaining_sell > 0 and stack:
                    lot = stack[-1]

                    matched = min(lot.open_qty, remaining_sell)
                    pnl = (price - lot.entry_price) * matched

                    lot.open_qty -= matched
                    lot.matched_qty += matched
                    lot.realized_pnl += pnl
                    lot.exit_date = date
                    lot.exit_price_sum += price * matched
                    remaining_sell -= matched

                    if lot.open_qty <= 0:
                        closed.append(stack.pop())

                if remaining_sell > 0:
                    log.warning(
                        "Sell of %.4f could not be fully matched "
                        "(%.4f unmatched) — no remaining Buy lots on stack.",
                        qty,
                        remaining_sell,
                    )

            else:
                log.debug("Skipping row with side=%r", side)

        # Whatever remains on the stack is open / partially matched
        remaining = list(stack)  # oldest first (stack bottom → top)

        all_lots = closed + remaining

        # Build output records
        records: list[LotRecord] = []
        for lot in all_lots:
            if lot.matched_qty == 0:
                status = "OPEN"
            elif lot.open_qty <= 0:
                status = "CLOSED"
            else:
                status = "PARTIAL"

            if lot.matched_qty > 0:
                exit_price: Optional[float] = round(
                    lot.exit_price_sum / lot.matched_qty, 8
                )
            else:
                exit_price = None

            records.append(
                LotRecord(
                    entry_date=lot.entry_date,
                    exit_date=lot.exit_date,
                    total_qty=lot.total_qty,
                    matched_qty=lot.matched_qty,
                    open_qty=max(0.0, lot.open_qty),
                    status=status,
                    entry_price=lot.entry_price,
                    exit_price=exit_price,
                    realized_pnl=round(lot.realized_pnl, 8),
                )
            )

        # Sort by entry_date ascending for the final output
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
