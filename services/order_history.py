"""
services/order_history.py — service layer for futures order history.

Responsibilities:
  - Fetch raw order data via the API client
  - Map API field names to clean internal names
  - Convert millisecond timestamps to human-readable date and time strings
  - Return structured result objects — NOT formatted strings

Follows the same pattern as TradeHistoryService:
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

class OrderHistoryClientProtocol(Protocol):
    """Minimal interface the service needs from an exchange API client."""

    def get_order_history(
        self, symbol: str, category: str, limit: int
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
    created_date: str   # UTC date, e.g. "2023-11-14"
    created_time: str   # UTC time, e.g. "22:13:20"
    updated_date: str   # UTC date of last status change
    updated_time: str   # UTC time of last status change


@dataclass
class OrderHistory:
    """A batch of orders for one symbol."""

    symbol: str
    category: str
    orders: list[Order]   # in the order returned by the API (newest-first)


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------

class OrderHistoryService:
    """Fetches order history for a single futures contract."""

    def __init__(
        self,
        client: OrderHistoryClientProtocol,
        category: str = "linear",
        limit: int = 50,
    ) -> None:
        """
        Args:
            client:   API client satisfying OrderHistoryClientProtocol.
            category: Bybit instrument category.
                      "linear"  → USDT-margined perpetuals (default)
                      "inverse" → coin-margined perpetuals
            limit:    Maximum number of orders to retrieve per call.
                      Bybit's hard maximum is 50 for order history.
                      No pagination is performed.
        """
        self._client = client
        self._category = category
        self._limit = limit

    def get_history(self, symbol: str) -> OrderHistory:
        """
        Return the most recent orders for *symbol*.

        Args:
            symbol: Perpetual futures symbol, e.g. "ZECUSDT".
                    Uppercase is enforced automatically.

        Returns:
            OrderHistory dataclass. The orders list may be empty if the
            account has no order history for this symbol.

        Raises:
            BybitAPIError: Propagated from the client on network / API errors.
        """
        symbol = symbol.upper()
        raw = self._client.get_order_history(
            symbol=symbol,
            category=self._category,
            limit=self._limit,
        )

        orders = [
            Order(
                order_id=entry.get("orderId", ""),
                symbol=entry.get("symbol", symbol),
                side=entry.get("side", ""),
                order_type=entry.get("orderType", ""),
                price=float(entry.get("price", 0) or 0),
                qty=float(entry.get("qty", 0) or 0),
                order_status=entry.get("orderStatus", ""),
                created_date=ms_timestamp_to_date_time(entry.get("createdTime", ""))[0],
                created_time=ms_timestamp_to_date_time(entry.get("createdTime", ""))[1],
                updated_date=ms_timestamp_to_date_time(entry.get("updatedTime", ""))[0],
                updated_time=ms_timestamp_to_date_time(entry.get("updatedTime", ""))[1],
            )
            for entry in raw
        ]

        return OrderHistory(
            symbol=symbol,
            category=self._category,
            orders=orders,
        )
