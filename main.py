"""
main.py — batch entry point.

Which actions run is controlled by the "actions.enabled" list in
data/config.json.  Comment out or remove any action name to skip that step.

The symbol used for trade history and order history actions is
read from "request_settings.symbol" in data/config.json.

The bot IDs used for the "grid_bots" action are read from
"futures_grid_bots.<SYMBOL>" in data/config.json.  Add bot IDs there to
enable the grid bot detail export for each symbol.

Available action names:
  "balances"             → data/exported/balance.csv
  "futures_positions"    → data/exported/futures_positions.csv
  "trade_history"        → data/exported/<symbol>_tradeHistory.csv
  "order_history"        → data/exported/<symbol>_orderHistory.csv
  "recent_executions"    → data/exported/ACCOUNT_recent_fills.csv
  "open_orders"          → data/exported/<symbol>_openOrders.csv
  "generate_lifo_report" → data/exported/<symbol>_lifo_inventory.csv
  "grid_bots"            → data/exported/<symbol>_FuturesGridBots.csv

Run:
    python main.py

For the interactive CLI, run:
    python cli.py

Credentials are loaded from a .env file (BYBIT_API_KEY, BYBIT_API_SECRET).
Application settings (logging, actions, paths) are loaded from data/config.json.
"""

import logging
import os
import sys
from typing import Callable, Optional

from dotenv import load_dotenv

from api.bybit_client import BybitAPIError, BybitClient
from config.config_loader import AppConfig, ConfigError, load_config
from config.logging_setup import setup_logging
from exporters.balance_exporter import BalanceExporter
from exporters.futures_position_exporter import FuturesPositionExporter
from exporters.order_history_exporter import OrderHistoryExporter
from exporters.order_history_merger import OrderHistoryMerger
from exporters.path_provider import PathProvider
from exporters.trade_history_exporter import TradeHistoryExporter
from services.balance import BalanceService
from services.futures_position import FuturesPositionService
from services.order_history import OrderHistory, OrderHistoryService
from services.trade_history import TradeHistoryService

log = logging.getLogger(__name__)


def main() -> None:
    """Bootstrap configuration, then run the enabled batch actions."""

    # 1. Load application config (data/config.json) — must come first
    try:
        config = load_config()
    except ConfigError as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        sys.exit(1)

    # 2. Set up logging from config
    setup_logging(config)
    log.info("Starting batch run")
    log.info("Enabled actions: %s", config.enabled_actions)

    # 3. Load credentials from .env (never stored in config.json)
    load_dotenv()
    api_key = os.getenv("BYBIT_API_KEY", "")
    api_secret = os.getenv("BYBIT_API_SECRET", "")
    testnet = os.getenv("BYBIT_TESTNET", "false").lower() == "true"

    if not api_key or not api_secret:
        log.error("BYBIT_API_KEY and BYBIT_API_SECRET must be set in your .env file")
        sys.exit(1)

    # 4. Build the shared API client
    client = BybitClient(testnet=testnet, api_key=api_key, api_secret=api_secret)
    log.debug("BybitClient initialised (testnet=%s)", testnet)

    # 5. Build PathProvider and ensure the output directory exists once
    paths = PathProvider(base_dir=config.exported_dir, symbol=config.symbol)
    paths.ensure_dir()
    log.debug("Export directory: %s", paths.base_dir)

    # 6. Dispatch — run only the actions listed in config
    _dispatch(client, config, paths)

    log.info("Batch run complete")


# ---------------------------------------------------------------------------
# Action registry
# ---------------------------------------------------------------------------


def _build_registry(
    client: BybitClient,
    config: AppConfig,
    paths: PathProvider,
) -> dict[str, Callable[[], None]]:
    """
    Map every known action name to a zero-argument callable.

    Adding a new action in future:
      1. Write the _run_* function below.
      2. Add a path method to PathProvider.
      3. Add the action name to ALL_ACTIONS in config_loader.py.
      4. Register it here.
    """
    lookback_days = config.lookback_days_default
    return {
        "balances": lambda: _run_balances(client, paths),
        "futures_positions": lambda: _run_futures_positions(client, paths),
        "trade_history": lambda: _run_trade_history(client, paths, lookback_days),
        "order_history": lambda: _run_order_history(client, paths, lookback_days),
        "recent_executions": lambda: _run_recent_executions(
            client, paths, paths.symbol, limit=10
        ),
        "open_orders": lambda: _run_open_orders(client, paths),
        "generate_lifo_report": lambda: _run_generate_lifo_report(client, paths),
        "grid_bots": lambda: _run_grid_bots(client, config, paths),
    }


def _dispatch(
    client: BybitClient,
    config: AppConfig,
    paths: PathProvider,
) -> None:
    if not config.enabled_actions:
        log.warning("No actions are enabled in config.json — nothing to do")
        return

    registry = _build_registry(client, config, paths)

    for action in config.enabled_actions:
        log.debug("Running action: %s", action)
        registry[action]()


# ---------------------------------------------------------------------------
# Individual action handlers
# ---------------------------------------------------------------------------


