"""
services/recent_executions.py — Simple service for the latest N trade fills.
"""

from __future__ import annotations
from dataclasses import dataclass
from typing import Any, Protocol, Optional
import logging

from utils.time_utils import ms_timestamp_to_date_time

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Protocol
# ---------------------------------------------------------------------------

class RecentExecutionClientProtocol(Protocol):
    def get_executions(
        self, 
        symbol: str | None = None, 
        category: str = "linear", 
        limit: int = 100,
        exec_type: str | None = None  
    ) -> list[dict[str, Any]]: ...

# ---------------------------------------------------------------------------
# Result Dataclasses
# ---------------------------------------------------------------------------

@dataclass
class RecentExecution:
    """A clean mapping of a single trade fill."""
    exec_id: str
    order_id: str
    symbol: str
    side: str
    price: float
    qty: float
    exec_type: str
    date: str
    time: str

@dataclass
class RecentExecutionHistory:
    symbol: str
    count: int
    executions: list[RecentExecution]

# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------

class RecentExecutionService:
    """
    Provides a simple snapshot of the most recent executions.
    This service does NOT paginate or use time windows. 
    It simply fetches the last 'limit' records.
    """

    def __init__(
        self,
        client: RecentExecutionClientProtocol,
        category: str = "linear",
        default_limit: int = 10  # Hardcoded default, can be overwritten by config later
    ) -> None:
        self._client = client
        self._category = category
        self._default_limit = default_limit

    def get_recent_fills(
        self, 
        symbol: str | None = None, 
        limit: int | None = None
    ) -> RecentExecutionHistory:
        """
        Fetch the last X trade fills using server-side filtering.
        """
        fetch_limit = limit if limit is not None else self._default_limit
        
        # Context name for logging and the dataclass
        context_name = symbol.strip().upper() if (symbol and symbol.strip()) else "ACCOUNT-WIDE"

        log.info(f"Requesting {fetch_limit} trades from API for {context_name}...")

        # We now pass exec_type="Trade" directly to the client.
        # The API will handle the filtering, so 'limit' applies only to trades.
        raw_data = self._client.get_executions(
            symbol=symbol,
            category=self._category,
            limit=fetch_limit,
            exec_type="Trade"  # Server-side filter applied here
        )

        executions = [
            RecentExecution(
                exec_id=entry.get("execId", ""),
                order_id=entry.get("orderId", ""),
                symbol=entry.get("symbol", context_name),
                side=entry.get("side", ""),
                price=float(entry.get("execPrice", 0) or 0),
                qty=float(entry.get("execQty", 0) or 0),
                exec_type=entry.get("execType", ""),
                date=ms_timestamp_to_date_time(str(entry.get("execTime", "")))[0],
                time=ms_timestamp_to_date_time(str(entry.get("execTime", "")))[1]
            )
            for entry in raw_data
        ]

        log.info(f"Received {len(executions)} trades from Bybit.")

        return RecentExecutionHistory(
            symbol=context_name,
            count=len(executions),
            executions=executions
        )