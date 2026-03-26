"""
Unit tests for ShowCommand and its formatting helpers.

ShowCommand is tested by injecting a stub FundingRateService so we never
touch the network. Output is captured via capsys.
"""

import pytest
from unittest.mock import MagicMock

from commands.show import ShowCommand, _fmt_rate, _rate_bar
from services.funding_rate import FundingRateAnalysis
from api.bybit_client import BybitAPIError


# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------

def _make_analysis(**overrides) -> FundingRateAnalysis:
    defaults = dict(
        symbol="ZECUSDT",
        category="linear",
        funding_interval_hours=8,
        rates=[0.0001, 0.0002, -0.0001, 0.0003, 0.0, 0.0001, 0.0002, 0.0001],
        average_rate=0.0001125,
        annualized_rate=0.12285,
    )
    defaults.update(overrides)
    return FundingRateAnalysis(**defaults)


@pytest.fixture
def stub_service() -> MagicMock:
    svc = MagicMock()
    svc.analyse.return_value = _make_analysis()
    return svc


@pytest.fixture
def cmd(stub_service) -> ShowCommand:
    return ShowCommand(funding_rate_service=stub_service)


# ---------------------------------------------------------------------------
# ShowCommand.execute()
# ---------------------------------------------------------------------------

class TestShowCommandExecute:

    def test_no_args_prints_usage(self, cmd, capsys):
        cmd.execute([])
        out = capsys.readouterr().out
        assert "Usage" in out

    def test_calls_service_with_uppercased_symbol(self, cmd, stub_service):
        cmd.execute(["zecusdt"])
        stub_service.analyse.assert_called_once_with("ZECUSDT")

    def test_output_contains_symbol(self, cmd, capsys):
        cmd.execute(["ZECUSDT"])
        out = capsys.readouterr().out
        assert "ZECUSDT" in out

    def test_output_contains_interval(self, cmd, capsys):
        cmd.execute(["ZECUSDT"])
        out = capsys.readouterr().out
        assert "8" in out

    def test_output_contains_annualised_rate(self, cmd, capsys):
        cmd.execute(["ZECUSDT"])
        out = capsys.readouterr().out
        assert "%" in out

    def test_api_error_shows_warning_not_crash(self, cmd, stub_service, capsys):
        stub_service.analyse.side_effect = BybitAPIError("symbol not found")
        cmd.execute(["FAKEUSDT"])
        out = capsys.readouterr().out
        assert "Error" in out or "⚠" in out

    def test_unexpected_error_shows_warning_not_crash(self, cmd, stub_service, capsys):
        stub_service.analyse.side_effect = RuntimeError("unexpected")
        cmd.execute(["ZECUSDT"])
        out = capsys.readouterr().out
        assert "error" in out.lower() or "⚠" in out


# ---------------------------------------------------------------------------
# Formatting helpers
# ---------------------------------------------------------------------------

class TestFmtRate:

    def test_positive_rate(self):
        assert _fmt_rate(0.0001) == "+0.0100%"

    def test_negative_rate(self):
        assert _fmt_rate(-0.0001) == "-0.0100%"

    def test_zero_rate(self):
        assert _fmt_rate(0.0) == "+0.0000%"

    def test_larger_positive(self):
        result = _fmt_rate(0.0050)
        assert result.startswith("+")
        assert "0.5000%" in result


class TestRateBar:

    def test_positive_rate_shows_up_arrow(self):
        bar = _rate_bar(0.002)
        assert "▲" in bar

    def test_negative_rate_shows_down_arrow(self):
        bar = _rate_bar(-0.002)
        assert "▼" in bar

    def test_zero_shows_dot(self):
        bar = _rate_bar(0.0)
        assert bar == "·"

    def test_max_width_capped_at_10(self):
        bar = _rate_bar(1.0)  # absurdly large rate
        assert len(bar) <= 10

    def test_width_scales_with_magnitude(self):
        small = _rate_bar(0.001)
        large = _rate_bar(0.005)
        assert len(large) >= len(small)
