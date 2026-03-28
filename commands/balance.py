"""
'balance [COIN]' command.

Displays wallet balances for the authenticated Bybit account.
Presentation logic lives here; business logic stays in BalanceService.
"""

from __future__ import annotations

from commands.base import BaseCommand
from services.balance import BalanceService, WalletBalance
from api.bybit_client import BybitAPIError


class BalanceCommand(BaseCommand):
    """Show wallet balances, optionally filtered to a single coin."""

    @property
    def name(self) -> str:
        return "balance"

    @property
    def usage(self) -> str:
        return "balance [COIN]  (e.g. balance  |  balance BTC)"

    def __init__(self, balance_service: BalanceService) -> None:
        """
        Args:
            balance_service: Injected service for balance retrieval.
        """
        self._balance_svc = balance_service

    # ------------------------------------------------------------------
    # Command entry-point
    # ------------------------------------------------------------------

    def execute(self, args: list[str]) -> None:
        coin_filter = args[0].upper() if args else None

        header = f"  Wallet Balance — {coin_filter}" if coin_filter else "  Wallet Balance — All coins"
        print(f"\n{'═' * 52}")
        print(header)
        print(f"{'═' * 52}")

        try:
            result = self._balance_svc.get_balances(coin_filter=coin_filter)
        except BybitAPIError as exc:
            # Surface auth errors (missing keys, wrong keys) clearly
            msg = str(exc)
            if "10003" in msg or "10004" in msg or "apiKey" in msg.lower():
                print("  ⚠  Authentication failed.")
                print("     Check that BYBIT_API_KEY and BYBIT_API_SECRET are")
                print("     set correctly in your .env file.")
            else:
                print(f"  ⚠  API error: {exc}")
            return
        except Exception as exc:
            print(f"  ⚠  Unexpected error: {exc}")
            return

        self._render(result, coin_filter)

    # ------------------------------------------------------------------
    # Renderer
    # ------------------------------------------------------------------

    @staticmethod
    def _render(result: WalletBalance, coin_filter: str | None) -> None:
        if not result.coins:
            if coin_filter:
                print(f"\n  '{coin_filter}' not found or balance is zero.")
            else:
                print("\n  No non-zero balances found.")
            print()
            return

        print()
        col_coin = 8
        col_total = 20
        col_avail = 20

        header = f"  {'Coin':<{col_coin}} {'Total':>{col_total}} {'Available':>{col_avail}}"
        print(header)
        print(f"  {'─' * (col_coin + col_total + col_avail + 2)}")

        for cb in result.coins:
            print(
                f"  {cb.coin:<{col_coin}}"
                f" {_fmt_amount(cb.total):>{col_total}}"
                f" {_fmt_amount(cb.available):>{col_avail}}"
            )

        print()


# ---------------------------------------------------------------------------
# Formatting helper
# ---------------------------------------------------------------------------

def _fmt_amount(value: float) -> str:
    """
    Format a coin amount with up to 8 decimal places, stripping trailing zeros.

    Examples:
        0.25      → "0.25"
        1.0       → "1.0"
        0.00012300 → "0.000123"
    """
    return f"{value:.8f}".rstrip("0").rstrip(".") or "0"
