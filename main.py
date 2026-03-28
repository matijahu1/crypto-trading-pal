"""
main.py — application entry point.

All wiring (dependency injection) happens here.
Nothing in cli/, commands/, or services/ imports from main.py.

Credentials are loaded from a .env file via python-dotenv.
Required for authenticated commands (e.g. balance):
    BYBIT_API_KEY=...
    BYBIT_API_SECRET=...

Optional:
    BYBIT_TESTNET=true   (defaults to false / mainnet)
"""

import os
from dotenv import load_dotenv

from api.bybit_client import BybitClient
from services.funding_rate import FundingRateService
from services.balance import BalanceService
from commands.show import ShowCommand
from commands.balance import BalanceCommand
from cli.loop import CommandLoop


def build_app() -> CommandLoop:
    """
    Construct and wire all application components.

    Returns:
        A ready-to-run CommandLoop.
    """
    # Load .env into the process environment (safe no-op if file is absent)
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

    # Services
    funding_rate_service = FundingRateService(client=bybit_client, category="linear")
    balance_service      = BalanceService(client=bybit_client, account_type="UNIFIED")

    # Commands  <- add new commands here as the app grows
    commands = [
        ShowCommand(funding_rate_service=funding_rate_service),
        BalanceCommand(balance_service=balance_service),
        # Future: WatchCommand(funding_rate_service, open_interest_service),
        # Future: ExportCommand(output_dir="./reports"),
    ]

    return CommandLoop(commands=commands)


if __name__ == "__main__":
    app = build_app()
    app.run()
