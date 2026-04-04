"""
main.py — batch entry point.

Exports four CSV files to the data/ directory:
  1. data/balance.csv                  — non-zero wallet balances
  2. data/futures_positions.csv        — open futures positions (non-zero size)
  3. data/ZECUSDT_tradeHistory.csv     — recent trade executions for ZECUSDT
  4. data/ZECUSDT_orderHistory.csv     — recent order history for ZECUSDT

Run:
    python main.py

For the interactive CLI, run:
    python cli.py

Credentials are loaded from a .env file (BYBIT_API_KEY, BYBIT_API_SECRET).
Application settings (logging, paths) are loaded from data/config.json.
"""

import logging
import os
import sys

from dotenv import load_dotenv

from config.config_loader import load_config, ConfigError
from config.logging_setup import setup_logging
from api.bybit_client import BybitClient, BybitAPIError
from services.balance import BalanceService
from services.futures_position import FuturesPositionService
from services.trade_history import TradeHistoryService
from services.order_history import OrderHistoryService
from exporters.balance_exporter import BalanceExporter
from exporters.futures_position_exporter import FuturesPositionExporter
from exporters.trade_history_exporter import make_exporter as make_trade_exporter
from exporters.order_history_exporter import make_exporter as make_order_exporter

log = logging.getLogger(__name__)


def main() -> None:
    """Bootstrap configuration, then run all batch exports sequentially."""

    # 1. Load application config (data/config.json) — must come first
    try:
        config = load_config()
    except ConfigError as exc:
        # Logging is not set up yet, so print is the only option here
        print(f"Configuration error: {exc}", file=sys.stderr)
        sys.exit(1)

    # 2. Set up logging from config — all subsequent output goes through logging
    setup_logging(config)
    log.info("Starting batch export run")

    # 3. Load credentials from .env (never stored in config.json)
    load_dotenv()
    api_key    = os.getenv("BYBIT_API_KEY", "")
    api_secret = os.getenv("BYBIT_API_SECRET", "")
    testnet    = os.getenv("BYBIT_TESTNET", "false").lower() == "true"

    if not api_key or not api_secret:
        log.error(
            "BYBIT_API_KEY and BYBIT_API_SECRET must be set in your .env file"
        )
        sys.exit(1)

    # 4. Build the shared API client
    client = BybitClient(testnet=testnet, api_key=api_key, api_secret=api_secret)
    log.debug("BybitClient initialised (testnet=%s)", testnet)

    # 5. Run exports
    _export_balances(client)
    _export_futures_positions(client)
    _export_trade_history(client)
    _export_order_history(client)

    log.info("Batch export run complete")


# ---------------------------------------------------------------------------
# Individual export steps — each is self-contained and independently failable
# ---------------------------------------------------------------------------

def _export_balances(client: BybitClient) -> None:
    """Fetch wallet balances and write data/balance.csv."""
    log.info("Fetching wallet balances...")

    service  = BalanceService(client=client, account_type="UNIFIED")
    exporter = BalanceExporter()

    try:
        wallet = service.get_balances()
    except BybitAPIError as exc:
        log.error("Could not fetch balances: %s", exc)
        return

    if not wallet.coins:
        log.warning("No non-zero balances found — data/balance.csv was not written")
        return

    path = exporter.export(wallet)
    log.info("Exported %d coin(s) to %s", len(wallet.coins), path)
    for cb in wallet.coins:
        log.debug("  %s: total=%s, available=%s", cb.coin, cb.total, cb.available)


def _export_futures_positions(client: BybitClient) -> None:
    """Fetch open futures positions and write data/futures_positions.csv."""
    log.info("Fetching futures positions...")

    service  = FuturesPositionService(client=client, category="linear")
    exporter = FuturesPositionExporter()

    try:
        snapshot = service.get_positions()
    except BybitAPIError as exc:
        log.error("Could not fetch positions: %s", exc)
        return

    if not snapshot.positions:
        log.warning("No open positions found — data/futures_positions.csv was not written")
        return

    path = exporter.export(snapshot)
    log.info("Exported %d position(s) to %s", len(snapshot.positions), path)
    for p in snapshot.positions:
        log.debug(
            "  %s %s: size=%s, entry=%s, pnl=%s",
            p.symbol, p.side, p.size, p.entry_price, p.unrealized_pnl,
        )


def _export_trade_history(client: BybitClient) -> None:
    """Fetch trade history for a single contract and write its CSV."""
    # -----------------------------------------------------------------------
    # Change SYMBOL here to export a different contract.
    # -----------------------------------------------------------------------
    SYMBOL = "ZECUSDT"
    # -----------------------------------------------------------------------

    log.info("Fetching trade history for %s...", SYMBOL)

    service  = TradeHistoryService(client=client, category="linear")
    exporter = make_trade_exporter(SYMBOL)

    try:
        history = service.get_history(SYMBOL)
    except BybitAPIError as exc:
        log.error("Could not fetch trade history: %s", exc)
        return

    if not history.trades:
        log.warning("No trade history found for %s — CSV was not written", SYMBOL)
        return

    path = exporter.export(history)
    log.info("Exported %d trade(s) to %s", len(history.trades), path)


def _export_order_history(client: BybitClient) -> None:
    """Fetch order history for a single contract and write its CSV."""
    # -----------------------------------------------------------------------
    # Change SYMBOL here to export a different contract.
    # -----------------------------------------------------------------------
    SYMBOL = "ZECUSDT"
    # -----------------------------------------------------------------------

    log.info("Fetching order history for %s...", SYMBOL)

    service  = OrderHistoryService(client=client, category="linear")
    exporter = make_order_exporter(SYMBOL)

    try:
        history = service.get_history(SYMBOL)
    except BybitAPIError as exc:
        log.error("Could not fetch order history: %s", exc)
        return

    if not history.orders:
        log.warning("No order history found for %s — CSV was not written", SYMBOL)
        return

    path = exporter.export(history)
    log.info("Exported %d order(s) to %s", len(history.orders), path)


if __name__ == "__main__":
    main()
