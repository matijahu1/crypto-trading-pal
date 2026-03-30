"""
services/futures_position.py — service layer for open futures positions.

Responsibilities:
  - Fetch raw position data via the API client
  - Filter out positions with zero size
  - Return structured result objects — NOT formatted strings

Follows the same pattern as BalanceService and FundingRateService:
  - Depends on a Protocol, not the concrete BybitClient
  - Returns dataclasses, never strings
  - Business logic lives here; presentation lives in exporters / commands
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol


# ---------------------------------------------------------------------------
# Protocol — decouples the service from the concrete client (mockable in tests)
# ---------------------------------------------------------------------------

class PositionClientProtocol(Protocol):
    """Minimal interface the service needs from an exchange API client."""

    def get_positions(self, category: str) -> list[dict[str, Any]]: ...


# ---------------------------------------------------------------------------
# Result dataclasses
# ---------------------------------------------------------------------------

@dataclass
class FuturesPosition:
    """A single open futures position."""

    symbol: str
    side: str          # "Buy" or "Sell"
    size: float        # position size in base currency
    entry_price: float
    mark_price: float  # 0.0 if not provided by the API
    unrealized_pnl: float  # 0.0 if not provided by the API


@dataclass
class PositionSnapshot:
    """All non-zero open positions at a point in time."""

    category: str               # e.g. "linear"
    positions: list[FuturesPosition]  # only non-zero size, sorted by symbol


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------

class FuturesPositionService:
    """Fetches and filters open futures position data."""

    def __init__(
        self,
        client: PositionClientProtocol,
        category: str = "linear",
    ) -> None:
        """
        Args:
            client:   API client satisfying PositionClientProtocol.
            category: Bybit instrument category.
                      "linear"  → USDT-margined perpetuals (default)
                      "inverse" → coin-margined perpetuals
        """
        self._client = client
        self._category = category

    def get_positions(self) -> PositionSnapshot:
        """
        Return all open positions with a non-zero size.

        Returns:
            PositionSnapshot with positions sorted alphabetically by symbol.

        Raises:
            BybitAPIError: Propagated from the client on network / API errors.
        """
        raw = self._client.get_positions(self._category)

        positions: list[FuturesPosition] = []
        for entry in raw:
            size = float(entry.get("size", 0) or 0)
            if size == 0:
                continue  # skip flat / closed positions

            positions.append(FuturesPosition(
                symbol=entry.get("symbol", ""),
                side=entry.get("side", ""),
                size=size,
                entry_price=float(entry.get("avgPrice", 0) or 0),
                mark_price=float(entry.get("markPrice", 0) or 0),
                unrealized_pnl=float(entry.get("unrealisedPnl", 0) or 0),
            ))

        positions.sort(key=lambda p: p.symbol)

        return PositionSnapshot(category=self._category, positions=positions)
