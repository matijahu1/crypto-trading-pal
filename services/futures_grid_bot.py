"""
services/futures_grid_bot.py — service layer for Futures Grid Bot details.

Responsibilities:
  - Accept a list of bot IDs (sourced from config.json, not from the API)
  - Fetch the full detail record for each bot via the Bybit documented endpoint
  - Map API field names to clean internal names
  - Convert millisecond timestamps to human-readable date and time strings
  - Return structured result objects — NOT formatted strings

Bybit API reference:
  https://bybit-exchange.github.io/docs/v5/bot/futures-grid/get-detail

Design notes:
  Unlike trade_history (which paginates over a time window), grid bot details
  are a single request per bot ID.  The service therefore iterates the
  configured bot ID list and fires one API call per entry.

  A per-bot failure does NOT abort the entire run.  When a single detail
  call fails, the error is logged and that bot is skipped — the remaining
  bots in the list are still fetched and exported.

  Decimal is used for all numeric financial fields to avoid floating-point
  rounding errors and preserve the exact precision returned by the API.

Change log:
  - Initial implementation.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Any, Protocol

from utils.time_utils import ms_timestamp_to_date_time

log = logging.getLogger(__name__)

_ZERO = Decimal("0")


# ---------------------------------------------------------------------------
# Protocol — decouples the service from the concrete client (mockable in tests)
# ---------------------------------------------------------------------------


class FuturesGridBotClientProtocol(Protocol):
    """Minimal interface the service needs from an exchange API client."""

    def get_futures_grid_bot_detail(self, bot_id: str) -> dict[str, Any]: ...


# ---------------------------------------------------------------------------
# Result dataclasses
# ---------------------------------------------------------------------------


@dataclass
class FuturesGridBot:
    """
    Full detail record for a single Futures Grid Bot.

    All Decimal fields are sourced from the Bybit API response strings to
    preserve exact precision.  Empty-string / missing values default to
    Decimal("0").  All timestamp fields are split into separate date and
    time strings (UTC).
    """

    # Identifiers
    bot_id: str
    symbol: str

    # Bot configuration
    bot_status: str       # e.g. "Running", "Stopped"
    upper_price: Decimal  # upper boundary of the grid range
    lower_price: Decimal  # lower boundary of the grid range
    grid_num: int         # number of grid lines
    leverage: Decimal     # leverage applied to the position
    direction: str        # "Long", "Short", or "Neutral"

    # Financial summary
    investment: Decimal        # initial capital invested (USDT)
    total_investment: Decimal  # total capital including any additions (USDT)
    grid_profit: Decimal       # realised grid profit so far (USDT)
    unrealized_pnl: Decimal    # open position unrealised PnL (USDT)

    # Fill quantities
    filled_open_qty: Decimal   # total buy-side fill volume
    filled_close_qty: Decimal  # total sell-side fill volume

    # Timestamps (UTC)
    created_date: str  # e.g. "2024-03-15"
    created_time: str  # e.g. "10:22:05"
    stopped_date: str  # empty string when the bot is still running
    stopped_time: str  # empty string when the bot is still running


@dataclass
class FuturesGridBotSnapshot:
    """A collection of grid bot detail records for one symbol."""

    symbol: str
    bots: list[FuturesGridBot] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------


class FuturesGridBotService:
    """
    Fetches full detail records for Futures Grid Bots by their IDs.

    Bot ID source
    -------------
    Bybit does not expose an API to list bot IDs, so they must be supplied
    externally — typically from ``AppConfig.get_bot_ids(symbol)`` which reads
    the ``"futures_grid_bots"`` block in config.json.

    Fetch strategy
    --------------
    One API call is made per bot ID.  Results are collected into a single
    ``FuturesGridBotSnapshot``.

    Resilience
    ----------
    A single-bot API failure is caught, logged as an error, and skipped.
    The service continues with the remaining IDs so a single bad bot ID
    does not block the entire export.
    """

    def __init__(self, client: FuturesGridBotClientProtocol) -> None:
        """
        Args:
            client: API client satisfying FuturesGridBotClientProtocol.
                    In production this is ``BybitClient``; in tests it can be
                    any object that implements ``get_futures_grid_bot_detail``.
        """
        self._client = client

    def get_snapshot(
        self,
        symbol: str,
        bot_ids: list[str],
    ) -> FuturesGridBotSnapshot:
        """
        Fetch detail records for all *bot_ids* and return them as a snapshot.

        Args:
            symbol:  Futures symbol the bots belong to, e.g. ``"CCUSDT"``.
                     Used only for labelling the snapshot — the API call uses
                     the bot ID, not the symbol.
            bot_ids: List of Bybit bot ID strings.  Sourced from config.json.
                     An empty list is valid and results in an empty snapshot.

        Returns:
            FuturesGridBotSnapshot with one FuturesGridBot per successfully
            fetched bot ID.  Bots that could not be fetched are omitted.

        Notes:
            Failures for individual bot IDs are logged at ERROR level and
            silently skipped.  The caller receives whatever was successfully
            retrieved.
        """
        symbol = symbol.upper()

        if not bot_ids:
            log.warning(
                "No bot IDs configured for %s — returning empty snapshot", symbol
            )
            return FuturesGridBotSnapshot(symbol=symbol)

        log.info(
            "Fetching Futures Grid Bot details for %s: %d bot(s) configured",
            symbol,
            len(bot_ids),
        )

        bots: list[FuturesGridBot] = []

        for bot_id in bot_ids:
            log.debug("  Fetching detail for botId=%s", bot_id)
            try:
                raw = self._client.get_futures_grid_bot_detail(bot_id)
            except Exception as exc:  # noqa: BLE001
                log.error(
                    "Failed to fetch detail for botId=%s (%s): %s — skipping",
                    bot_id,
                    symbol,
                    exc,
                )
                continue

            if not raw:
                log.warning(
                    "Empty detail response for botId=%s (%s) — skipping",
                    bot_id,
                    symbol,
                )
                continue

            bots.append(_parse_bot(raw, bot_id, symbol))
            log.debug("  botId=%s fetched OK", bot_id)

        log.info(
            "Futures Grid Bot snapshot complete: %d/%d bot(s) fetched for %s",
            len(bots),
            len(bot_ids),
            symbol,
        )

        return FuturesGridBotSnapshot(symbol=symbol, bots=bots)


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------


def _parse_bot(raw: dict[str, Any], bot_id: str, symbol: str) -> FuturesGridBot:
    """
    Map a raw Bybit API detail dict to a ``FuturesGridBot`` dataclass.

    Missing or unparseable numeric fields default to Decimal("0").
    Missing timestamp fields default to empty strings.
    """
    created_ms = _to_int(raw.get("createdTime") or raw.get("createTime"))
    stopped_ms = _to_int(raw.get("stoppedTime") or raw.get("stopTime"))

    if created_ms:
        created_date, created_time = ms_timestamp_to_date_time(str(created_ms))
    else:
        created_date, created_time = "", ""

    if stopped_ms:
        stopped_date, stopped_time = ms_timestamp_to_date_time(str(stopped_ms))
    else:
        stopped_date, stopped_time = "", ""

    return FuturesGridBot(
        bot_id=str(raw.get("botId", bot_id)),
        symbol=str(raw.get("symbol", symbol)),
        bot_status=str(raw.get("botStatus") or raw.get("status", "")),
        upper_price=_to_decimal(raw.get("upperPrice")),
        lower_price=_to_decimal(raw.get("lowerPrice")),
        grid_num=_to_int(raw.get("gridNum")) or 0,
        leverage=_to_decimal(raw.get("leverage")),
        direction=str(raw.get("triggerDirection") or raw.get("direction", "")),
        investment=_to_decimal(raw.get("investment")),
        total_investment=_to_decimal(raw.get("totalInvestment")),
        grid_profit=_to_decimal(raw.get("gridProfit")),
        unrealized_pnl=_to_decimal(raw.get("unrealizedPnl")),
        filled_open_qty=_to_decimal(raw.get("filledOpenQty")),
        filled_close_qty=_to_decimal(raw.get("filledCloseQty")),
        created_date=created_date,
        created_time=created_time,
        stopped_date=stopped_date,
        stopped_time=stopped_time,
    )


def _to_decimal(value: Any) -> Decimal:
    """
    Safely convert an API value to Decimal.

    Bybit returns numeric fields as strings (e.g. ``"30.5"``).
    Returns Decimal("0") for None, empty string, or any unparseable value.
    """
    if value is None:
        return _ZERO
    try:
        return Decimal(str(value))
    except InvalidOperation:
        return _ZERO


def _to_int(value: Any) -> int:
    """
    Safely convert an API value to int.

    Returns 0 for None, empty string, or any unparseable value.
    """
    if value is None:
        return 0
    try:
        return int(str(value))
    except (ValueError, TypeError):
        return 0
