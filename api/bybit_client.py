"""
Thin HTTP wrapper around the public Bybit V5 REST API.

Responsibilities:
  - Build and execute HTTP requests
  - Raise on non-200 / error responses
  - Return raw parsed JSON — no business logic here

To swap exchanges later: implement the same interface in a new module.
"""

import urllib.request
import urllib.parse
import json
from typing import Any


BASE_URL = "https://api.bybit.com"


class BybitAPIError(Exception):
    """Raised when Bybit returns a non-zero retCode or an HTTP error."""


class BybitClient:
    """Minimal client for the Bybit V5 public REST API."""

    def __init__(self, base_url: str = BASE_URL) -> None:
        self._base_url = base_url.rstrip("/")

    def _get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        """
        Perform a GET request and return the parsed JSON body.

        Args:
            path:   API path, e.g. "/v5/market/funding/history"
            params: Query-string parameters.

        Returns:
            Parsed JSON response dict.

        Raises:
            BybitAPIError: On HTTP errors or Bybit retCode != 0.
        """
        url = self._base_url + path
        if params:
            url += "?" + urllib.parse.urlencode(params)

        try:
            with urllib.request.urlopen(url, timeout=10) as resp:
                body = json.loads(resp.read().decode())
        except Exception as exc:
            raise BybitAPIError(f"HTTP request failed: {exc}") from exc

        if body.get("retCode", 0) != 0:
            msg = body.get("retMsg", "unknown error")
            raise BybitAPIError(f"Bybit API error [{body['retCode']}]: {msg}")

        return body

    # ------------------------------------------------------------------
    # Public API methods (add more here as features grow)
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
            category: "linear" for USDT perpetuals, "inverse" for coin-margined
            limit:    Number of historical entries to return (default 8)

        Returns:
            List of funding rate records ordered newest-first, e.g.:
            [{"symbol": "ZECUSDT", "fundingRate": "0.0001",
              "fundingRateTimestamp": "1700000000000"}, ...]

        Example raw API call:
            GET https://api.bybit.com/v5/market/funding/history
                ?category=linear&symbol=ZECUSDT&limit=8
        """
        data = self._get(
            "/v5/market/funding/history",
            params={"category": category, "symbol": symbol, "limit": limit},
        )
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
            category: "linear" or "inverse"

        Returns:
            Single instrument info dict from Bybit.
        """
        data = self._get(
            "/v5/market/instruments-info",
            params={"category": category, "symbol": symbol},
        )
        items: list[dict] = data["result"]["list"]
        if not items:
            raise BybitAPIError(f"Symbol '{symbol}' not found on Bybit ({category})")
        return items[0]
