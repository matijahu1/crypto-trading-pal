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

    def get_wallet_balance(self, account_type: str = "UNIFIED") -> list[dict[str, Any]]:
        """
        Fetch coin balances for the authenticated account.

        This is a **private** endpoint — api_key and api_secret must be set.

        Args:
            account_type: "UNIFIED" for the unified trading account (default),
                          "CONTRACT" for classic derivatives accounts.

        Returns:
            List of coin balance dicts, e.g.:
            [{"coin": "BTC", "walletBalance": "0.25",
              "availableToWithdraw": "0.20"}, ...]

        Equivalent pybit call:
            session.get_wallet_balance(accountType="UNIFIED")

        Raises:
            BybitAPIError: On auth failure (retCode 10003/10004) or any other
                           API / network error.
        """
        try:
            response = self._session.get_wallet_balance(accountType=account_type)
        except Exception as exc:
            raise BybitAPIError(f"Failed to fetch wallet balance: {exc}") from exc

        data = self._unwrap(response)

        # Bybit returns a list of accounts; each account has a "coin" sub-list.
        # We flatten to a single list of coin dicts for simplicity.
        accounts: list[dict[str, Any]] = data["result"]["list"]
        if not accounts:
            return []
        return accounts[0].get("coin", [])

    def get_positions(self, category: str = "linear") -> list[dict[str, Any]]:
        """
        Fetch all open positions for the authenticated account.

        This is a **private** endpoint — api_key and api_secret must be set.

        Args:
            category: "linear" for USDT-margined perpetuals (default),
                      "inverse" for coin-margined perpetuals.

        Returns:
            List of position dicts for all symbols with an open position, e.g.:
            [{"symbol": "BTCUSDT", "side": "Buy", "size": "0.01",
              "avgPrice": "65000", "markPrice": "65200",
              "unrealisedPnl": "2.0"}, ...]

        Equivalent pybit call:
            session.get_positions(category="linear", settleCoin="USDT")

        Raises:
            BybitAPIError: On auth failure or any other API / network error.
        """
        try:
            # settleCoin="USDT" fetches all linear positions without requiring
            # a specific symbol — the only way to get the full position list.
            response = self._session.get_positions(
                category=category,
                settleCoin="USDT" if category == "linear" else "BTC",
            )
        except Exception as exc:
            raise BybitAPIError(f"Failed to fetch positions: {exc}") from exc

        data = self._unwrap(response)
        return data["result"]["list"]

    def get_trade_history(
        self,
        symbol: str,
        category: str = "linear",
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """
        Fetch execution (trade) history for a single symbol.

        This is a **private** endpoint — api_key and api_secret must be set.

        Args:
            symbol:   Perpetual futures symbol, e.g. "ZECUSDT".
            category: "linear" for USDT-margined perpetuals (default),
                      "inverse" for coin-margined perpetuals.
            limit:    Number of executions to return. Max 100 per Bybit's API.

        Returns:
            List of execution dicts, newest-first, e.g.:
            [{"execId": "abc123", "symbol": "ZECUSDT", "side": "Buy",
              "execPrice": "30.5", "execQty": "10", "execTime": "1700000000000"
             }, ...]

        Equivalent pybit call:
            session.get_executions(category="linear", symbol="ZECUSDT", limit=100)

        Raises:
            BybitAPIError: On auth failure or any other API / network error.
        """
        try:
            response = self._session.get_executions(
                category=category,
                symbol=symbol,
                limit=limit,
            )
        except Exception as exc:
            raise BybitAPIError(f"Failed to fetch trade history: {exc}") from exc

        data = self._unwrap(response, symbol)
        return data["result"]["list"]
