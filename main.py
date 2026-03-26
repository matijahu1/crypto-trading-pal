"""
main.py — application entry point.

All wiring (dependency injection) happens here.
Nothing in cli/, commands/, or services/ imports from main.py.
"""

from api.bybit_client import BybitClient
from services.funding_rate import FundingRateService
from commands.show import ShowCommand
from cli.loop import CommandLoop


def build_app() -> CommandLoop:
    """
    Construct and wire all application components.

    Returns:
        A ready-to-run CommandLoop.
    """
    # Infrastructure
    bybit_client = BybitClient()

    # Services
    funding_rate_service = FundingRateService(client=bybit_client, category="linear")

    # Commands  ← add new commands here as the app grows
    commands = [
        ShowCommand(funding_rate_service=funding_rate_service),
        # Future: WatchCommand(funding_rate_service, open_interest_service),
        # Future: ExportCommand(output_dir="./reports"),
    ]

    return CommandLoop(commands=commands)


if __name__ == "__main__":
    app = build_app()
    app.run()
