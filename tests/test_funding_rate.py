"""
Unit tests for FundingRateService.

The Bybit HTTP client is replaced by a simple stub that returns controlled data,
so these tests run offline and deterministically.
"""

import pytest
from services.funding_rate import FundingRateService, FundingRateAnalysis


# ---------------------------------------------------------------------------
# Stub client
# ---------------------------------------------------------------------------

class StubBybitClient:
    """In-memory stub satisfying FundingRateClientProtocol."""

    def __init__(
        self,
        rates: list[str],
        funding_interval_minutes: int = 480,
        raise_on_info: bool = False,
    ) -> None:
        self._rates = rates
        self._interval = funding_interval_minutes
        self._raise_on_info = raise_on_info

    def get_funding_rate_history(self, symbol, category, limit):
        return [
            {"symbol": symbol, "fundingRate": r, "fundingRateTimestamp": "0"}
            for r in self._rates[:limit]
        ]

    def get_instruments_info(self, symbol, category):
        if self._raise_on_info:
            raise RuntimeError("instruments endpoint unavailable")
        return {"symbol": symbol, "fundingInterval": self._interval}


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

SAMPLE_RATES = ["0.0001", "0.0002", "-0.0001", "0.0003",
                "0.0000", "0.0001", "0.0002", "0.0001"]


@pytest.fixture
def service() -> FundingRateService:
    client = StubBybitClient(rates=SAMPLE_RATES, funding_interval_minutes=480)
    return FundingRateService(client=client)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestFundingRateAnalysis:

    def test_returns_analysis_dataclass(self, service):
        result = service.analyse("ZECUSDT")
        assert isinstance(result, FundingRateAnalysis)

    def test_symbol_is_uppercased(self, service):
        result = service.analyse("zecusdt")
        assert result.symbol == "ZECUSDT"

    def test_correct_number_of_rates(self, service):
        result = service.analyse("ZECUSDT", lookback=8)
        assert len(result.rates) == 8

    def test_rates_are_floats(self, service):
        result = service.analyse("ZECUSDT")
        assert all(isinstance(r, float) for r in result.rates)

    def test_average_rate_calculation(self, service):
        result = service.analyse("ZECUSDT")
        expected_avg = sum(float(r) for r in SAMPLE_RATES) / len(SAMPLE_RATES)
        assert result.average_rate == pytest.approx(expected_avg)

    def test_funding_interval_parsed_from_minutes(self):
        client = StubBybitClient(rates=SAMPLE_RATES, funding_interval_minutes=240)
        svc = FundingRateService(client=client)
        result = svc.analyse("BTCUSDT")
        assert result.funding_interval_hours == 4

    def test_annualised_rate_8h_interval(self, service):
        result = service.analyse("ZECUSDT")
        periods_per_year = (365 * 24) / 8
        expected = result.average_rate * periods_per_year
        assert result.annualized_rate == pytest.approx(expected)

    def test_annualised_rate_4h_interval(self):
        client = StubBybitClient(rates=SAMPLE_RATES, funding_interval_minutes=240)
        svc = FundingRateService(client=client)
        result = svc.analyse("ETHUSDT")
        periods_per_year = (365 * 24) / 4
        expected = result.average_rate * periods_per_year
        assert result.annualized_rate == pytest.approx(expected)

    def test_fallback_to_8h_if_instruments_fails(self):
        """Service should not crash when instruments endpoint is unavailable."""
        client = StubBybitClient(rates=SAMPLE_RATES, raise_on_info=True)
        svc = FundingRateService(client=client)
        result = svc.analyse("XYZUSDT")
        assert result.funding_interval_hours == 8

    def test_lookback_respected(self, service):
        result = service.analyse("ZECUSDT", lookback=4)
        assert len(result.rates) == 4

    def test_all_positive_rates(self):
        rates = ["0.0001"] * 8
        client = StubBybitClient(rates=rates)
        svc = FundingRateService(client=client)
        result = svc.analyse("BTCUSDT")
        assert result.average_rate > 0

    def test_all_negative_rates(self):
        rates = ["-0.0001"] * 8
        client = StubBybitClient(rates=rates)
        svc = FundingRateService(client=client)
        result = svc.analyse("BTCUSDT")
        assert result.average_rate < 0

    def test_mixed_rates_average_near_zero(self):
        rates = ["0.0001", "-0.0001"] * 4
        client = StubBybitClient(rates=rates)
        svc = FundingRateService(client=client)
        result = svc.analyse("BTCUSDT")
        assert result.average_rate == pytest.approx(0.0)
