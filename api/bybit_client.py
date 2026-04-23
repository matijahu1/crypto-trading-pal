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
  - Added get_grid_bots() and get_grid_bot_detail() for Futures Grid Bot
    data export.  Both methods use ``session._submit_request`` because
    Bybit does not publish these endpoints in its V5 REST documentation.
    See the NOTE in each method's docstring.
  - Added get_futures_grid_bot_detail() which calls the *documented* Bybit
    V5 endpoint for Futures Grid Bot detail:
      GET /v5/bot/futures-grid/get-detail
    This is the preferred method for fetching bot details by ID and is used
    by FuturesGridBotService.  The older get_grid_bot_detail() (undocumented
    endpoint) is retained for backward compatibility.
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

# ---------------------------------------------------------------------------
# Grid bot endpoint paths
# ---------------------------------------------------------------------------

# ⚠️  IMPORTANT — UNDOCUMENTED ENDPOINTS
#
# Bybit does not publish REST endpoints for querying the Futures Grid Bot
# list or detail in its V5 API documentation (bybit-exchange.github.io).
# The values below are the best-known paths based on network inspection of
# the Bybit web app as of early 2025.  They are subject to change without
# notice.
#
# To update them if Bybit changes its backend:
#   1. Open the Bybit trading bot page in a browser with DevTools → Network.
#   2. Navigate to "My Bots" and look for authenticated XHR/fetch calls.
#   3. Update the path constants below.
#
# If/when Bybit documents these endpoints officially, replace the path
# constants and switch from ``_submit_request`` to a proper pybit method.

_GRID_BOT_LIST_PATH = "/v5/bot/futures-grid/query-grid-list"
_GRID_BOT_DETAIL_PATH = "/v5/grid/get-grid-sub-order"

# Documented V5 endpoint for Futures Grid Bot detail (by bot ID).
# Reference: https://bybit-exchange.github.io/docs/v5/bot/futures-grid/get-detail
_FUTURES_GRID_BOT_DETAIL_PATH = "/v5/bot/futures-grid/get-detail"


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

        ret_code = response.get("retCode", 0)
        if ret_code != 0:
            log.warning(
                "Bybit server-time endpoint returned retCode=%d (%s) — "
                "running without clock correction.",
                ret_code,
                response.get("retMsg", "unknown"),
            )
            return

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

    # ------------------------------------------------------------------
    # Grid Bot methods (undocumented endpoints — see module docstring)
    # ------------------------------------------------------------------

    def get_grid_bots(
        self,
        symbol: str,
        category: str = "future",
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        """
        Return a list of active Futures Grid Bots for the given symbol.

        ⚠️  UNDOCUMENTED ENDPOINT — see ``_GRID_BOT_LIST_PATH`` for notes.

        The response ``result.list`` contains one record per active bot.
        Each record includes at minimum: ``botId``, ``symbol``, ``status``,
        ``investment``, ``gridProfit``, ``upperPrice``, ``lowerPrice``,
        ``gridNum``, ``leverage``, ``direction``, ``createdTime``.

        Args:
            symbol:   Futures symbol, e.g. "ICPUSDT".
            category: Bot category — "future" for Futures Grid (not "linear").
            limit:    Maximum number of records to return.

        Returns:
            List of raw bot dicts.  Empty list when no bots are active.

        Raises:
            BybitAPIError: On authentication failure or any API/network error.
        """
        params: dict[str, Any] = {
            "symbol": symbol.upper(),
            "category": category,
            "limit": limit,
        }
        try:
            response = self._session._submit_request(  # type: ignore[attr-defined]
                method="GET",
                path=_GRID_BOT_LIST_PATH,
                query=params,
                auth=True,
            )
        except Exception as exc:
            raise BybitAPIError(
                f"Failed to fetch grid bot list for {symbol}: {exc}"
            ) from exc

        data = self._unwrap(response, symbol)
        result = data.get("result", {})
        # The list may be nested under "list" or "gridList" depending on version
        bots: list[dict[str, Any]] = result.get("list") or result.get("gridList") or []
        return bots

    def get_grid_bot_detail(
        self,
        bot_id: str,
    ) -> dict[str, Any]:
        """
        Return the full detail record for a single Futures Grid Bot.

        ⚠️  UNDOCUMENTED ENDPOINT — see ``_GRID_BOT_DETAIL_PATH`` for notes.

        The response includes all fields from get_grid_bots() plus richer
        per-grid data: ``filledOpenQty``, ``filledCloseQty``,
        ``totalInvestment``, ``unrealizedPnl``, and more.

        Args:
            bot_id: The ``botId`` string returned by ``get_grid_bots()``.

        Returns:
            A single raw bot detail dict.

        Raises:
            BybitAPIError: On authentication failure, unknown botId, or any
                           API/network error.
        """
        params: dict[str, Any] = {"botId": bot_id}
        try:
            response = self._session._submit_request(  # type: ignore[attr-defined]
                method="GET",
                path=_GRID_BOT_DETAIL_PATH,
                query=params,
                auth=True,
            )
        except Exception as exc:
            raise BybitAPIError(
                f"Failed to fetch grid bot detail for botId={bot_id}: {exc}"
            ) from exc

        data = self._unwrap(response)
        return cast(dict[str, Any], data.get("result", {}))

    def get_futures_grid_bot_detail(
        self,
        bot_id: str,
    ) -> dict[str, Any]:
        """
        Return the full detail record for a single Futures Grid Bot using
        the **documented** Bybit V5 endpoint.

        API reference:
            https://bybit-exchange.github.io/docs/v5/bot/futures-grid/get-detail

        This is the preferred method used by ``FuturesGridBotService``.
        Unlike ``get_grid_bot_detail()`` (which hits an undocumented path),
        this method calls the officially documented endpoint and is therefore
        more stable.

        Request parameters:
            botId (str): The Bybit-assigned bot ID.  Obtain bot IDs from the
                         Bybit web UI or the Trading Bot section of the app,
                         then store them in ``config.json`` under
                         ``"futures_grid_bots"``.

        Response fields (commonly present):
            botId, symbol, botStatus, upperPrice, lowerPrice, gridNum,
            leverage, triggerDirection, investment, totalInvestment,
            gridProfit, unrealizedPnl, filledOpenQty, filledCloseQty,
            createdTime, stoppedTime.

        Args:
            bot_id: The ``botId`` string as shown in the Bybit UI and stored
                    in config.json.

        Returns:
            A single raw bot detail dict (the ``result`` object from the API
            response).  Returns an empty dict when the API result is absent.

        Raises:
            BybitAPIError: On authentication failure, an unknown botId, or
                           any API / network error.
        """
        params: dict[str, Any] = {"botId": bot_id}
        try:
            response = self._session._submit_request(  # type: ignore[attr-defined]
                method="GET",
                path=_FUTURES_GRID_BOT_DETAIL_PATH,
                query=params,
                auth=True,
            )
        except Exception as exc:
            raise BybitAPIError(
                f"Failed to fetch futures grid bot detail for botId={bot_id}: {exc}"
            ) from exc

        data = self._unwrap(response)
        return cast(dict[str, Any], data.get("result", {}))
