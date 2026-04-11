"""
exporters/order_history_merger.py — load, merge, and sort order history records.

Responsibilities:
  - Load an existing order-history CSV from disk (if it exists).
  - Merge a list of newly fetched Order objects into the loaded records.
  - Deduplicate by order_id (safe for "Filled" orders: status never changes).
  - Sort the combined result by updated_ts DESC (newest first).
  - Return the final list of Order objects, ready to hand to OrderHistoryExporter.

This class intentionally has NO knowledge of:
  - How the CSV path is determined  (→ PathProvider)
  - How the final list is written   (→ OrderHistoryExporter)
  - How orders are fetched from the API (→ OrderHistoryService)

That separation makes the merger independently unit-testable:
pass in a list of Order objects and a (possibly non-existent) tmp_path,
assert on the returned list — zero mocking required.

Typical call site (main.py)::

    merger   = OrderHistoryMerger(output_path)
    combined = merger.merge(new_orders)
    exporter = OrderHistoryExporter(output_path)
    exporter.export(OrderHistory(symbol=symbol, category="linear", orders=combined))
"""

from __future__ import annotations

import csv
import logging
import pathlib

from services.order_history import Order

log = logging.getLogger(__name__)

# Column names must stay in sync with OrderHistoryExporter.HEADERS.
# They are defined here as constants so both sides of the contract
# can import from one place rather than duplicating the string literals.
_COL_ORDER_ID     = "order_id"
_COL_SYMBOL       = "symbol"
_COL_SIDE         = "side"
_COL_ORDER_TYPE   = "order_type"
_COL_PRICE        = "price"
_COL_QTY          = "qty"
_COL_ORDER_STATUS = "order_status"
_COL_CREATED_TS   = "created_ts"
_COL_UPDATED_TS   = "updated_ts"
_COL_CREATED_DATE = "created_date"
_COL_CREATED_TIME = "created_time"
_COL_UPDATED_DATE = "updated_date"
_COL_UPDATED_TIME = "updated_time"


class OrderHistoryMerger:
    """
    Load-Merge-Sort helper for incremental order history exports.

    Because "Filled" orders are immutable (their status and timestamps never
    change after reaching the Filled state), deduplication by order_id is both
    necessary and sufficient.  No field-level comparison is needed.

    Args:
        csv_path: Path to the existing CSV file.  If the file does not yet
                  exist, the merger starts from an empty baseline.
    """

    def __init__(self, csv_path: str | pathlib.Path) -> None:
        self._csv_path = pathlib.Path(csv_path)

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def merge(self, new_orders: list[Order]) -> list[Order]:
        """
        Combine *new_orders* with any records already on disk.

        Steps:
          1. Load existing rows from ``self._csv_path`` (empty list if file
             absent or empty).
          2. Build a dict keyed by order_id from existing records so that
             deduplication is O(1).
          3. Add each new order only if its order_id is not already present.
          4. Sort the combined list by updated_ts DESC.

        Args:
            new_orders: Fresh Order objects returned by OrderHistoryService.
                        May be an empty list — the existing CSV is still
                        rewritten unchanged (idempotent behaviour).

        Returns:
            Deduplicated, sorted list of Order objects.
            This list can be wrapped in an OrderHistory and passed directly
            to OrderHistoryExporter.
        """
        existing = self._load_existing()

        # Index existing orders by id for O(1) duplicate detection
        merged: dict[str, Order] = {o.order_id: o for o in existing}

        added   = 0
        skipped = 0
        for order in new_orders:
            if order.order_id not in merged:
                merged[order.order_id] = order
                added += 1
            else:
                skipped += 1

        log.debug(
            "Merge complete: %d existing, %d new, %d duplicate(s) skipped → %d total",
            len(existing), added, skipped, len(merged),
        )

        return _sort_desc(list(merged.values()))

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _load_existing(self) -> list[Order]:
        """
        Read the CSV at ``self._csv_path`` and return its rows as Order objects.

        Returns an empty list if:
          - the file does not exist  (first run)
          - the file exists but is empty or header-only

        Raises:
            ValueError: If a row contains an unparseable numeric field.
                        This surfaces data corruption early rather than
                        silently dropping records.
        """
        if not self._csv_path.exists():
            log.debug("No existing CSV at %s — starting fresh", self._csv_path)
            return []

        orders: list[Order] = []
        try:
            with self._csv_path.open(newline="", encoding="utf-8") as fh:
                reader = csv.DictReader(fh)
                for row in reader:
                    orders.append(_row_to_order(row))
        except OSError as exc:
            log.warning("Could not read existing CSV %s: %s — starting fresh", self._csv_path, exc)
            return []

        log.debug("Loaded %d existing order(s) from %s", len(orders), self._csv_path)
        return orders


# ---------------------------------------------------------------------------
# Module-level helpers (pure functions, easy to unit-test independently)
# ---------------------------------------------------------------------------

def _row_to_order(row: dict[str, str]) -> Order:
    """Convert a CSV DictReader row into an Order dataclass."""
    return Order(
        order_id=row[_COL_ORDER_ID],
        symbol=row[_COL_SYMBOL],
        side=row[_COL_SIDE],
        order_type=row[_COL_ORDER_TYPE],
        price=float(row[_COL_PRICE] or 0),
        qty=float(row[_COL_QTY] or 0),
        order_status=row[_COL_ORDER_STATUS],
        created_ts=row[_COL_CREATED_TS],
        updated_ts=row[_COL_UPDATED_TS],
        created_date=row[_COL_CREATED_DATE],
        created_time=row[_COL_CREATED_TIME],
        updated_date=row[_COL_UPDATED_DATE],
        updated_time=row[_COL_UPDATED_TIME],
    )


def _sort_desc(orders: list[Order]) -> list[Order]:
    """Sort orders by updated_ts descending (newest first)."""
    return sorted(
        orders,
        key=lambda o: int(o.updated_ts) if o.updated_ts else 0,
        reverse=True,
    )
