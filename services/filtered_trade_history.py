"""
services/filtered_trade_history.py — execType-filtered view of trade history.

Responsibilities:
  - Wrap TradeHistoryService to reuse all fetch, pagination, windowing,
    and deduplication logic (DRY — no duplication of that complexity here).
  - Post-filter the result set to keep only trades whose execType matches
    the configured value.
  - Return the same TradeHistory dataclass so the service is a drop-in
    replacement for TradeHistoryService at every call site.

Supported exec types (Bybit):
  "Trade"   — regular position fills
  "Funding" — periodic funding-rate settlements

Any other execType string is accepted; the filter is a plain equality check.

Usage:
    service = FilteredTradeHistoryService(client, exec_type="Trade", ...)
    history = service.get_history("ZECUSDT")

    service = FilteredTradeHistoryService(client, exec_type="Funding", ...)
    history = service.get_history("ZECUSDT")
"""

from __future__ import annotations

import datetime
import logging

from services.trade_history import (
    _LOOKBACK_DAYS_FALLBACK,
    _MS_PER_DAY,
    TradeHistory,
    TradeHistoryClientProtocol,
    TradeHistoryService,
    _now_ms,
)

log = logging.getLogger(__name__)


def _ms_to_readable(ms: int) -> str:
    """Convert a millisecond UTC timestamp to 'YYYY-MM-DD HH:MM:SS UTC'."""
    dt = datetime.datetime.fromtimestamp(ms / 1000, tz=datetime.timezone.utc)
    return dt.strftime("%Y-%m-%d %H:%M:%S UTC")


class FilteredTradeHistoryService:
    """
    Fetches execution history then returns only trades of a given execType.

    All heavy lifting — time-window slicing, inner-loop paging, duplicate
    guarding — is delegated to TradeHistoryService.  This class only adds
    a single filter step on the returned TradeHistory.

    The public interface (get_history signature, return type) is intentionally
    identical to TradeHistoryService so the two are interchangeable.
    """

    def __init__(
        self,
        client: TradeHistoryClientProtocol,
        exec_type: str,
        category: str = "linear",
        limit: int = 100,
        lookback_days: int = _LOOKBACK_DAYS_FALLBACK,
    ) -> None:
        """
        Args:
            client:        API client satisfying TradeHistoryClientProtocol.
            exec_type:     The execType to keep, e.g. "Trade" or "Funding".
                           Comparison is case-sensitive (Bybit values are
                           title-cased: "Trade", "Funding", "BustTrade", …).
            category:      Bybit instrument category ("linear" or "inverse").
            limit:         Max records per API call (Bybit maximum: 100).
            lookback_days: Default calendar days to look back.
                           Overridable per call via get_history(lookback_days=).
        """
        self._exec_type = exec_type
        self._inner = TradeHistoryService(
            client=client,
            category=category,
            limit=limit,
            lookback_days=lookback_days,
        )

    def get_history(
        self,
        symbol: str,
        lookback_days: int | None = None,
        start_time_ms: int | None = None,
    ) -> TradeHistory:
        """
        Return trades for *symbol* whose execType equals self._exec_type.

        Parameters are forwarded unchanged to TradeHistoryService.get_history;
        see that method's docstring for full parameter semantics.

        Returns:
            TradeHistory dataclass with only the matching trades.
            trades may be an empty list if none match.

        Raises:
            BybitAPIError: Propagated from the inner service on API errors.
        """
        # Compute the effective fetch window for human-readable logging.
        # This mirrors the priority logic inside TradeHistoryService so the
        # logged range matches exactly what the inner service will request.
        now = _now_ms()
        if start_time_ms is not None:
            effective_start = start_time_ms
        elif lookback_days is not None:
            effective_start = now - lookback_days * _MS_PER_DAY
        else:
            effective_start = now - self._inner._lookback_days * _MS_PER_DAY

        log.debug(
            "Fetching %s executions for %s  |  %s → %s  (%s days)",
            self._exec_type,
            symbol.upper(),
            _ms_to_readable(effective_start),
            _ms_to_readable(now),
            round((now - effective_start) / _MS_PER_DAY, 1),
        )

        full = self._inner.get_history(
            symbol=symbol,
            lookback_days=lookback_days,
            start_time_ms=start_time_ms,
        )

        filtered = [t for t in full.trades if t.exec_type == self._exec_type]

        log.info(
            "%s | execType=%s | from API: %d trade(s) total | after filter: %d trade(s)",
            full.symbol,
            self._exec_type,
            len(full.trades),
            len(filtered),
        )

        return TradeHistory(
            symbol=full.symbol,
            category=full.category,
            trades=filtered,
        )
