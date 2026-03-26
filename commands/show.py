"""
'show <SYMBOL>' command.

Orchestrates data retrieval via services and formats output for the terminal.
Presentation logic lives here; business logic stays in the service layer.
"""

from __future__ import annotations

from commands.base import BaseCommand
from services.funding_rate import FundingRateService, FundingRateAnalysis
from api.bybit_client import BybitAPIError


class ShowCommand(BaseCommand):
    """Display funding rate analysis (and future data) for a perpetual symbol."""

    @property
    def name(self) -> str:
        return "show"

    @property
    def usage(self) -> str:
        return "show <SYMBOL>  (e.g. show ZECUSDT)"

    def __init__(self, funding_rate_service: FundingRateService) -> None:
        """
        Args:
            funding_rate_service: Injected service for funding rate analysis.
        """
        self._funding_svc = funding_rate_service

    # ------------------------------------------------------------------
    # Command entry-point
    # ------------------------------------------------------------------

    def execute(self, args: list[str]) -> None:
        if not args:
            print(f"Usage: {self.usage}")
            return

        symbol = args[0].upper()
        print(f"\n{'═' * 52}")
        print(f"  {symbol} — Perpetual Futures Analysis")
        print(f"{'═' * 52}")

        self._show_funding_rates(symbol)

        # Future sections slot in here, e.g.:
        #   self._show_open_interest(symbol)
        #   self._show_order_book(symbol)

        print()

    # ------------------------------------------------------------------
    # Section renderers
    # ------------------------------------------------------------------

    def _show_funding_rates(self, symbol: str) -> None:
        """Fetch and print the funding rate analysis block."""
        print("\n  📊 Funding Rate Analysis")
        print(f"  {'─' * 46}")

        try:
            analysis = self._funding_svc.analyse(symbol)
        except BybitAPIError as exc:
            print(f"  ⚠  Error fetching funding rates: {exc}")
            return
        except Exception as exc:
            print(f"  ⚠  Unexpected error: {exc}")
            return

        self._render_funding_analysis(analysis)

    @staticmethod
    def _render_funding_analysis(a: FundingRateAnalysis) -> None:
        """Format and print a FundingRateAnalysis to stdout."""
        print(f"  Interval          : every {a.funding_interval_hours} hours")
        print()

        print(f"  {'#':<4} {'Funding Rate':>14}")
        print(f"  {'─' * 20}")
        for i, rate in enumerate(a.rates, start=1):
            bar = _rate_bar(rate)
            print(f"  {i:<4} {_fmt_rate(rate):>14}  {bar}")

        print(f"  {'─' * 20}")
        print(f"  {'Avg':<4} {_fmt_rate(a.average_rate):>14}")
        print()
        print(f"  Annualised rate   : {a.annualized_rate:+.4%}")


# ---------------------------------------------------------------------------
# Formatting helpers (module-level so they're easily testable)
# ---------------------------------------------------------------------------

def _fmt_rate(rate: float) -> str:
    """Format a funding rate as a percentage string, e.g. '+0.0100%'."""
    return f"{rate:+.4%}"


def _rate_bar(rate: float, scale: float = 0.0010) -> str:
    """
    Return a tiny ASCII bar indicating the sign/magnitude of *rate*.

    Args:
        rate:  Raw funding rate (e.g. 0.0001).
        scale: Rate value that corresponds to one bar character.
    """
    width = min(10, int(abs(rate) / scale))
    if rate >= 0:
        return "▲" * width or "·"
    return "▼" * width or "·"
