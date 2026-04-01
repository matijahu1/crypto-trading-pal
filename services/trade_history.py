"""
services/trade_history.py — service layer for futures trade (execution) history.

Responsibilities:
  - Fetch raw execution data via the API client
  - Map API field names to clean internal names
  - Convert millisecond timestamps to human-readable date and time strings
  - Return structured result objects — NOT formatted strings

Follows the same pattern as FuturesPositionService and BalanceService:
  - Depends on a Protocol, not the concrete BybitClient
  - Returns dataclasses, never strings
  - Business logic lives here; presentation lives in exporters
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from utils.time_utils import ms_timestamp_to_date_time


# ---------------------------------------------------------------------------
# Protocol — decouples the service from the concrete client (mockable in tests)
# ---------------------------------------------------------------------------

class TradeHistoryClientProtocol(Protocol):
    """Minimal interface the service needs from an exchange API client."""

    def get_trade_history(
        self, symbol: str, category: str, limit: int
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
    date: str        # UTC date of execution, e.g. "2023-11-14"
    time: str        # UTC time of execution, e.g. "22:13:20"


@dataclass
class TradeHistory:
    """A batch of trades for one symbol."""

    symbol: str
    category: str
    trades: list[Trade]   # in the order returned by the API (newest-first)


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------

class TradeHistoryService:
    """Fetches execution history for a single futures contract."""

    def __init__(
        self,
        client: TradeHistoryClientProtocol,
        category: str = "linear",
        limit: int = 100,
    ) -> None:
        """
        Args:
            client:   API client satisfying TradeHistoryClientProtocol.
            category: Bybit instrument category.
                      "linear"  → USDT-margined perpetuals (default)
                      "inverse" → coin-margined perpetuals
            limit:    Maximum number of executions to retrieve per call.
                      Bybit's hard maximum is 100. No pagination is performed.
        """
        self._client = client
        self._category = category
        self._limit = limit

    def get_history(self, symbol: str) -> TradeHistory:
        """
        Return the most recent trade executions for *symbol*.

        Args:
            symbol: Perpetual futures symbol, e.g. "ZECUSDT".
                    Uppercase is enforced automatically.

        Returns:
            TradeHistory dataclass. The trades list may be empty if the
            account has no execution history for this symbol.

        Raises:
            BybitAPIError: Propagated from the client on network / API errors.
        """
        symbol = symbol.upper()
        raw = self._client.get_trade_history(
            symbol=symbol,
            category=self._category,
            limit=self._limit,
        )

        trades = [
            Trade(
                trade_id=entry.get("execId", ""),
                symbol=entry.get("symbol", symbol),
                side=entry.get("side", ""),
                price=float(entry.get("execPrice", 0) or 0),
                size=float(entry.get("execQty", 0) or 0),
                date=ms_timestamp_to_date_time(entry.get("execTime", ""))[0],
                time=ms_timestamp_to_date_time(entry.get("execTime", ""))[1],
            )
            for entry in raw
        ]

        return TradeHistory(
            symbol=symbol,
            category=self._category,
            trades=trades,
        )
