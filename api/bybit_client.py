"""
Bybit API client — wraps the official ``pybit`` SDK.

Responsibilities:
  - Own the pybit session lifecycle and configuration
  - Translate pybit responses into the same plain-dict shape the rest of the
    application has always expected
  - Raise BybitAPIError on any SDK or API-level failure

The public method signatures are identical to the previous urllib-based
implementation, so FundingRateService (and every other service) is unaffected.

To swap exchanges: implement the same two public methods in a new module and
pass it wherever BybitClient is currently injected.

pybit docs: https://github.com/bybit-exchange/pybit
"""

from __future__ import annotations

from typing import Any

from pybit.unified_trading import HTTP  # pybit >= 5.x unified trading session


class BybitAPIError(Exception):
    """Raised when pybit or the Bybit API signals an error."""


class BybitClient:
    """
    Thin adapter around pybit's unified HTTP session.

    Configuration
    -------------
    testnet : bool
        ``False``  → mainnet  (https://api.bybit.com)   [default]
        ``True``   → testnet  (https://api-testnet.bybit.com)

    api_key / api_secret
        Only required for authenticated (private) endpoints.
        All funding-rate and instrument-info calls are public — leave them
        empty for now; add them when private endpoints are needed.

    Example
    -------
    >>> client = BybitClient()                          # mainnet, public only
    >>> client = BybitClient(testnet=True)              # testnet
    >>> client = BybitClient(api_key="k", api_secret="s")  # authenticated
    """

    def __init__(
        self,
        testnet: bool = False,
        api_key: str = "",
        api_secret: str = "",
    ) -> None:
        """
        Args:
            testnet:    Connect to Bybit testnet when True.
            api_key:    Optional — only needed for private endpoints.
            api_secret: Optional — only needed for private endpoints.
        """
        # pybit.unified_trading.HTTP is the V5 unified trading session.
        # Passing empty strings for key/secret is fine for public endpoints.
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
        """
        Validate a pybit response and return it as-is.

        pybit already raises ``InvalidRequestError`` / ``FailedRequestError``
        for non-zero retCodes when ``recv_window`` is set, but the unified
        session can also return retCode != 0 silently on some edge cases.
        This guard makes error handling consistent regardless.

        Args:
            response: Raw dict returned by any pybit session method.
            symbol:   Optional symbol name for clearer error messages.

        Returns:
            The validated response dict (identical object, not a copy).

        Raises:
            BybitAPIError: If retCode is non-zero.
        """
        ret_code = response.get("retCode", 0)
        if ret_code != 0:
            msg = response.get("retMsg", "unknown error")
            raise BybitAPIError(f"Bybit API error [{ret_code}]: {msg}")
        return response

    # ------------------------------------------------------------------
    # Public API methods
    # (signatures are intentionally identical to the old urllib client)
    # ------------------------------------------------------------------

    def get_funding_rate_history(
        self,
        symbol: str,
        category: str = "linear",
        limit: int = 8,
    ) -> list[dict[str, Any]]:
        """
        Fetch recent funding rate history for a perpetual futures symbol.

        Args:
            symbol:   E.g. "ZECUSDT"
            category: "linear" for USDT-margined, "inverse" for coin-margined.
            limit:    Number of records to return (default 8, max 200).

        Returns:
            List of funding rate records, newest-first:
            [{"symbol": "ZECUSDT", "fundingRate": "0.0001",
              "fundingRateTimestamp": "1700000000000"}, ...]

        Equivalent pybit call:
            session.get_funding_rate_history(
                category="linear", symbol="ZECUSDT", limit=8
            )

        Raises:
            BybitAPIError: On API-level errors or network failures.
        """
        try:
            response = self._session.get_funding_rate_history(
                category=category,
                symbol=symbol,
                limit=limit,
            )
        except Exception as exc:
            raise BybitAPIError(f"Failed to fetch funding rate history: {exc}") from exc

        data = self._unwrap(response, symbol)
        return data["result"]["list"]

    def get_instruments_info(
        self,
        symbol: str,
        category: str = "linear",
    ) -> dict[str, Any]:
        """
        Fetch instrument metadata (including funding interval) for a symbol.

        Args:
            symbol:   E.g. "ZECUSDT"
            category: "linear" or "inverse".

        Returns:
            Single instrument info dict, e.g.:
            {"symbol": "ZECUSDT", "fundingInterval": 480, ...}

        Equivalent pybit call:
            session.get_instruments_info(category="linear", symbol="ZECUSDT")

        Raises:
            BybitAPIError: If symbol is not found or the request fails.
        """
        try:
            response = self._session.get_instruments_info(
                category=category,
                symbol=symbol,
            )
        except Exception as exc:
            raise BybitAPIError(f"Failed to fetch instruments info: {exc}") from exc

        data = self._unwrap(response, symbol)
        items: list[dict[str, Any]] = data["result"]["list"]
        if not items:
            raise BybitAPIError(f"Symbol '{symbol}' not found on Bybit ({category})")
        return items[0]
