"""
cli.py — interactive CLI entry point.

Run this file to start the interactive terminal:
    python cli.py

For the batch exporter, run:
    python main.py

Credentials are loaded from a .env file (BYBIT_API_KEY, BYBIT_API_SECRET).
Application settings (logging, paths) are loaded from data/config.json.
"""

import logging
import os
import sys

from dotenv import load_dotenv

from config.config_loader import load_config, ConfigError
from config.logging_setup import setup_logging
from api.bybit_client import BybitClient
from services.funding_rate import FundingRateService
from services.balance import BalanceService
from commands.show import ShowCommand
from commands.balance import BalanceCommand
from cli.loop import CommandLoop

log = logging.getLogger(__name__)


def build_app() -> CommandLoop:
    """
    Bootstrap config + logging, then construct and wire all CLI components.

    Returns:
        A ready-to-run CommandLoop.
    """
    # 1. Load application config
    try:
        config = load_config()
    except ConfigError as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        sys.exit(1)

    # 2. Set up logging
    setup_logging(config)
    log.debug("CLI starting up")

    # 3. Load credentials from .env
    load_dotenv()
    api_key    = os.getenv("BYBIT_API_KEY", "")
    api_secret = os.getenv("BYBIT_API_SECRET", "")
    testnet    = os.getenv("BYBIT_TESTNET", "false").lower() == "true"

    # Infrastructure — one shared client for all services
    bybit_client = BybitClient(
        testnet=testnet,
        api_key=api_key,
        api_secret=api_secret,
    )
    log.debug("BybitClient initialised (testnet=%s)", testnet)

    # Services
    funding_rate_service = FundingRateService(client=bybit_client, category="linear")
    balance_service      = BalanceService(client=bybit_client, account_type="UNIFIED")

    # Commands
    commands = [
        ShowCommand(funding_rate_service=funding_rate_service),
        BalanceCommand(balance_service=balance_service),
    ]

    return CommandLoop(commands=commands)


if __name__ == "__main__":
    app = build_app()
    app.run()
