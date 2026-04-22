"""
services/grid_bot.py — service layer for Futures Grid Bot data.

Responsibilities:
  - Fetch the list of active grid bots for a symbol via BybitClient
  - Optionally enrich each bot with its detail record (unrealised PnL, etc.)
  - Map raw API field names to clean, typed internal names
  - Return structured result objects — NOT formatted strings
  - Use Decimal for all financial values (no floats)

Design notes:
  - The two-step fetch (list → detail per bot) mirrors the Bybit UI flow.
    ``GridBotService.get_snapshot()`` performs both steps automatically.
  - ``fetch_details=False`` can be passed to skip the per-bot detail calls
    (e.g. in tests or when only the summary list is needed).
  - All monetary and price fields use ``decimal.Decimal`` to preserve the
    exact precision returned by the API and avoid floating-point drift.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Any, Protocol

log = logging.getLogger(__name__)

_ZERO = Decimal("0")


# ---------------------------------------------------------------------------
# Protocol — decouples service from concrete client (mockable in tests)
# ---------------------------------------------------------------------------


class GridBotClientProtocol(Protocol):
    """Minimal interface the service needs from an exchange API client."""

    def get_grid_bots(
        self,
        symbol: str,
        category: str,
        limit: int,
    ) -> list[dict[str, Any]]: ...

    def get_grid_bot_detail(
        self,
        bot_id: str,
    ) -> dict[str, Any]: ...


# ---------------------------------------------------------------------------
# Result dataclasses
# ---------------------------------------------------------------------------


@dataclass
class GridBot:
    """
    A single Futures Grid Bot record.

    Fields populated from the list endpoint; detail fields are populated
    only when ``GridBotService.get_snapshot(fetch_details=True)`` is used.
    """

    # ── Identity ──────────────────────────────────────────────────────────
    bot_id:    str   # unique bot identifier
    symbol:    str   # e.g. "ICPUSDT"
    status:    str   # e.g. "Running", "Terminated"
    direction: str   # "neutral", "long", "short"

    # ── Grid configuration ────────────────────────────────────────────────
    upper_price: Decimal  # upper boundary of the price range
    lower_price: Decimal  # lower boundary of the price range
    grid_num:    int      # number of grid levels
    leverage:    Decimal  # contract leverage

    # ── Financial summary (list endpoint) ─────────────────────────────────
    investment:   Decimal  # USDT invested in the bot
    grid_profit:  Decimal  # realised grid profit so far (USDT)

    # ── Enriched fields (detail endpoint; default Decimal("0") if skipped) ─
    unrealized_pnl:   Decimal = field(default_factory=lambda: _ZERO)
    total_investment: Decimal = field(default_factory=lambda: _ZERO)
    filled_open_qty:  Decimal = field(default_factory=lambda: _ZERO)
    filled_close_qty: Decimal = field(default_factory=lambda: _ZERO)

    # ── Timestamps ────────────────────────────────────────────────────────
    created_time: str = ""  # UTC datetime string, e.g. "2024-03-01 12:00:00"


@dataclass
class GridBotSnapshot:
    """All active grid bots for a given symbol, as of the fetch time."""

    symbol:   str
    category: str
    bots:     list[GridBot]


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------


class GridBotService:
    """
    Fetches and assembles Futures Grid Bot data for a single symbol.

    Two-step fetch strategy:
    ------------------------
    Step 1 — List: ``get_grid_bots(symbol)`` returns a summary record for
        each active bot, including bot_id, grid config, and realised profit.

    Step 2 — Detail (optional): For each bot, ``get_grid_bot_detail(bot_id)``
        returns richer data including unrealised PnL and fill quantities.
        Set ``fetch_details=False`` to skip this step (saves N API calls).

    Typical usage::

        service  = GridBotService(client=client, category="future")
        snapshot = service.get_snapshot("ICPUSDT")
    """

    def __init__(
        self,
        client: GridBotClientProtocol,
        category: str = "future",
        limit: int = 50,
    ) -> None:
        """
        Args:
            client:   API client satisfying GridBotClientProtocol.
            category: Grid bot category — "future" for Futures Grid.
            limit:    Maximum number of bots to retrieve from the list
                      endpoint per call.
        """
        self._client   = client
        self._category = category
        self._limit    = limit

    def get_snapshot(
        self,
        symbol: str,
        fetch_details: bool = True,
    ) -> GridBotSnapshot:
        """
        Return all active grid bots for *symbol*.

        Args:
            symbol:        Futures symbol, e.g. "ICPUSDT".
                           Uppercase is enforced automatically.
            fetch_details: When ``True`` (default), each bot is enriched
                           with a second API call for its detail record.
                           Set to ``False`` to use list-only data.

        Returns:
            GridBotSnapshot.  The ``bots`` list is empty when there are
            no active bots for the symbol.

        Raises:
            BybitAPIError: Propagated from the client on network / API errors.
        """
        symbol = symbol.upper()

        log.info(
            "Fetching grid bot list: symbol=%s category=%s fetch_details=%s",
            symbol, self._category, fetch_details,
        )

        raw_list = self._client.get_grid_bots(
            symbol=symbol,
            category=self._category,
            limit=self._limit,
        )

        log.debug("Grid bot list returned %d record(s) for %s", len(raw_list), symbol)

        bots: list[GridBot] = []
        for raw in raw_list:
            bot = _map_list_record(raw, symbol)

            if fetch_details:
                try:
                    detail = self._client.get_grid_bot_detail(bot.bot_id)
                    _enrich_from_detail(bot, detail)
                    log.debug("  Enriched bot %s with detail data", bot.bot_id)
                except Exception as exc:
                    # Detail enrichment is best-effort: a failure here should
                    # not abort the entire snapshot.  Log and continue.
                    log.warning(
                        "  Could not fetch detail for bot %s: %s — "
                        "detail fields will remain at default values",
                        bot.bot_id, exc,
                    )

            bots.append(bot)

        log.info(
            "Grid bot snapshot complete: %d bot(s) for %s", len(bots), symbol
        )

        return GridBotSnapshot(
            symbol=symbol,
            category=self._category,
            bots=bots,
        )


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------


def _map_list_record(raw: dict[str, Any], fallback_symbol: str) -> GridBot:
    """Map a raw list-endpoint dict to a GridBot dataclass."""
    return GridBot(
        bot_id=str(raw.get("botId", "")),
        symbol=str(raw.get("symbol", fallback_symbol)),
        status=str(raw.get("status", "")),
        direction=str(raw.get("direction", "")),
        upper_price=_to_decimal(raw.get("upperPrice")),
        lower_price=_to_decimal(raw.get("lowerPrice")),
        grid_num=_to_int(raw.get("gridNum")),
        leverage=_to_decimal(raw.get("leverage")),
        investment=_to_decimal(raw.get("investment")),
        grid_profit=_to_decimal(raw.get("gridProfit")),
        created_time=_ms_to_utc_string(raw.get("createdTime")),
    )


def _enrich_from_detail(bot: GridBot, detail: dict[str, Any]) -> None:
    """Mutate *bot* in place with the additional fields from the detail endpoint."""
    bot.unrealized_pnl   = _to_decimal(detail.get("unrealizedPnl"))
    bot.total_investment = _to_decimal(detail.get("totalInvestment"))
    bot.filled_open_qty  = _to_decimal(detail.get("filledOpenQty"))
    bot.filled_close_qty = _to_decimal(detail.get("filledCloseQty"))
    # The detail endpoint may provide a more precise investment figure;
    # override the list-level value if present.
    if detail.get("investment"):
        bot.investment = _to_decimal(detail.get("investment"))


def _to_decimal(value: Any) -> Decimal:
    """Safely convert an API value to Decimal (Bybit returns numerics as strings)."""
    if value is None:
        return _ZERO
    try:
        return Decimal(str(value))
    except InvalidOperation:
        return _ZERO


def _to_int(value: Any) -> int:
    """Safely convert an API value to int."""
    if value is None:
        return 0
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _ms_to_utc_string(value: Any) -> str:
    """
    Convert a millisecond Unix timestamp (int or string) to a UTC datetime
    string in the format ``"YYYY-MM-DD HH:MM:SS"``.
    Returns ``""`` for None, zero, or invalid values.
    """
    if value is None:
        return ""
    try:
        ms = int(value)
    except (TypeError, ValueError):
        return ""
    if ms <= 0:
        return ""

    import datetime
    dt = datetime.datetime.fromtimestamp(ms / 1000, tz=datetime.timezone.utc)
    return dt.strftime("%Y-%m-%d %H:%M:%S")
