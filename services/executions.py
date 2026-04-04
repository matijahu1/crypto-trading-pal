"""
services/executions.py — service layer for futures execution history.

Responsibilities:
  - Fetch raw execution data via the API client
  - Map API field names to clean internal names
  - Convert millisecond timestamps to human-readable date and time strings
  - Return structured result objects — NOT formatted strings

Follows the same pattern as OrderHistoryService:
  - Depends on a Protocol, not the concrete BybitClient
  - Returns dataclasses, never strings
  - Business logic lives here; presentation lives in exporters

Note: this service exposes richer execution detail than TradeHistoryService,
including exec_fee, exec_fee_rate, and exec_type (e.g. "Trade", "Funding").
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from utils.time_utils import ms_timestamp_to_date_time


# ---------------------------------------------------------------------------
# Protocol — decouples the service from the concrete client (mockable in tests)
# ---------------------------------------------------------------------------

class ExecutionsClientProtocol(Protocol):
    """Minimal interface the service needs from an exchange API client."""

    def get_executions(
        self, symbol: str, category: str, limit: int
    ) -> list[dict[str, Any]]: ...


# ---------------------------------------------------------------------------
# Result dataclasses
# ---------------------------------------------------------------------------

@dataclass
class Execution:
    """A single trade execution (fill)."""

    exec_id: str          # unique execution ID
    symbol: str
    side: str             # "Buy" or "Sell"
    exec_price: float     # fill price
    exec_qty: float       # filled quantity
    exec_fee: float       # fee charged for this fill
    exec_fee_rate: float  # fee rate applied
    exec_type: str        # e.g. "Trade", "Funding", "BustTrade"
    date: str             # UTC date, e.g. "2023-11-14"
    time: str             # UTC time, e.g. "22:13:20"


@dataclass
class ExecutionHistory:
    """A batch of executions for one symbol."""

    symbol: str
    category: str
    executions: list[Execution]   # in the order returned by the API (newest-first)


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------

class ExecutionsService:
    """Fetches execution history for a single futures contract."""

    def __init__(
        self,
        client: ExecutionsClientProtocol,
        category: str = "linear",
        limit: int = 100,
    ) -> None:
        """
        Args:
            client:   API client satisfying ExecutionsClientProtocol.
            category: Bybit instrument category.
                      "linear"  → USDT-margined perpetuals (default)
                      "inverse" → coin-margined perpetuals
            limit:    Maximum number of executions to retrieve per call.
                      Bybit's hard maximum is 100. No pagination is performed.
        """
        self._client = client
        self._category = category
        self._limit = limit

    def get_executions(self, symbol: str) -> ExecutionHistory:
        """
        Return the most recent executions for *symbol*.

        Args:
            symbol: Perpetual futures symbol, e.g. "ZECUSDT".
                    Uppercase is enforced automatically.

        Returns:
            ExecutionHistory dataclass. The executions list may be empty if the
            account has no execution history for this symbol.

        Raises:
            BybitAPIError: Propagated from the client on network / API errors.
        """
        symbol = symbol.upper()
        raw = self._client.get_executions(
            symbol=symbol,
            category=self._category,
            limit=self._limit,
        )

        executions = [
            Execution(
                exec_id=entry.get("execId", ""),
                symbol=entry.get("symbol", symbol),
                side=entry.get("side", ""),
                exec_price=float(entry.get("execPrice", 0) or 0),
                exec_qty=float(entry.get("execQty", 0) or 0),
                exec_fee=float(entry.get("execFee", 0) or 0),
                exec_fee_rate=float(entry.get("feeRate", 0) or 0),
                exec_type=entry.get("execType", ""),
                date=ms_timestamp_to_date_time(entry.get("execTime", ""))[0],
                time=ms_timestamp_to_date_time(entry.get("execTime", ""))[1],
            )
            for entry in raw
        ]

        return ExecutionHistory(
            symbol=symbol,
            category=self._category,
            executions=executions,
        )
