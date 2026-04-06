"""
services/order_history.py — service layer for futures order history.

Responsibilities:
  - Split the lookback period into 7-day windows (Bybit API limit)
  - Fetch each window separately, paging within it until results are exhausted
  - Map API field names to clean internal names
  - Convert millisecond timestamps to human-readable date and time strings
  - Return structured result objects — NOT formatted strings

Uses the same two-level loop strategy as TradeHistoryService.
"""

from __future__ import annotations

import datetime
import logging
from dataclasses import dataclass
from typing import Any, Protocol

from utils.time_utils import ms_timestamp_to_date_time

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Time-window constants
# ---------------------------------------------------------------------------

LOOKBACK_DAYS = 30
"""
Total number of calendar days of order history to fetch.
Change this value to widen or narrow the lookback period.
"""

MAX_WINDOW_DAYS = 7
"""
Maximum days per single API request.
Bybit rejects requests where endTime - startTime > 7 days.
Do not change this value.
"""

_MS_PER_DAY = 24 * 60 * 60 * 1_000


# ---------------------------------------------------------------------------
# Protocol — decouples the service from the concrete client (mockable in tests)
# ---------------------------------------------------------------------------

class OrderHistoryClientProtocol(Protocol):
    """Minimal interface the service needs from an exchange API client."""

    def get_order_history(
        self,
        symbol: str,
        category: str,
        limit: int,
        start_time: int | None = None,
        end_time: int | None = None,
    ) -> list[dict[str, Any]]: ...


# ---------------------------------------------------------------------------
# Result dataclasses
# ---------------------------------------------------------------------------

@dataclass
class Order:
    """A single historical order."""

    order_id: str       # unique order ID
    symbol: str
    side: str           # "Buy" or "Sell"
    order_type: str     # "Market" or "Limit"
    price: float        # order price (0.0 for Market orders)
    qty: float          # order quantity
    order_status: str   # e.g. "Filled", "Cancelled", "PartiallyFilled"
    created_date: str   # UTC date of order creation, e.g. "2023-11-14"
    created_time: str   # UTC time of order creation, e.g. "22:13:20"
    updated_date: str   # UTC date of last status change
    updated_time: str   # UTC time of last status change


@dataclass
class OrderHistory:
    """A batch of orders for one symbol."""

    symbol: str
    category: str
    orders: list[Order]   # newest-first across the full lookback period


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------

