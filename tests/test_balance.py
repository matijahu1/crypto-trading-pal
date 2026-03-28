"""
Unit tests for BalanceService.

The API client is replaced by a plain stub class — no real HTTP calls,
no unittest.mock. Each test is self-contained: arrange → act → assert.
"""

import pytest
from services.balance import BalanceService, WalletBalance, CoinBalance
from api.bybit_client import BybitAPIError


# ---------------------------------------------------------------------------
# Stub client
# ---------------------------------------------------------------------------

class StubBalanceClient:
    """
    In-memory stub satisfying BalanceClientProtocol.

    Pass `coins` to control what the API returns.
    Pass `raise_error=True` to simulate a network / auth failure.
    """

    def __init__(
        self,
        coins: list[dict] | None = None,
        raise_error: bool = False,
    ) -> None:
        self._coins = coins or []
        self._raise_error = raise_error
        # Record which account_type was passed so we can assert on it
        self.last_account_type: str | None = None

    def get_wallet_balance(self, account_type: str) -> list[dict]:
        self.last_account_type = account_type
        if self._raise_error:
            raise BybitAPIError("Bybit API error [10003]: Invalid api_key")
        return self._coins


# ---------------------------------------------------------------------------
# Realistic sample data (mirrors actual Bybit V5 response fields)
# ---------------------------------------------------------------------------

SAMPLE_COINS = [
    {"coin": "BTC",  "walletBalance": "0.25",    "availableToWithdraw": "0.20"},
    {"coin": "ETH",  "walletBalance": "1.50",    "availableToWithdraw": "1.20"},
    {"coin": "USDT", "walletBalance": "500.0",   "availableToWithdraw": "480.0"},
    {"coin": "SOL",  "walletBalance": "0.0",     "availableToWithdraw": "0.0"},   # zero — should be filtered
    {"coin": "XRP",  "walletBalance": "0",       "availableToWithdraw": "0"},     # zero string variant
]


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def service() -> BalanceService:
    """Service backed by the full SAMPLE_COINS dataset."""
    client = StubBalanceClient(coins=SAMPLE_COINS)
    return BalanceService(client=client)


# ---------------------------------------------------------------------------
# Tests — return types and structure
# ---------------------------------------------------------------------------

class TestReturnTypes:

    def test_returns_wallet_balance_dataclass(self, service):
        result = service.get_balances()

        assert isinstance(result, WalletBalance)

    def test_coins_are_coin_balance_instances(self, service):
        result = service.get_balances()

        assert all(isinstance(c, CoinBalance) for c in result.coins)

    def test_account_type_preserved_in_result(self):
        client = StubBalanceClient(coins=SAMPLE_COINS)
        svc = BalanceService(client=client, account_type="CONTRACT")

        result = svc.get_balances()

        assert result.account_type == "CONTRACT"

    def test_account_type_passed_to_client(self):
        client = StubBalanceClient(coins=SAMPLE_COINS)
        svc = BalanceService(client=client, account_type="CONTRACT")

        svc.get_balances()

        assert client.last_account_type == "CONTRACT"


# ---------------------------------------------------------------------------
# Tests — zero-balance filtering
# ---------------------------------------------------------------------------

class TestZeroBalanceFiltering:

    def test_zero_balance_coins_are_excluded(self, service):
        # SOL and XRP have walletBalance == "0" / "0.0" in SAMPLE_COINS
        result = service.get_balances()

        coin_names = [c.coin for c in result.coins]
        assert "SOL" not in coin_names
        assert "XRP" not in coin_names

    def test_non_zero_coins_are_included(self, service):
        result = service.get_balances()

        coin_names = [c.coin for c in result.coins]
        assert "BTC" in coin_names
        assert "ETH" in coin_names
        assert "USDT" in coin_names

    def test_only_three_non_zero_coins_returned(self, service):
        # SAMPLE_COINS has 5 entries but SOL and XRP are zero
        result = service.get_balances()

        assert len(result.coins) == 3

    def test_empty_wallet_returns_empty_list(self):
        client = StubBalanceClient(coins=[])
        svc = BalanceService(client=client)

        result = svc.get_balances()

        assert result.coins == []

    def test_all_zero_balances_returns_empty_list(self):
        all_zero = [
            {"coin": "BTC", "walletBalance": "0.0", "availableToWithdraw": "0.0"},
            {"coin": "ETH", "walletBalance": "0",   "availableToWithdraw": "0"},
        ]
        client = StubBalanceClient(coins=all_zero)
        svc = BalanceService(client=client)

        result = svc.get_balances()

        assert result.coins == []


