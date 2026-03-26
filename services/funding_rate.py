"""
Service layer for funding rate analysis.

Responsibilities:
  - Fetch raw data via the API client
  - Apply business logic (compute averages, annualise, parse intervals)
  - Return structured result objects — NOT formatted strings

The CLI / command layer is responsible for presentation.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, Any


# ---------------------------------------------------------------------------
# Protocol — lets us inject any compatible client (easy mocking in tests)
# ---------------------------------------------------------------------------

class FundingRateClientProtocol(Protocol):
    """Minimal interface the service needs from a Bybit-like API client."""

    def get_funding_rate_history(
        self, symbol: str, category: str, limit: int
    ) -> list[dict[str, Any]]: ...

    def get_instruments_info(
        self, symbol: str, category: str
    ) -> dict[str, Any]: ...


# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------

@dataclass
class FundingRateAnalysis:
    """Structured result of a funding rate analysis for one symbol."""

    symbol: str
    category: str
    funding_interval_hours: int       # e.g. 8
    rates: list[float]                # newest-first
    average_rate: float
    annualized_rate: float            # e.g. 0.1095 = 10.95 %


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------

class FundingRateService:
    """Fetches and analyses funding rate data for perpetual futures symbols."""

    PERIODS_PER_YEAR = 365 * 24  # total hours in a year

    def __init__(
        self,
        client: FundingRateClientProtocol,
        category: str = "linear",
    ) -> None:
        """
        Args:
            client:   API client that satisfies FundingRateClientProtocol.
            category: Bybit market category ("linear" or "inverse").
        """
        self._client = client
        self._category = category

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def analyse(self, symbol: str, lookback: int = 8) -> FundingRateAnalysis:
        """
        Return a funding rate analysis for *symbol*.

        Args:
            symbol:   Perpetual futures symbol, e.g. "ZECUSDT".
            lookback: How many historical funding rates to fetch (default 8).

        Returns:
            FundingRateAnalysis dataclass.
        """
        symbol = symbol.upper()

        interval_hours = self._fetch_interval_hours(symbol)
        rates = self._fetch_rates(symbol, lookback)

        avg = sum(rates) / len(rates)
        periods_per_year = self.PERIODS_PER_YEAR / interval_hours
        annualised = avg * periods_per_year

        return FundingRateAnalysis(
            symbol=symbol,
            category=self._category,
            funding_interval_hours=interval_hours,
            rates=rates,
            average_rate=avg,
            annualized_rate=annualised,
        )

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _fetch_interval_hours(self, symbol: str) -> int:
        """
        Derive the funding interval (in hours) from instrument metadata.

        Bybit expresses the interval in minutes via the ``fundingInterval`` field.
        Falls back to 8 hours if the field is absent or unparseable.
        """
        try:
            info = self._client.get_instruments_info(symbol, self._category)
            minutes: int = int(info.get("fundingInterval", 480))
            return max(1, minutes // 60)
        except Exception:
            return 8  # safe default — most Bybit perps use 8 h

    def _fetch_rates(self, symbol: str, limit: int) -> list[float]:
        """
        Fetch the last *limit* funding rates and return them as floats,
        newest-first.
        """
        raw = self._client.get_funding_rate_history(
            symbol, category=self._category, limit=limit
        )
        return [float(record["fundingRate"]) for record in raw]
