"""
main.py — batch entry point.

Which actions run is controlled by the "actions.enabled" list in
data/config.json.  Comment out or remove any action name to skip that step.

The symbol used for trade history and order history actions is
read from "request_settings.symbol" in data/config.json.

Available action names:
  "balances"          → data/exported/balance.csv
  "futures_positions" → data/exported/futures_positions.csv
  "trade_history"     → data/exported/<symbol>_tradeHistory.csv
  "order_history"     → data/exported/<symbol>_orderHistory.csv
  "recent_executions" → data/exported/ACCOUNT_recent_fills.csv

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

from config.config_loader import load_config, ConfigError
from config.logging_setup import setup_logging
from api.bybit_client import BybitClient, BybitAPIError
from exporters.path_provider import PathProvider
from services.balance import BalanceService
from services.futures_position import FuturesPositionService
from services.trade_history import TradeHistoryService
from services.order_history import OrderHistory, OrderHistoryService
from exporters.balance_exporter import BalanceExporter
from exporters.futures_position_exporter import FuturesPositionExporter
from exporters.trade_history_exporter import TradeHistoryExporter
from exporters.order_history_exporter import OrderHistoryExporter
from exporters.order_history_merger import OrderHistoryMerger

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
    log.debug("Enabled actions: %s", config.enabled_actions)

    # 3. Load credentials from .env (never stored in config.json)
    load_dotenv()
    api_key    = os.getenv("BYBIT_API_KEY", "")
    api_secret = os.getenv("BYBIT_API_SECRET", "")
    testnet    = os.getenv("BYBIT_TESTNET", "false").lower() == "true"

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
    _dispatch(client, config.enabled_actions, paths, config.lookback_days_default)

    log.info("Batch run complete")


# ---------------------------------------------------------------------------
# Action registry
# ---------------------------------------------------------------------------

def _build_registry(
    client: BybitClient,
    paths: PathProvider,
    lookback_days: int,
) -> dict[str, Callable[[], None]]:
    """
    Map every known action name to a zero-argument callable.

    Adding a new action in future:
      1. Write the _run_* function below.
      2. Add a path method to PathProvider.
      3. Add the action name to ALL_ACTIONS in config_loader.py.
      4. Register it here.
    """
    return {
        "balances":
            lambda: _run_balances(client, paths),
        "futures_positions":
            lambda: _run_futures_positions(client, paths),
        "trade_history":
            lambda: _run_trade_history(client, paths, lookback_days),
        "order_history":
            lambda: _run_order_history(client, paths, lookback_days),
        "recent_executions":
            lambda: _run_recent_executions(client, paths, paths.symbol, limit=10),
    }


def _dispatch(
    client: BybitClient,
    enabled_actions: list[str],
    paths: PathProvider,
    lookback_days: int,
) -> None:
    if not enabled_actions:
        log.warning("No actions are enabled in config.json — nothing to do")
        return

    registry = _build_registry(client, paths, lookback_days)

    for action in enabled_actions:
        log.debug("Running action: %s", action)
        registry[action]()


# ---------------------------------------------------------------------------
# Individual action handlers
# ---------------------------------------------------------------------------

def _run_balances(client: BybitClient, paths: PathProvider) -> None:
    output_path = paths.balance_path()
    log.info("Fetching wallet balances → %s", output_path)

    service  = BalanceService(client=client, account_type="UNIFIED")
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

    service  = FuturesPositionService(client=client, category="linear")
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

    service  = TradeHistoryService(client=client, category="linear", lookback_days=lookback_days)
    exporter = TradeHistoryExporter(output_path)

    try:
        history = service.get_history(paths.symbol)
    except BybitAPIError as exc:
        log.error("Could not fetch trade history: %s", exc)
        return

    if not history.trades:
        log.warning("No trade history found for %s — %s not written", paths.symbol, output_path)
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

    The output path is obtained from PathProvider *before* the API call so
    that a future "skip if up-to-date" check can be inserted at step 1
    without restructuring this function.
    """
    output_path = paths.order_history_path()
    log.info("Processing filled order history for %s → %s", paths.symbol, output_path)

    # ── 1. Initialise merger — loads existing CSV rows if the file exists ─────
    merger = OrderHistoryMerger(output_path)

    # ── 2. Fetch new "Filled" orders from the Bybit API ──────────────────────
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

    # ── 3 & 4. Merge + sort (dedup by order_id, sorted by updated_ts DESC) ───
    combined = merger.merge(fresh.orders)

    if not combined:
        log.warning(
            "No filled orders found for %s — %s was not written", paths.symbol, output_path
        )
        return

    # ── 5. Overwrite CSV with the complete, deduplicated, sorted list ─────────
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
    from services.recent_executions import RecentExecutionService
    from exporters.recent_executions_exporter import RecentExecutionsExporter

    output_path = paths.recent_fills_path()
    context     = symbol if symbol else "ACCOUNT-WIDE"
    log.info("Fetching recent fills for %s (limit: %d) → %s", context, limit, output_path)

    service  = RecentExecutionService(client=client, category="linear")
    exporter = RecentExecutionsExporter(output_path)

    try:
        history = service.get_recent_fills(symbol=symbol, limit=limit)
    except BybitAPIError as exc:
        log.error("Could not fetch recent executions for %s: %s", context, exc)
        return

    if not history.executions:
        log.warning("No recent executions found for %s — %s not written", context, output_path)
        return

    path = exporter.export(history)
    log.info("Exported %d recent execution(s) to %s", len(history.executions), path)


if __name__ == "__main__":
    main()
