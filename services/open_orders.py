"""
services/open_orders.py — fetch and map currently active orders.

Responsibilities:
  - Call BybitClient.get_open_orders() for a given symbol.
  - Map raw API dicts to typed OpenOrder dataclasses.
  - Use decimal.Decimal for all monetary and quantity fields so that
    floating-point rounding errors never corrupt financial data.
  - Return an OpenOrderSnapshot dataclass ready for export.

Usage::

    from services.open_orders import OpenOrderService

    service  = OpenOrderService(client=client, category="linear")
    snapshot = service.get_open_orders("ICPUSDT")
    print(snapshot.orders)           # list[OpenOrder]
    print(snapshot.orders[0].price)  # Decimal("2.187")

Design notes:
  - Decimal values are parsed from the raw API string, never from a float,
    so no precision is lost in transit.
  - Empty string prices (market orders) are stored as Decimal("0").
  - created_date / created_time are derived from createdTime (Unix ms).
  - The service is intentionally stateless: construct once, call many times.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Domain dataclasses
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class OpenOrder:
    """Immutable snapshot of one active order."""

    order_id:     str
    symbol:       str
    side:         str       # "Buy" | "Sell"
    order_type:   str       # "Limit" | "Market" | …
    price:        Decimal   # Decimal("0") for market orders
    qty:          Decimal
    order_status: str       # "New" | "PartiallyFilled" | …
    created_ts:   str       # raw createdTime ms string from API
    created_date: str       # "YYYY-MM-DD"
    created_time: str       # "HH:MM:SS"


@dataclass(frozen=True)
class OpenOrderSnapshot:
    """All active orders for one symbol at fetch time."""

    symbol:   str
    category: str
    orders:   list[OpenOrder]


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------

class OpenOrderService:
    """
    Fetches currently active (unfilled) orders for a single symbol.

    Args:
        client:   Any object that exposes ``get_open_orders(symbol, category,
                  limit)``.  In production this is a BybitClient instance;
                  in tests it is a plain stub.
        category: Bybit market category, default ``"linear"``.
    """

    def __init__(self, client: Any, category: str = "linear") -> None:
        self._client   = client
        self._category = category

    def get_open_orders(self, symbol: str) -> OpenOrderSnapshot:
        """
        Fetch all active orders for *symbol* and return a typed snapshot.

        Args:
            symbol: Futures symbol, e.g. ``"ICPUSDT"``.  Stored as upper-case.

        Returns:
            OpenOrderSnapshot with zero or more OpenOrder entries.

        Raises:
            BybitAPIError: propagated from the client on any API/network error.
        """
        symbol = symbol.strip().upper()
        log.debug("Fetching open orders for %s (category=%s)", symbol, self._category)

        raw: list[dict[str, Any]] = self._client.get_open_orders(
            symbol=symbol,
            category=self._category,
        )

        orders = [self._map(row) for row in raw]
        log.debug("Received %d open order(s) for %s", len(orders), symbol)

        return OpenOrderSnapshot(
            symbol=symbol,
            category=self._category,
            orders=orders,
        )

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _map(raw: dict[str, Any]) -> OpenOrder:
        """Convert one raw API dict to a typed OpenOrder."""
        created_ts   = str(raw.get("createdTime", ""))
        created_date, created_time = _parse_ts(created_ts)

        return OpenOrder(
            order_id     = str(raw.get("orderId",     "")),
            symbol       = str(raw.get("symbol",      "")),
            side         = str(raw.get("side",        "")),
            order_type   = str(raw.get("orderType",   "")),
            price        = _to_decimal(raw.get("price", "0")),
            qty          = _to_decimal(raw.get("qty",   "0")),
            order_status = str(raw.get("orderStatus", "")),
            created_ts   = created_ts,
            created_date = created_date,
            created_time = created_time,
        )


# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------

def _to_decimal(value: Any) -> Decimal:
    """
    Parse *value* to Decimal without ever going through float.

    Empty strings and None become Decimal("0").
    Unrecognised strings log a warning and return Decimal("0").
    """
    if value is None or value == "":
        return Decimal("0")
    try:
        return Decimal(str(value))
    except InvalidOperation:
        log.warning("Cannot convert %r to Decimal — using 0", value)
        return Decimal("0")


def _parse_ts(ms_str: str) -> tuple[str, str]:
    """
    Convert a Unix-millisecond timestamp string to (date, time) strings.

    Returns ("", "") for empty or unparseable input.
    """
    if not ms_str:
        return "", ""
    try:
        dt = datetime.fromtimestamp(int(ms_str) / 1_000, tz=timezone.utc)
        return dt.strftime("%Y-%m-%d"), dt.strftime("%H:%M:%S")
    except (ValueError, OSError):
        return "", ""
