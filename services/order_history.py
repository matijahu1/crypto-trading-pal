"""
services/order_history.py — service layer for futures order history.

Responsibilities:
  - Split the lookback period into 7-day windows (Bybit API limit)
  - Fetch each window separately, paging within it until results are exhausted
  - Filter server-side for a specific order status (default: "Filled")
  - Map API field names to clean internal names
  - Preserve original API timestamps alongside derived date/time strings
  - Sort the final result by updatedTime DESC (newest first)
  - Return structured result objects — NOT formatted strings

Change log:
  - get_history() gains an ``order_status`` parameter (default ``"Filled"``).
    The value is forwarded to the API so only matching orders are returned,
    reducing response size and removing the need for client-side filtering.
    Pass ``order_status=None`` to fetch all statuses (backwards compatible).
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

MAX_WINDOW_DAYS = 7
"""
Maximum days per single API request.
Bybit rejects requests where endTime - startTime > 7 days.
Do not change this value.
"""

MS_PER_DAY = 24 * 60 * 60 * 1_000

_LOOKBACK_DAYS_FALLBACK = 30

# Public alias preserved for test imports.
LOOKBACK_DAYS = _LOOKBACK_DAYS_FALLBACK


# ---------------------------------------------------------------------------
# Protocol
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
        order_status: str | None = None,
    ) -> list[dict[str, Any]]: ...


# ---------------------------------------------------------------------------
# Result dataclasses
# ---------------------------------------------------------------------------

@dataclass
class Order:
    """A single historical order."""

    order_id:     str
    symbol:       str
    side:         str    # "Buy" or "Sell"
    order_type:   str    # "Market" or "Limit"
    price:        float  # 0.0 for Market orders
    qty:          float
    order_status: str    # e.g. "Filled", "Cancelled", "PartiallyFilled"
    created_ts:   str    # raw createdTime from API, e.g. "1700000000000"
    updated_ts:   str    # raw updatedTime from API, e.g. "1700000060000"
    created_date: str    # UTC date of order creation, e.g. "2023-11-14"
    created_time: str    # UTC time of order creation, e.g. "22:13:20"
    updated_date: str    # UTC date of last status change
    updated_time: str    # UTC time of last status change


@dataclass
class OrderHistory:
    """A batch of orders for one symbol."""

    symbol:   str
    category: str
    orders:   list[Order]   # sorted by updatedTime DESC


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------

class OrderHistoryService:
    """
    Fetches order history for a single futures contract.

    Time-window strategy
    --------------------
    Bybit limits each request to a 7-day window (startTime..endTime),
    filtering by createdTime.  To cover the full lookback period we
    divide it into fixed 7-day slices and iterate backwards from now:

        global_start = now - lookback_days
        end_time     = now

        while end_time > global_start:
            start_time = max(end_time - 7 days, global_start)
            fetch all pages within [start_time, end_time]   ← inner loop
            end_time = start_time - 1                       ← advance window

    Inner-loop paging
    -----------------
    Each API call returns up to `limit` records newest-first by createdTime.
    After each page we move the upper boundary down to just below the oldest
    createdTime on that page.  We stop when a page is empty.

    Duplicate guard
    ---------------
    A set of seen orderIds prevents duplicates at window boundaries.
    """

    def __init__(
        self,
        client: OrderHistoryClientProtocol,
        category:      str = "linear",
        limit:         int = 50,
        lookback_days: int = _LOOKBACK_DAYS_FALLBACK,
    ) -> None:
        self._client        = client
        self._category      = category
        self._limit         = limit
        self._lookback_days = lookback_days

    def get_history(
        self,
        symbol:        str,
        lookback_days: int | None = None,
        start_time_ms: int | None = None,
        order_status:  str | None = "Filled",
    ) -> OrderHistory:
        """
        Return orders for *symbol* within the requested time range.

        Args:
            symbol:        Perpetual futures symbol, e.g. "CCUSDT".
                           Uppercase is enforced automatically.
            lookback_days: Overrides the instance-level default when provided.
                           Ignored when start_time_ms is set.
            start_time_ms: Explicit UTC millisecond timestamp for the global
                           start of the fetch window.  Takes priority over
                           lookback_days.
            order_status:  Server-side filter forwarded to the Bybit API.
                           Default ``"Filled"`` — only fully executed orders.
                           Pass ``None`` to retrieve all statuses.

        Parameter priority for global_start:
            1. start_time_ms  — used directly when provided.
            2. lookback_days  — applied from now when provided.
            3. self._lookback_days — the constructor default.

        Returns:
            OrderHistory with orders sorted by updatedTime DESC.
            The list may be empty if there are no matching orders.

        Raises:
            BybitAPIError: Propagated from the client on network / API errors.
        """
        symbol = symbol.upper()

        now_ms   = _now_ms()
        end_time = now_ms

        if start_time_ms is not None:
            global_start = start_time_ms
        elif lookback_days is not None:
            global_start = now_ms - lookback_days * MS_PER_DAY
        else:
            global_start = now_ms - self._lookback_days * MS_PER_DAY

        log.debug(
            "Order history fetch: symbol=%s status=%s global_start=%d now=%d",
            symbol, order_status or "ALL", global_start, now_ms,
        )

        all_orders: list[Order] = []
        seen_ids:   set[str]   = set()

        # ── Outer loop: iterate backwards through 7-day windows ──────────────
        while end_time > global_start:

            start_time = max(
                end_time - MAX_WINDOW_DAYS * MS_PER_DAY,
                global_start,
            )

            log.debug(
                "  Window: start=%d  end=%d  (%.1f days)",
                start_time, end_time,
                (end_time - start_time) / MS_PER_DAY,
            )

            window_end = end_time

            # ── Inner loop: page through this 7-day window ───────────────────
            while True:
                page = self._client.get_order_history(
                    symbol=symbol,
                    category=self._category,
                    limit=self._limit,
                    start_time=start_time,
                    end_time=window_end,
                    order_status=order_status,      # ← server-side filter
                )

                if not page:
                    log.debug("    Empty page — window exhausted")
                    break

                oldest_created: int = window_end

                for entry in page:
                    order_id   = entry.get("orderId", "")
                    created_ts = entry.get("createdTime", "")
                    updated_ts = entry.get("updatedTime", "")
                    created_ms = int(created_ts or 0)

                    if created_ms < oldest_created:
                        oldest_created = created_ms

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
                        created_ts=created_ts,
                        updated_ts=updated_ts,
                        created_date=ms_timestamp_to_date_time(created_ts)[0],
                        created_time=ms_timestamp_to_date_time(created_ts)[1],
                        updated_date=ms_timestamp_to_date_time(updated_ts)[0],
                        updated_time=ms_timestamp_to_date_time(updated_ts)[1],
                    ))

                log.debug(
                    "    Page: %d record(s), oldest createdTime=%d, total so far=%d",
                    len(page), oldest_created, len(all_orders),
                )

                window_end = oldest_created - 1

                if window_end < start_time:
                    break

            end_time = start_time - 1

        all_orders.sort(
            key=lambda o: int(o.updated_ts) if o.updated_ts else 0,
            reverse=True,
        )

        log.info(
            "Order history complete: %d order(s) for %s (status=%s)",
            len(all_orders), symbol, order_status or "ALL",
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
