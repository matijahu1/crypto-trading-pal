"""
api/bybit_client.py — wraps the official ``pybit`` SDK.

Change log:
  - get_order_history() gains an optional ``order_status`` parameter.
    When provided, it is forwarded to Bybit as ``orderStatus`` so the
    server returns only matching orders (e.g. ``"Filled"``).
    Pass ``None`` (the default) to retrieve all statuses as before.
  - BybitClient.__init__() now accepts ``recv_window`` (default: 10_000 ms)
    and ``sync_time`` (default: True).
  - When ``sync_time=True``, the constructor fetches the Bybit server time
    once, computes the offset between local and server clocks, and patches
    ``pybit._helpers.generate_timestamp`` so that every subsequent signed
    request uses a corrected timestamp.  This prevents ErrCode 10002
    ("request expired / invalid timestamp") on machines whose clocks drift.
"""

from __future__ import annotations

import logging
import time
from typing import Any, cast

import pybit._helpers as _pybit_helpers  # pyright: ignore[reportMissingTypeStubs]
from pybit.unified_trading import HTTP  # pyright: ignore[reportMissingTypeStubs]

log = logging.getLogger(__name__)

# Bybit accepts timestamps within ±recv_window ms of server time.
# The API hard-caps recv_window at 60 000 ms; 10 000 is a safe default
# that gives 10 s of tolerance without weakening replay protection.
_DEFAULT_RECV_WINDOW: int = 10_000


class BybitAPIError(Exception):
    """Raised when pybit or the Bybit API signals an error."""