def _run_balances(client: BybitClient, paths: PathProvider) -> None:
    output_path = paths.balance_path()
    log.info("Fetching wallet balances → %s", output_path)

    service = BalanceService(client=client, account_type="UNIFIED")
    exporter = BalanceExporter(output_path)

    try:
        wallet = service.get_balances()
    except BybitAPIError as exc:
        log.error("Could not fetch balances: %s", exc)
        return

    if not wallet.coins:
        log.warning("No non-zero balances found — %s was not written", output_path)
        return

    path = exporter.export(wallet)
    log.info("Exported %d coin(s) to %s", len(wallet.coins), path)
    for cb in wallet.coins:
        log.debug("  %s: total=%s, available=%s", cb.coin, cb.total, cb.available)


def _run_futures_positions(client: BybitClient, paths: PathProvider) -> None:
    output_path = paths.futures_positions_path()
    log.info("Fetching futures positions → %s", output_path)

    service = FuturesPositionService(client=client, category="linear")
    exporter = FuturesPositionExporter(output_path)

    try:
        snapshot = service.get_positions()
    except BybitAPIError as exc:
        log.error("Could not fetch positions: %s", exc)
        return

    if not snapshot.positions:
        log.warning("No open positions found — %s was not written", output_path)
        return

    path = exporter.export(snapshot)
    log.info("Exported %d position(s) to %s", len(snapshot.positions), path)


def _run_trade_history(
    client: BybitClient, paths: PathProvider, lookback_days: int
) -> None:
    output_path = paths.trade_history_path()
    log.info("Fetching trade history for %s → %s", paths.symbol, output_path)

    service = TradeHistoryService(
        client=client, category="linear", lookback_days=lookback_days
    )
    exporter = TradeHistoryExporter(output_path)

    try:
        history = service.get_history(paths.symbol)
    except BybitAPIError as exc:
        log.error("Could not fetch trade history: %s", exc)
        return

    if not history.trades:
        log.warning(
            "No trade history found for %s — %s not written", paths.symbol, output_path
        )
        return

    path = exporter.export(history)
    log.info("Exported %d trade(s) to %s", len(history.trades), path)


def _run_order_history(
    client: BybitClient, paths: PathProvider, lookback_days: int
) -> None:
    """
    Fetch only "Filled" orders and write an incremental CSV.

    Strategy — Load-Merge-Sort-Overwrite:
      1. LOAD      — PathProvider resolves the output path; OrderHistoryMerger
                     loads any records already present in that CSV.
      2. FETCH     — OrderHistoryService requests only "Filled" orders from the
                     API (server-side filter), covering the configured lookback.
      3. MERGE     — OrderHistoryMerger deduplicates by order_id (safe because
                     "Filled" is a terminal state — records never change).
      4. SORT      — Merger returns the combined list sorted by updated_ts DESC.
      5. OVERWRITE — OrderHistoryExporter rewrites the full CSV with the merged,
                     sorted list.
    """
    output_path = paths.order_history_path()
    log.info("Processing filled order history for %s → %s", paths.symbol, output_path)

    merger = OrderHistoryMerger(output_path)
    service = OrderHistoryService(
        client=client,
        category="linear",
        lookback_days=lookback_days,
    )
    try:
        fresh = service.get_history(paths.symbol, order_status="Filled")
    except BybitAPIError as exc:
        log.error("Could not fetch order history for %s: %s", paths.symbol, exc)
        return

    combined = merger.merge(fresh.orders)

    if not combined:
        log.warning(
            "No filled orders found for %s — %s was not written",
            paths.symbol,
            output_path,
        )
        return

    exporter = OrderHistoryExporter(output_path)
    path = exporter.export(
        OrderHistory(symbol=paths.symbol, category="linear", orders=combined)
    )
    log.info(
        "Exported %d filled order(s) to %s (%d from API, %d already on disk)",
        len(combined),
        path,
        len(fresh.orders),
        max(0, len(combined) - len(fresh.orders)),
    )


def _run_recent_executions(
    client: BybitClient,
    paths: PathProvider,
    symbol: Optional[str],
    limit: int,
) -> None:
    from exporters.recent_executions_exporter import RecentExecutionsExporter
    from services.recent_executions import RecentExecutionService

    output_path = paths.recent_fills_path()
    context = symbol if symbol else "ACCOUNT-WIDE"
    log.info(
        "Fetching recent fills for %s (limit: %d) → %s", context, limit, output_path
    )

    service = RecentExecutionService(client=client, category="linear")
    exporter = RecentExecutionsExporter(output_path)

    try:
        history = service.get_recent_fills(symbol=symbol, limit=limit)
    except BybitAPIError as exc:
        log.error("Could not fetch recent executions for %s: %s", context, exc)
        return

    if not history.executions:
        log.warning(
            "No recent executions found for %s — %s not written", context, output_path
        )
        return

    path = exporter.export(history)
    log.info("Exported %d recent execution(s) to %s", len(history.executions), path)


