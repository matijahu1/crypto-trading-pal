"""
api/bybit_client.py — wraps the official ``pybit`` SDK.

Change log:
  - get_order_history() gains an optional ``order_status`` parameter.
    When provided, it is forwarded to Bybit as ``orderStatus`` so the
    server returns only matching orders (e.g. ``"Filled"``).
    Pass ``None`` (the default) to retrieve all statuses as before.
"""

from __future__ import annotations

from typing import Any
from typing import cast

from pybit.unified_trading import HTTP # pyright: ignore[reportMissingTypeStubs]


class BybitAPIError(Exception):
    """Raised when pybit or the Bybit API signals an error."""


class BybitClient:
    """
    Thin adapter around pybit's unified HTTP session.

    testnet : bool
        ``False``  → mainnet  (https://api.bybit.com)   [default]
        ``True``   → testnet  (https://api-testnet.bybit.com)
    """

    def __init__(
        self,
        testnet:    bool = False,
        api_key:    str  = "",
        api_secret: str  = "",
    ) -> None:
        self._session = HTTP(
            testnet=testnet,
            api_key=api_key or None,
            api_secret=api_secret or None,
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _unwrap(response: dict[str, Any], symbol: str | None = None) -> dict[str, Any]:
        ret_code = response.get("retCode", 0)
        if ret_code != 0:
            msg = response.get("retMsg", "unknown error")
            raise BybitAPIError(f"Bybit API error [{ret_code}]: {msg}")
        return response

    # ------------------------------------------------------------------
    # Public API methods
    # ------------------------------------------------------------------

    def get_funding_rate_history(
        self,
        symbol:   str,
        category: str = "linear",
        limit:    int = 8,
    ) -> list[dict[str, Any]]:
        try:
            response = self._session.get_funding_rate_history(  # type: ignore
                category=category, symbol=symbol, limit=limit,
            )
        except Exception as exc:
            raise BybitAPIError(f"Failed to fetch funding rate history: {exc}") from exc
        return self._unwrap(response, symbol)["result"]["list"] # type: ignore

    def get_instruments_info(
        self,
        symbol:   str,
        category: str = "linear",
    ) -> dict[str, Any]:
        try:
            response = self._session.get_instruments_info( # type: ignore
                category=category, symbol=symbol,
            )
        except Exception as exc:
            raise BybitAPIError(f"Failed to fetch instruments info: {exc}") from exc
        data  = self._unwrap(response, symbol) # type: ignore
        items: list[dict[str, Any]] = data["result"]["list"]
        if not items:
            raise BybitAPIError(f"Symbol '{symbol}' not found on Bybit ({category})")
        return items[0]

    def get_wallet_balance(self, account_type: str = "UNIFIED") -> list[dict[str, Any]]:
        try:
            response = self._session.get_wallet_balance(accountType=account_type) # type: ignore
        except Exception as exc:
            raise BybitAPIError(f"Failed to fetch wallet balance: {exc}") from exc
        accounts: list[dict[str, Any]] = self._unwrap(response)["result"]["list"] # type: ignore
        return accounts[0].get("coin", []) if accounts else []

    def get_positions(self, category: str = "linear") -> list[dict[str, Any]]:
        try:
            response = self._session.get_positions( # type: ignore
                category=category,
                settleCoin="USDT" if category == "linear" else "BTC",
            )
        except Exception as exc:
            raise BybitAPIError(f"Failed to fetch positions: {exc}") from exc
        return self._unwrap(response)["result"]["list"] # type: ignore

    def get_trade_history(
        self,
        symbol:     str,
        category:   str      = "linear",
        limit:      int      = 100,
        start_time: int | None = None,
        end_time:   int | None = None,
    ) -> list[dict[str, Any]]:
        kwargs: dict[str, Any] = dict(category=category, symbol=symbol, limit=limit)
        if start_time is not None:
            kwargs["startTime"] = start_time
        if end_time is not None:
            kwargs["endTime"] = end_time
        try:
            response = self._session.get_executions(**kwargs) # type: ignore
        except Exception as exc:
            raise BybitAPIError(f"Failed to fetch trade history: {exc}") from exc
        return self._unwrap(response, symbol)["result"]["list"] # type: ignore

    def get_order_history(
        self,
        symbol:       str,
        category:     str      = "linear",
        limit:        int      = 50,
        start_time:   int | None = None,
        end_time:     int | None = None,
        order_status: str | None = None,
    ) -> list[dict[str, Any]]:
        """
        Fetch order history for a single symbol.

        Args:
            symbol:       Perpetual futures symbol, e.g. "CCUSDT".
            category:     "linear" or "inverse".
            limit:        Max orders per call (Bybit maximum: 50).
            start_time:   Window start as Unix ms timestamp (filters createdTime).
            end_time:     Window end as Unix ms timestamp (filters createdTime).
            order_status: Optional server-side status filter forwarded as
                          ``orderStatus``.  E.g. ``"Filled"`` returns only
                          fully executed orders.  ``None`` returns all statuses.

        Returns:
            List of order dicts, newest-first by createdTime.

        Raises:
            BybitAPIError: On auth failure or any other API / network error.
        """
        kwargs: dict[str, Any] = dict(category=category, symbol=symbol, limit=limit)
        if start_time is not None:
            kwargs["startTime"] = start_time
        if end_time is not None:
            kwargs["endTime"] = end_time
        if order_status is not None:
            kwargs["orderStatus"] = order_status   # ← server-side filter

        try:
            response = self._session.get_order_history(**kwargs) # type: ignore
        except Exception as exc:
            raise BybitAPIError(f"Failed to fetch order history: {exc}") from exc
        
        data = self._unwrap(response, symbol) # type: ignore
        return cast(list[dict[str, Any]], data["result"]["list"])        

    def get_executions(
        self,
        symbol:    str | None = None,
        category:  str        = "linear",
        limit:     int        = 100,
        exec_type: str | None = None,
    ) -> list[dict[str, Any]]:
        kwargs: dict[str, Any] = {"category": category, "limit": limit}
        if symbol:
            kwargs["symbol"] = symbol
        if exec_type:
            kwargs["execType"] = exec_type
        try:
            response = self._session.get_executions(**kwargs) # type: ignore
        except Exception as exc:
            context = symbol if symbol else "ACCOUNT"
            raise BybitAPIError(f"Failed to fetch executions for {context}: {exc}") from exc
        return self._unwrap(response, symbol)["result"]["list"] # type: ignore