class BybitClient:
    """
    Thin adapter around pybit's unified HTTP session.

    Args:
        testnet:     ``False`` → mainnet (default), ``True`` → testnet.
        api_key:     Bybit API key.
        api_secret:  Bybit API secret.
        recv_window: Maximum age (ms) Bybit will accept for a signed request.
                     Default is 10 000 ms.  Bybit hard-caps this at 60 000 ms.
        sync_time:   When ``True`` (default), fetch the Bybit server time at
                     startup, compute the local-to-server clock offset, and
                     patch pybit's timestamp generator so all subsequent
                     signed requests carry a corrected timestamp.  Set to
                     ``False`` to skip the network call (e.g. in unit tests).

    Clock synchronisation detail:
        pybit builds the ``X-BAPI-TIMESTAMP`` header by calling
        ``pybit._helpers.generate_timestamp()``, which returns
        ``int(time.time() * 1000)``.  We replace that function with a closure
        that adds the measured offset, so no pybit internals need forking.

        offset = server_time_ms - local_time_ms   (measured at startup)

        corrected_timestamp = int(time.time() * 1000) + offset

        The offset is re-measured only at construction time.  If the local
        clock drifts significantly during a long run, reconstruct the client.
    """

    def __init__(
        self,
        testnet: bool = False,
        api_key: str = "",
        api_secret: str = "",
        recv_window: int = _DEFAULT_RECV_WINDOW,
        sync_time: bool = True,
    ) -> None:
        self._session = HTTP(
            testnet=testnet,
            api_key=api_key or None,
            api_secret=api_secret or None,
            recv_window=recv_window,
        )
        log.debug(
            "BybitClient initialised (testnet=%s, recv_window=%d ms)",
            testnet,
            recv_window,
        )

        if sync_time:
            self._apply_time_offset()

    # ------------------------------------------------------------------
    # Time synchronisation
    # ------------------------------------------------------------------

    def _apply_time_offset(self) -> None:
        """
        Fetch the Bybit server time, compute the clock offset, and patch
        pybit's timestamp generator with a corrected version.

        The patch is process-wide (module-level function replacement) because
        pybit calls ``_helpers.generate_timestamp()`` by direct import inside
        ``_V5HTTPManager._prepare_headers()``.  Replacing the function on the
        module object is the only way to intercept it without forking pybit.

        If the server-time request fails for any reason, we log a warning and
        leave the timestamp generator untouched rather than crashing startup.
        """
        try:
            # Record local time immediately before and after the call so we
            # can estimate the midpoint (reduces one-way latency bias).
            t_before_ms = int(time.time() * 1_000)
            response = self._session.get_server_time()  # type: ignore[attr-defined]
            t_after_ms = int(time.time() * 1_000)
        except Exception as exc:
            log.warning(
                "Could not fetch Bybit server time — running without clock "
                "correction.  Original error: %s",
                exc,
            )
            return

        # _unwrap will raise BybitAPIError on retCode != 0, but get_server_time
        # is public so we handle it inline here to keep startup safe.
        ret_code = response.get("retCode", 0)
        if ret_code != 0:
            log.warning(
                "Bybit server-time endpoint returned retCode=%d (%s) — "
                "running without clock correction.",
                ret_code,
                response.get("retMsg", "unknown"),
            )
            return

        # Bybit returns timeSecond as a string of Unix seconds.
        try:
            server_time_ms = int(response["result"]["timeSecond"]) * 1_000
        except (KeyError, ValueError, TypeError) as exc:
            log.warning(
                "Unexpected server-time response format — running without "
                "clock correction.  Error: %s",
                exc,
            )
            return

        # Use the midpoint of the round-trip as the local reference time
        # to minimise one-sided latency bias.
        local_midpoint_ms = (t_before_ms + t_after_ms) // 2
        offset_ms = server_time_ms - local_midpoint_ms

        if abs(offset_ms) < 500:
            # Offset is negligible — no patch needed.
            log.debug(
                "Clock offset is %+d ms — within tolerance, no correction applied.",
                offset_ms,
            )
            return

        log.info(
            "Clock offset detected: local is %+d ms relative to Bybit server. "
            "Patching pybit timestamp generator.",
            offset_ms,
        )

        # Capture offset_ms in a closure and replace the module-level function.
        _captured_offset = offset_ms

        def _corrected_timestamp() -> int:
            return int(time.time() * 1_000) + _captured_offset

        _pybit_helpers.generate_timestamp = _corrected_timestamp  # type: ignore[attr-defined]

        log.debug(
            "pybit._helpers.generate_timestamp patched with offset %+d ms.",
            offset_ms,
        )

    def get_server_time_ms(self) -> int:
        """
        Return the current Bybit server time as a Unix millisecond integer.

        Useful for callers that need a reliable "now" reference independent
        of local clock skew (e.g. computing lookback windows).

        Raises:
            BybitAPIError: if the request fails or returns a non-zero retCode.
        """
        try:
            response = self._session.get_server_time()  # type: ignore[attr-defined]
        except Exception as exc:
            raise BybitAPIError(f"Failed to fetch server time: {exc}") from exc

        self._unwrap(response)
        try:
            return int(response["result"]["timeSecond"]) * 1_000
        except (KeyError, ValueError, TypeError) as exc:
            raise BybitAPIError(
                f"Unexpected server-time response format: {exc}"
            ) from exc

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
        symbol: str,
        category: str = "linear",
        limit: int = 8,
    ) -> list[dict[str, Any]]:
        try:
            response = self._session.get_funding_rate_history(  # type: ignore
                category=category,
                symbol=symbol,
                limit=limit,
            )
        except Exception as exc:
            raise BybitAPIError(f"Failed to fetch funding rate history: {exc}") from exc
        return self._unwrap(response, symbol)["result"]["list"]  # type: ignore

    def get_instruments_info(
        self,
        symbol: str,
        category: str = "linear",
    ) -> dict[str, Any]:
        try:
            response = self._session.get_instruments_info(  # type: ignore
                category=category,
                symbol=symbol,
            )
        except Exception as exc:
            raise BybitAPIError(f"Failed to fetch instruments info: {exc}") from exc
        data = self._unwrap(response, symbol)  # type: ignore
        items: list[dict[str, Any]] = data["result"]["list"]
        if not items:
            raise BybitAPIError(f"Symbol '{symbol}' not found on Bybit ({category})")
        return items[0]

    def get_wallet_balance(self, account_type: str = "UNIFIED") -> list[dict[str, Any]]:
        try:
            response = self._session.get_wallet_balance(accountType=account_type)  # type: ignore
        except Exception as exc:
            raise BybitAPIError(f"Failed to fetch wallet balance: {exc}") from exc
        accounts: list[dict[str, Any]] = self._unwrap(response)["result"]["list"]  # type: ignore
        return accounts[0].get("coin", []) if accounts else []

    def get_positions(self, category: str = "linear") -> list[dict[str, Any]]:
        try:
            response = self._session.get_positions(  # type: ignore
                category=category,
                settleCoin="USDT" if category == "linear" else "BTC",
            )
        except Exception as exc:
            raise BybitAPIError(f"Failed to fetch positions: {exc}") from exc
        return self._unwrap(response)["result"]["list"]  # type: ignore

    def get_trade_history(
        self,
        symbol: str,
        category: str = "linear",
        limit: int = 100,
        start_time: int | None = None,
        end_time: int | None = None,
    ) -> list[dict[str, Any]]:
        kwargs: dict[str, Any] = dict(category=category, symbol=symbol, limit=limit)
        if start_time is not None:
            kwargs["startTime"] = start_time
        if end_time is not None:
            kwargs["endTime"] = end_time
        try:
            response = self._session.get_executions(**kwargs)  # type: ignore
        except Exception as exc:
            raise BybitAPIError(f"Failed to fetch trade history: {exc}") from exc
        return self._unwrap(response, symbol)["result"]["list"]  # type: ignore

    def get_order_history(
        self,
        symbol: str,
        category: str = "linear",
        limit: int = 50,
        start_time: int | None = None,
        end_time: int | None = None,
        order_status: str | None = None,
    ) -> list[dict[str, Any]]:
        """
        Fetch order history for a single symbol.

        Args:
            symbol:       Perpetual futures symbol, e.g. "ICPUSDT".
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
            kwargs["orderStatus"] = order_status

        try:
            response = self._session.get_order_history(**kwargs)  # type: ignore
        except Exception as exc:
            raise BybitAPIError(f"Failed to fetch order history: {exc}") from exc

        data = self._unwrap(response, symbol)  # type: ignore
        return cast(list[dict[str, Any]], data["result"]["list"])

    def get_executions(
        self,
        symbol: str | None = None,
        category: str = "linear",
        limit: int = 100,
        exec_type: str | None = None,
    ) -> list[dict[str, Any]]:
        kwargs: dict[str, Any] = {"category": category, "limit": limit}
        if symbol and symbol != "ACCOUNT":
            kwargs["symbol"] = symbol
        if exec_type:
            kwargs["execType"] = exec_type
        try:
            response = self._session.get_executions(**kwargs)  # type: ignore
        except Exception as exc:
            context = symbol if symbol else "ACCOUNT"
            raise BybitAPIError(
                f"Failed to fetch executions for {context}: {exc}"
            ) from exc
        return self._unwrap(response, symbol)["result"]["list"]  # type: ignore

    def get_open_orders(
        self,
        symbol: str,
        category: str = "linear",
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        """
        Fetch all currently active (unfilled) orders for *symbol*.

        Args:
            symbol:   Perpetual futures symbol, e.g. "ICPUSDT".
            category: "linear" or "inverse".
            limit:    Max orders per call (Bybit maximum: 50).

        Returns:
            List of order dicts for active orders (status "New",
            "PartiallyFilled", etc.).  Empty list when none are open.

        Raises:
            BybitAPIError: On auth failure or any API / network error.
        """
        kwargs: dict[str, Any] = dict(category=category, symbol=symbol, limit=limit)
        try:
            response = self._session.get_open_orders(**kwargs)  # type: ignore
        except Exception as exc:
            raise BybitAPIError(f"Failed to fetch open orders: {exc}") from exc

        data = self._unwrap(response, symbol)
        return cast(list[dict[str, Any]], data["result"]["list"])
