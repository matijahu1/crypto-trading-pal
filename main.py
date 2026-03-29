"""
main.py — batch entry point.

Run this file to export wallet balances to balance.csv:
    python main.py

For the interactive CLI, run:
    python cli.py

Credentials are loaded from a .env file via python-dotenv.
Required:
    BYBIT_API_KEY=...
    BYBIT_API_SECRET=...

Optional:
    BYBIT_TESTNET=true   (defaults to false / mainnet)
"""

import os
import sys
from dotenv import load_dotenv

from api.bybit_client import BybitClient, BybitAPIError
from services.balance import BalanceService
from exporters.balance_exporter import BalanceExporter


def main() -> None:
    """Fetch wallet balances and write them to balance.csv."""
    load_dotenv()

    api_key    = os.getenv("BYBIT_API_KEY", "")
    api_secret = os.getenv("BYBIT_API_SECRET", "")
    testnet    = os.getenv("BYBIT_TESTNET", "false").lower() == "true"

    if not api_key or not api_secret:
        print("Error: BYBIT_API_KEY and BYBIT_API_SECRET must be set in your .env file.")
        sys.exit(1)

    client   = BybitClient(testnet=testnet, api_key=api_key, api_secret=api_secret)
    service  = BalanceService(client=client, account_type="UNIFIED")
    exporter = BalanceExporter()  # writes to data/balance.csv by default

    print("Fetching wallet balances...")

    try:
        wallet = service.get_balances()
    except BybitAPIError as exc:
        print(f"Error: could not fetch balances — {exc}")
        sys.exit(1)

    if not wallet.coins:
        print("No non-zero balances found. data/balance.csv was not written.")
        return

    written_path = exporter.export(wallet)

    print(f"Exported {len(wallet.coins)} coin(s) to {written_path}")
    for cb in wallet.coins:
        print(f"  {cb.coin}: total={cb.total}, available={cb.available}")


if __name__ == "__main__":
    main()
