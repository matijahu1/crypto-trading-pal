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
from services.futures_position import FuturesPositionService
from services.trade_history import TradeHistoryService
from services.order_history import OrderHistoryService
from exporters.balance_exporter import BalanceExporter
from exporters.futures_position_exporter import FuturesPositionExporter
from exporters.trade_history_exporter import make_exporter as make_trade_exporter
from exporters.order_history_exporter import make_exporter as make_order_exporter


def main() -> None:
    """Run all batch exports sequentially."""
    load_dotenv()

    api_key    = os.getenv("BYBIT_API_KEY", "")
    api_secret = os.getenv("BYBIT_API_SECRET", "")
    testnet    = os.getenv("BYBIT_TESTNET", "false").lower() == "true"

    if not api_key or not api_secret:
        print("Error: BYBIT_API_KEY and BYBIT_API_SECRET must be set in your .env file.")
        sys.exit(1)

    # One shared client for all services
    client = BybitClient(testnet=testnet, api_key=api_key, api_secret=api_secret)

    _export_balances(client)
    _export_futures_positions(client)
    _export_trade_history(client)
    _export_order_history(client)


# ---------------------------------------------------------------------------
# Individual export steps — each is self-contained and independently failable
# ---------------------------------------------------------------------------

def _export_balances(client: BybitClient) -> None:
    """Fetch wallet balances and write data/balance.csv."""
    print("Fetching wallet balances...")

    service  = BalanceService(client=client, account_type="UNIFIED")
    exporter = BalanceExporter()

    try:
        wallet = service.get_balances()
    except BybitAPIError as exc:
        print(f"  Error: could not fetch balances — {exc}")
        return

    if not wallet.coins:
        print("  No non-zero balances found. data/balance.csv was not written.")
        return

    path = exporter.export(wallet)
    print(f"  Exported {len(wallet.coins)} coin(s) to {path}")
    for cb in wallet.coins:
        print(f"    {cb.coin}: total={cb.total}, available={cb.available}")


def _export_futures_positions(client: BybitClient) -> None:
    """Fetch open futures positions and write data/futures_positions.csv."""
    print("Fetching futures positions...")

    service  = FuturesPositionService(client=client, category="linear")
    exporter = FuturesPositionExporter()

    try:
        snapshot = service.get_positions()
    except BybitAPIError as exc:
        print(f"  Error: could not fetch positions — {exc}")
        return

    if not snapshot.positions:
        print("  No open positions found. data/futures_positions.csv was not written.")
        return

    path = exporter.export(snapshot)
    print(f"  Exported {len(snapshot.positions)} position(s) to {path}")
    for p in snapshot.positions:
        print(f"    {p.symbol} {p.side}: size={p.size}, entry={p.entry_price}, pnl={p.unrealized_pnl}")


def _export_trade_history(client: BybitClient) -> None:
    """Fetch trade history for a single contract and write its CSV."""
    # -----------------------------------------------------------------------
    # Change SYMBOL here to export a different contract.
    # -----------------------------------------------------------------------
    SYMBOL = "ZECUSDT"
    # -----------------------------------------------------------------------

    print(f"Fetching trade history for {SYMBOL}...")

    service  = TradeHistoryService(client=client, category="linear")
    exporter = make_trade_exporter(SYMBOL)

    try:
        history = service.get_history(SYMBOL)
    except BybitAPIError as exc:
        print(f"  Error: could not fetch trade history — {exc}")
        return

    if not history.trades:
        print(f"  No trade history found for {SYMBOL}. CSV was not written.")
        return

    path = exporter.export(history)
    print(f"  Exported {len(history.trades)} trade(s) to {path}")


def _export_order_history(client: BybitClient) -> None:
    """Fetch order history for a single contract and write its CSV."""
    # -----------------------------------------------------------------------
    # Change SYMBOL here to export a different contract.
    # -----------------------------------------------------------------------
    SYMBOL = "ZECUSDT"
    # -----------------------------------------------------------------------

    print(f"Fetching order history for {SYMBOL}...")

    service  = OrderHistoryService(client=client, category="linear")
    exporter = make_order_exporter(SYMBOL)

    try:
        history = service.get_history(SYMBOL)
    except BybitAPIError as exc:
        print(f"  Error: could not fetch order history — {exc}")
        return

    if not history.orders:
        print(f"  No order history found for {SYMBOL}. CSV was not written.")
        return

    path = exporter.export(history)
    print(f"  Exported {len(history.orders)} order(s) to {path}")


if __name__ == "__main__":
    main()
