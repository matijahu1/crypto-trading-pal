"""
services/trade_history.py — service layer for futures trade (execution) history.

Responsibilities:
  - Split the lookback period into 7-day windows (Bybit API limit)
  - Fetch each window separately, paging within it until results are exhausted
  - Map API field names to clean internal names
  - Convert millisecond timestamps to human-readable date and time strings
  - Return structured result objects — NOT formatted strings
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
Total number of calendar days of trade history to fetch.
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

class TradeHistoryClientProtocol(Protocol):
    """Minimal interface the service needs from an exchange API client."""

    def get_trade_history(
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
class Trade:
    """A single executed trade (fill)."""

    trade_id: str    # unique execution ID
    symbol: str
    side: str        # "Buy" or "Sell"
    price: float     # execution price
    size: float      # executed quantity
    exec_type: str   # execution type, e.g. "Trade", "Funding", "BustTrade"
    date: str        # UTC date of execution, e.g. "2023-11-14"
    time: str        # UTC time of execution, e.g. "22:13:20"


@dataclass
class TradeHistory:
    """A batch of trades for one symbol."""

    symbol: str
    category: str
    trades: list[Trade]   # newest-first across the full lookback period


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------

class TradeHistoryService:
    """
    Fetches execution history for a single futures contract.

    Time-window strategy
    --------------------
    Bybit limits each request to a 7-day window (startTime..endTime).
    To cover the full lookback period we divide it into fixed 7-day
    slices and iterate backwards from now:

        global_start = now - lookback_days
        end_time     = now

        while end_time > global_start:
            start_time = max(end_time - 7 days, global_start)
            fetch all pages within [start_time, end_time]   ← inner loop
            end_time = start_time - 1                       ← advance window

    Inner-loop paging (within one 7-day window)
    --------------------------------------------
    Each API call returns up to `limit` records newest-first.
    After each page we move the upper boundary down to just below the oldest
    record on that page.  We stop the inner loop when a page is empty —
    meaning there are no more records in this window.

    Duplicate guard
    ---------------
    A set of seen execIds prevents duplicates if the same trade appears in
    two consecutive windows (e.g. at an exact window boundary).
    """

    def __init__(
        self,
        client: TradeHistoryClientProtocol,
        category: str = "linear",
        limit: int = 100,
        lookback_days: int = LOOKBACK_DAYS,
    ) -> None:
        """
        Args:
            client:        API client satisfying TradeHistoryClientProtocol.
            category:      Bybit instrument category ("linear" or "inverse").
            limit:         Max records per API call (Bybit maximum: 100).
            lookback_days: Default number of calendar days to look back.
        """
        self._client = client
        self._category = category
        self._limit = limit
        self._lookback_days = lookback_days

    def get_history(
        self,
        symbol: str,
        lookback_days: int | None = None,
        start_time_ms: int | None = None,
    ) -> TradeHistory:
        """
        Return all trade executions for *symbol* within the requested range.

        Args:
            symbol:        Perpetual futures symbol, e.g. "ZECUSDT".
                           Uppercase is enforced automatically.
            lookback_days: If provided, overrides the instance-level default.
                           Ignored when start_time_ms is set.
            start_time_ms: Explicit UTC millisecond timestamp for the start of
                           the fetch window.  Takes priority over lookback_days.

        Parameter priority for global_start:
            1. start_time_ms  — used directly when provided.
            2. lookback_days  — applied from now when provided.
            3. self._lookback_days — the constructor default.

        Returns:
            TradeHistory dataclass. Trades are accumulated newest-first.
            The list may be empty if there are no executions in the period.

        Raises:
            BybitAPIError: Propagated from the client on network / API errors.
        """
        symbol = symbol.upper()

        now_ms   = _now_ms()
        end_time = now_ms

        if start_time_ms is not None:
            global_start = start_time_ms
        elif lookback_days is not None:
            global_start = now_ms - lookback_days * _MS_PER_DAY
        else:
            global_start = now_ms - self._lookback_days * _MS_PER_DAY

        log.debug(
            "Trade history fetch started: symbol=%s global_start=%d now=%d",
            symbol, global_start, now_ms,
        )

        all_trades: list[Trade] = []
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
            window_end = end_time  # slides down as we page through results

            while True:
                page = self._client.get_trade_history(
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

                oldest_in_page: int = window_end   # overwritten below

                for entry in page:
                    exec_id   = entry.get("execId", "")
                    exec_time = int(entry.get("execTime", 0) or 0)

                    if exec_time < oldest_in_page:
                        oldest_in_page = exec_time

                    # Deduplicate across window boundaries
                    if exec_id and exec_id in seen_ids:
                        continue
                    if exec_id:
                        seen_ids.add(exec_id)

                    all_trades.append(Trade(
                        trade_id=exec_id,
                        symbol=entry.get("symbol", symbol),
                        side=entry.get("side", ""),
                        price=float(entry.get("execPrice", 0) or 0),
                        size=float(entry.get("execQty", 0) or 0),
                        exec_type=entry.get("execType", ""),
                        date=ms_timestamp_to_date_time(str(exec_time))[0] if exec_time else "",
                        time=ms_timestamp_to_date_time(str(exec_time))[1] if exec_time else "",
                    ))

                log.debug(
                    "    Page: %d record(s), oldest=%d, total so far=%d",
                    len(page), oldest_in_page, len(all_trades),
                )

                # Slide the upper boundary down for the next inner-loop page
                window_end = oldest_in_page - 1

                # Safety: stop if paging would go before this window's start
                if window_end < start_time:
                    break

            # Advance outer window: next window ends just before this one started
            end_time = start_time - 1

        log.info(
            "Trade history complete: %d trade(s) for %s",
            len(all_trades), symbol,
        )

        return TradeHistory(
            symbol=symbol,
            category=self._category,
            trades=all_trades,
        )


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------

def _now_ms() -> int:
    """Return the current UTC time as a Unix millisecond timestamp."""
    return int(datetime.datetime.now(tz=datetime.timezone.utc).timestamp() * 1000)