def _run_open_orders(client: BybitClient, paths: PathProvider) -> None:
    """
    Fetch all currently active orders for the configured symbol and export
    them to ``{SYMBOL}_openOrders.csv``.

    Behaviour when no orders are found:
        An empty CSV (headers only) is written and an info message is logged.
        This keeps the output directory's file set consistent — downstream
        tools can always rely on the file being present after a successful run.
    """
    from exporters.open_orders_exporter import OpenOrdersExporter
    from services.open_orders import OpenOrderService

    output_path = paths.open_orders_path()
    log.info("Fetching open orders for %s → %s", paths.symbol, output_path)

    service = OpenOrderService(client=client, category="linear")
    exporter = OpenOrdersExporter(output_path)

    try:
        snapshot = service.get_open_orders(paths.symbol)
    except BybitAPIError as exc:
        log.error("Could not fetch open orders for %s: %s", paths.symbol, exc)
        return

    if not snapshot.orders:
        log.info(
            "No open orders found for %s — writing empty CSV to %s",
            paths.symbol,
            output_path,
        )

    path = exporter.export(snapshot)
    log.info(
        "Exported %d open order(s) to %s",
        len(snapshot.orders),
        path,
    )


def _run_generate_lifo_report(client: BybitClient, paths: PathProvider) -> None:
    """
    Generate a LIFO inventory report from the existing order history CSV.

    Input dependency:
        ``{SYMBOL}_orderHistory.csv`` in the configured export directory.
        Run the ``order_history`` action first if the file is absent.
    """
    from exporters.lifo_report_exporter import LifoReportExporter
    from services.lifo_report import LifoReportService

    input_path = paths.order_history_path()
    output_path = paths.lifo_report_path()

    log.info("Generating LIFO inventory report for %s → %s", paths.symbol, output_path)

    if not input_path.exists():
        log.error(
            "Error: Order history file not found for %s. "
            "Please run 'order_history' first. (Expected: %s)",
            paths.symbol,
            input_path,
        )
        return

    service = LifoReportService(input_path)
    try:
        records = service.generate()
    except (FileNotFoundError, ValueError) as exc:
        log.error("Could not generate LIFO report for %s: %s", paths.symbol, exc)
        return

    if not records:
        log.warning(
            "No lot records produced for %s — %s was not written",
            paths.symbol,
            output_path,
        )
        return

    exporter = LifoReportExporter(output_path)
    path = exporter.export(records)

    open_count = sum(1 for r in records if r.status == "OPEN")
    partial_count = sum(1 for r in records if r.status == "PARTIAL")
    closed_count = sum(1 for r in records if r.status == "CLOSED")
    total_pnl = sum(r.realized_pnl for r in records)

    log.info(
        "Exported %d lot(s) to %s — OPEN: %d, PARTIAL: %d, CLOSED: %d | "
        "Total realized PnL: %.4f",
        len(records),
        path,
        open_count,
        partial_count,
        closed_count,
        total_pnl,
    )


def _run_grid_bots(
    client: BybitClient,
    config: AppConfig,
    paths: PathProvider,
) -> None:
    """
    Fetch Futures Grid Bot details for the configured symbol using bot IDs
    defined in config.json, and export them to ``{SYMBOL}_FuturesGridBots.csv``.

    Bot ID source
    -------------
    Bot IDs are read from the ``"futures_grid_bots"`` block in config.json::

        "futures_grid_bots": {
            "CCUSDT": ["123456", "789012"],
            "ZECUSDT": ["345678", "333222"]
        }

    Only the IDs for the active symbol (``request_settings.symbol``) are
    fetched.  If no IDs are configured for that symbol, the action logs a
    warning and writes an empty CSV so downstream tools always find the file.

    Fetch strategy
    --------------
    One API call is made per bot ID (documented endpoint):
        GET /v5/bot/futures-grid/get-detail?botId=<id>

    A single-bot failure does NOT abort the export — it is logged and that
    bot is skipped.  All remaining bots are still fetched and written.

    Output
    ------
    ``data/exported/<SYMBOL>_FuturesGridBots.csv``
    """
    from exporters.futures_grid_bot_exporter import FuturesGridBotExporter
    from services.futures_grid_bot import FuturesGridBotService

    symbol = paths.symbol
    output_path = paths.base_dir / f"{symbol}_FuturesGridBots.csv"

    bot_ids = config.get_bot_ids(symbol)

    log.info(
        "Fetching Futures Grid Bot details for %s (%d bot ID(s) configured) → %s",
        symbol,
        len(bot_ids),
        output_path,
    )

    if not bot_ids:
        log.warning(
            "No bot IDs configured for %s under 'futures_grid_bots' in config.json. "
            "Add bot IDs to enable this export.  Writing empty CSV.",
            symbol,
        )

    service = FuturesGridBotService(client=client)
    exporter = FuturesGridBotExporter(output_path)

    try:
        snapshot = service.get_snapshot(symbol=symbol, bot_ids=bot_ids)
    except Exception as exc:  # noqa: BLE001 — surface any unexpected error
        log.error("Could not fetch grid bot details for %s: %s", symbol, exc)
        return

    path = exporter.export(snapshot)
    log.info(
        "Exported %d grid bot(s) to %s",
        len(snapshot.bots),
        path,
    )


if __name__ == "__main__":
    main()
