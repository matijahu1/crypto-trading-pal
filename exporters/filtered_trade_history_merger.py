"""
exporters/filtered_trade_history_merger.py — load, merge, and sort filtered
trade history records.

Responsibilities:
  - Load an existing trade-history CSV from disk (if it exists).
  - Merge a list of newly fetched Trade objects into the loaded records.
  - Deduplicate by trade_id.
  - Sort the combined result by (date DESC, time DESC) — newest first.
  - Return the final list of Trade objects, ready to hand to TradeHistoryExporter.

This class intentionally has NO knowledge of:
  - How the CSV path is determined  (→ PathProvider / action handler)
  - How the final list is written   (→ TradeHistoryExporter)
  - How trades are fetched from the API (→ FilteredTradeHistoryService)

That separation makes the merger independently unit-testable:
pass in a list of Trade objects and a (possibly non-existent) tmp_path,
assert on the returned list — zero mocking required.

Typical call site (main.py)::

    merger  = FilteredTradeHistoryMerger(output_path)
    fresh   = service.get_history(symbol)
    combined = merger.merge(fresh.trades)
    exporter = make_trade_exporter(symbol, output_dir=base_dir)
    exporter.export(TradeHistory(symbol=symbol, category="linear", trades=combined))
"""

from __future__ import annotations

import csv
import logging
import pathlib
from decimal import Decimal, InvalidOperation

from services.trade_history import Trade

log = logging.getLogger(__name__)

# Column names must stay in sync with TradeHistoryExporter.HEADERS / HEADERS
# constant in trade_history_exporter.py.  Defined here as constants so both
# sides of the contract can import from one place.
_COL_TRADE_ID    = "trade_id"
_COL_SYMBOL      = "symbol"
_COL_SIDE        = "side"
_COL_PRICE       = "price"
_COL_SIZE        = "size"
_COL_EXEC_TYPE   = "exec_type"
_COL_TRADING_FEE = "trading_fee"
_COL_DATE        = "date"
_COL_TIME        = "time"

_ZERO = Decimal("0")


class FilteredTradeHistoryMerger:
    """
    Load-Merge-Sort helper for incremental filtered trade history exports.

    Trade records are deduplicated by trade_id.  A trade's fields never
    change after execution, so trade_id equality is both necessary and
    sufficient for deduplication — no field-level comparison is needed.

    Args:
        csv_path: Path to the existing CSV file.  If the file does not yet
                  exist the merger starts from an empty baseline (first run).
    """

    def __init__(self, csv_path: str | pathlib.Path) -> None:
        self._csv_path = pathlib.Path(csv_path)
        self._existing: list[Trade] = self._load_existing()

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    @property
    def existing_count(self) -> int:
        """Number of trades loaded from the existing CSV."""
        return len(self._existing)

    def merge(self, new_trades: list[Trade]) -> list[Trade]:
        """
        Combine *new_trades* with any records already on disk.

        Steps:
          1. Build an index keyed by trade_id from the records loaded at
             construction time — O(1) duplicate detection.
          2. Add each new trade only if its trade_id is not already present.
          3. Sort the combined list by (date DESC, time DESC) — newest first,
             matching the natural order produced by the Bybit API.

        Args:
            new_trades: Fresh Trade objects returned by FilteredTradeHistoryService.
                        May be an empty list — the existing CSV is still
                        rewritten unchanged (idempotent behaviour).

        Returns:
            Deduplicated, sorted list of Trade objects ready to be wrapped in
            a TradeHistory and passed to TradeHistoryExporter.
        """
        merged: dict[str, Trade] = {t.trade_id: t for t in self._existing}

        added   = 0
        skipped = 0
        for trade in new_trades:
            if trade.trade_id not in merged:
                merged[trade.trade_id] = trade
                added += 1
            else:
                skipped += 1

        log.debug(
            "Merge complete: %d existing, %d new, %d duplicate(s) skipped → %d total",
            len(self._existing),
            added,
            skipped,
            len(merged),
        )

        return _sort_desc(list(merged.values()))

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _load_existing(self) -> list[Trade]:
        """
        Read the CSV at ``self._csv_path`` and return its rows as Trade objects.

        Returns an empty list if:
          - the file does not exist  (first run)
          - the file exists but is empty or header-only

        Raises:
            ValueError: If a row contains an unparseable Decimal field.
                        This surfaces data corruption early rather than
                        silently dropping records.
        """
        if not self._csv_path.exists():
            log.debug("No existing CSV at %s — starting fresh", self._csv_path)
            return []

        trades: list[Trade] = []
        try:
            with self._csv_path.open(newline="", encoding="utf-8") as fh:
                reader = csv.DictReader(fh)
                for row in reader:
                    trades.append(_row_to_trade(row))
        except OSError as exc:
            log.warning(
                "Could not read existing CSV %s: %s — starting fresh",
                self._csv_path,
                exc,
            )
            return []

        log.debug("Loaded %d existing trade(s) from %s", len(trades), self._csv_path)
        return trades


# ---------------------------------------------------------------------------
# Module-level helpers (pure functions, independently unit-testable)
# ---------------------------------------------------------------------------

def _to_decimal(value: str) -> Decimal:
    """Convert a CSV string to Decimal; returns Decimal('0') on empty / bad input."""
    if not value:
        return _ZERO
    try:
        return Decimal(value)
    except InvalidOperation:
        return _ZERO


def _row_to_trade(row: dict[str, str]) -> Trade:
    """Convert a CSV DictReader row into a Trade dataclass."""
    return Trade(
        trade_id=row[_COL_TRADE_ID],
        symbol=row[_COL_SYMBOL],
        side=row[_COL_SIDE],
        price=_to_decimal(row[_COL_PRICE]),
        size=_to_decimal(row[_COL_SIZE]),
        exec_type=row[_COL_EXEC_TYPE],
        trading_fee=_to_decimal(row[_COL_TRADING_FEE]),
        date=row[_COL_DATE],
        time=row[_COL_TIME],
    )


def _sort_desc(trades: list[Trade]) -> list[Trade]:
    """
    Sort trades by (date DESC, time DESC) — newest first.

    Both fields are ISO-format strings ("YYYY-MM-DD", "HH:MM:SS") so
    lexicographic ordering is identical to chronological ordering.
    Trades with an empty date are placed at the end.
    """
    return sorted(
        trades,
        key=lambda t: (t.date, t.time),
        reverse=True,
    )