# ---------------------------------------------------------------------------
# Tests — coin values are parsed correctly
# ---------------------------------------------------------------------------

class TestValueParsing:

    def test_total_balance_parsed_as_float(self, service):
        result = service.get_balances()

        btc = next(c for c in result.coins if c.coin == "BTC")
        assert btc.total == pytest.approx(0.25)

    def test_available_balance_parsed_as_float(self, service):
        result = service.get_balances()

        btc = next(c for c in result.coins if c.coin == "BTC")
        assert btc.available == pytest.approx(0.20)

    def test_all_balance_fields_are_floats(self, service):
        result = service.get_balances()

        for cb in result.coins:
            assert isinstance(cb.total, float)
            assert isinstance(cb.available, float)

    def test_missing_available_field_defaults_to_zero(self):
        coins = [{"coin": "BTC", "walletBalance": "0.5"}]  # no availableToWithdraw
        client = StubBalanceClient(coins=coins)
        svc = BalanceService(client=client)

        result = svc.get_balances()

        assert result.coins[0].available == pytest.approx(0.0)

    def test_null_available_field_defaults_to_zero(self):
        # Bybit occasionally returns null/None for unavailable fields
        coins = [{"coin": "ETH", "walletBalance": "1.0", "availableToWithdraw": None}]
        client = StubBalanceClient(coins=coins)
        svc = BalanceService(client=client)

        result = svc.get_balances()

        assert result.coins[0].available == pytest.approx(0.0)


# ---------------------------------------------------------------------------
# Tests — result is sorted alphabetically
# ---------------------------------------------------------------------------

class TestSorting:

    def test_coins_sorted_alphabetically(self, service):
        result = service.get_balances()

        names = [c.coin for c in result.coins]
        assert names == sorted(names)

    def test_single_coin_result_is_still_a_list(self):
        client = StubBalanceClient(coins=[
            {"coin": "BTC", "walletBalance": "1.0", "availableToWithdraw": "1.0"}
        ])
        svc = BalanceService(client=client)

        result = svc.get_balances()

        assert len(result.coins) == 1
        assert result.coins[0].coin == "BTC"


# ---------------------------------------------------------------------------
# Tests — coin filter
# ---------------------------------------------------------------------------

class TestCoinFilter:

    def test_filter_returns_only_requested_coin(self, service):
        result = service.get_balances(coin_filter="BTC")

        assert len(result.coins) == 1
        assert result.coins[0].coin == "BTC"

    def test_filter_is_case_insensitive(self, service):
        result_lower = service.get_balances(coin_filter="btc")
        result_upper = service.get_balances(coin_filter="BTC")
        result_mixed = service.get_balances(coin_filter="Btc")

        assert len(result_lower.coins) == 1
        assert len(result_upper.coins) == 1
        assert len(result_mixed.coins) == 1

    def test_filter_for_missing_coin_returns_empty_list(self, service):
        result = service.get_balances(coin_filter="DOGE")

        assert result.coins == []

    def test_filter_for_zero_balance_coin_returns_empty_list(self, service):
        # SOL is in SAMPLE_COINS but has a zero balance — should still be excluded
        result = service.get_balances(coin_filter="SOL")

        assert result.coins == []

    def test_no_filter_returns_all_non_zero_coins(self, service):
        result = service.get_balances(coin_filter=None)

        assert len(result.coins) == 3

    def test_filter_correct_values_for_eth(self, service):
        result = service.get_balances(coin_filter="ETH")

        eth = result.coins[0]
        assert eth.coin == "ETH"
        assert eth.total == pytest.approx(1.50)
        assert eth.available == pytest.approx(1.20)


# ---------------------------------------------------------------------------
# Tests — API error handling
# ---------------------------------------------------------------------------

class TestErrorHandling:

    def test_api_error_is_propagated(self):
        # The service intentionally does NOT swallow errors — the command layer
        # is responsible for user-facing error messages.
        client = StubBalanceClient(raise_error=True)
        svc = BalanceService(client=client)

        with pytest.raises(BybitAPIError):
            svc.get_balances()

    def test_api_error_message_is_preserved(self):
        client = StubBalanceClient(raise_error=True)
        svc = BalanceService(client=client)

        with pytest.raises(BybitAPIError, match="10003"):
            svc.get_balances()