class OrderHistoryService:
    """
    Fetches order history for a single futures contract.

    Time-window strategy
    --------------------
    Bybit limits each request to a 7-day window (startTime..endTime),
    filtering by createdTime.  To cover the full LOOKBACK_DAYS period we
    divide it into fixed 7-day slices and iterate backwards from now:

        global_start = now - LOOKBACK_DAYS
        end_time     = now

        while end_time > global_start:
            start_time = max(end_time - 7 days, global_start)
            fetch all pages within [start_time, end_time]   ← inner loop
            end_time = start_time - 1                       ← advance window

    Inner-loop paging (within one 7-day window)
    --------------------------------------------
    Each API call returns up to `limit` records newest-first by createdTime.
    After each page we move the upper boundary down to just below the oldest
    createdTime on that page.  We stop the inner loop when a page is empty —
    meaning there are no more records in this window.

    Empty-window handling
    ---------------------
    An empty page ends only the inner loop for that window.  The outer loop
    always continues, because gaps in order activity are expected and a
    window with no orders should not abort the full fetch.

    Duplicate guard
    ---------------
    A set of seen orderIds prevents duplicates when the same order appears in
    two consecutive windows (e.g. at a window boundary).
    """

    def __init__(
        self,
        client: OrderHistoryClientProtocol,
        category: str = "linear",
        limit: int = 50,
    ) -> None:
        """
        Args:
            client:   API client satisfying OrderHistoryClientProtocol.
            category: Bybit instrument category ("linear" or "inverse").
            limit:    Max orders per API call (Bybit maximum: 50).
        """
        self._client = client
        self._category = category
        self._limit = limit

    def get_history(self, symbol: str) -> OrderHistory:
        """
        Return all orders for *symbol* within the last LOOKBACK_DAYS.

        Args:
            symbol: Perpetual futures symbol, e.g. "ZECUSDT".
                    Uppercase is enforced automatically.

        Returns:
            OrderHistory dataclass. Orders are accumulated newest-first.
            The list may be empty if there are no orders in the period.

        Raises:
            BybitAPIError: Propagated from the client on network / API errors.
        """
        symbol = symbol.upper()

        now_ms       = _now_ms()
        global_start = now_ms - LOOKBACK_DAYS * _MS_PER_DAY
        end_time     = now_ms

        log.debug(
            "Order history fetch started: symbol=%s lookback=%dd "
            "global_start=%d now=%d",
            symbol, LOOKBACK_DAYS, global_start, now_ms,
        )

        all_orders: list[Order] = []
        seen_ids:   set[str]   = set()

        # ── Outer loop: iterate backwards through 7-day windows ──────────────
        while end_time > global_start:

            start_time = max(end_time - MAX_WINDOW_DAYS * _MS_PER_DAY,
                             global_start)

            log.debug(
                "  Window: start=%d  end=%d  (%.1f days)",
                start_time, end_time,
                (end_time - start_time) / _MS_PER_DAY,
            )

            # ── Inner loop: page through this 7-day window ───────────────────
            window_end = end_time   # slides down as we page through results

            while True:
                page = self._client.get_order_history(
                    symbol=symbol,
                    category=self._category,
                    limit=self._limit,
                    start_time=start_time,
                    end_time=window_end,
                )

                if not page:
                    # No more records in this window — move to the next one
                    log.debug("    Empty page — window exhausted")
                    break

                oldest_created: int = window_end   # overwritten below

                for entry in page:
                    order_id     = entry.get("orderId", "")
                    created_time = int(entry.get("createdTime", 0) or 0)

                    if created_time < oldest_created:
                        oldest_created = created_time

                    # Deduplicate across window boundaries
                    if order_id and order_id in seen_ids:
                        continue
                    if order_id:
                        seen_ids.add(order_id)

                    all_orders.append(Order(
                        order_id=order_id,
                        symbol=entry.get("symbol", symbol),
                        side=entry.get("side", ""),
                        order_type=entry.get("orderType", ""),
                        price=float(entry.get("price", 0) or 0),
                        qty=float(entry.get("qty", 0) or 0),
                        order_status=entry.get("orderStatus", ""),
                        created_date=ms_timestamp_to_date_time(str(created_time))[0] if created_time else "",
                        created_time=ms_timestamp_to_date_time(str(created_time))[1] if created_time else "",
                        updated_date=ms_timestamp_to_date_time(entry.get("updatedTime", ""))[0],
                        updated_time=ms_timestamp_to_date_time(entry.get("updatedTime", ""))[1],
                    ))

                log.debug(
                    "    Page: %d record(s), oldest createdTime=%d, total so far=%d",
                    len(page), oldest_created, len(all_orders),
                )

                # Slide the upper boundary down for the next inner-loop page
                window_end = oldest_created - 1

                # Safety: stop if paging would go before this window's start
                if window_end < start_time:
                    break

            # Advance outer window: next window ends just before this one started
            end_time = start_time - 1

        log.info(
            "Order history complete: %d order(s) over %d day(s) for %s",
            len(all_orders), LOOKBACK_DAYS, symbol,
        )

        return OrderHistory(
            symbol=symbol,
            category=self._category,
            orders=all_orders,
        )


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------

def _now_ms() -> int:
    """Return the current UTC time as a Unix millisecond timestamp."""
    return int(datetime.datetime.now(tz=datetime.timezone.utc).timestamp() * 1000)
