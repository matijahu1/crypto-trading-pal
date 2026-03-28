"""
Service layer for wallet balance retrieval.

Responsibilities:
  - Fetch raw balance data via the API client
  - Filter out zero balances
  - Return structured result objects — NOT formatted strings

Follows the same pattern as FundingRateService:
  - Depends on a Protocol, not the concrete BybitClient
  - Returns dataclasses, never strings
  - Business logic lives here; presentation lives in the command layer
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol


# ---------------------------------------------------------------------------
# Protocol — decouples the service from the concrete client (mockable in tests)
# ---------------------------------------------------------------------------

class BalanceClientProtocol(Protocol):
    """Minimal interface the service needs from an exchange API client."""

    def get_wallet_balance(self, account_type: str) -> list[dict[str, Any]]: ...


# ---------------------------------------------------------------------------
# Result dataclasses
# ---------------------------------------------------------------------------

@dataclass
class CoinBalance:
    """Balance information for a single coin."""

    coin: str
    total: float
    available: float


@dataclass
class WalletBalance:
    """All non-zero coin balances for a wallet."""

    account_type: str
    coins: list[CoinBalance]  # only non-zero balances, sorted by coin name


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------

class BalanceService:
    """Fetches and filters wallet balance data."""

    def __init__(
        self,
        client: BalanceClientProtocol,
        account_type: str = "UNIFIED",
    ) -> None:
        """
        Args:
            client:       API client satisfying BalanceClientProtocol.
            account_type: Bybit account type — "UNIFIED" covers the unified
                          trading account (spot + derivatives). Use "CONTRACT"
                          for a classic derivatives-only account.
        """
        self._client = client
        self._account_type = account_type

    def get_balances(self, coin_filter: str | None = None) -> WalletBalance:
        """
        Return non-zero wallet balances, optionally filtered to one coin.

        Args:
            coin_filter: If provided (e.g. "BTC"), return only that coin.
                         Case-insensitive. If the coin is not found or has a
                         zero balance, the result will have an empty coins list.

        Returns:
            WalletBalance dataclass with a sorted list of CoinBalance entries.

        Raises:
            BybitAPIError: Propagated from the client on network / API errors.
        """
        raw_coins = self._client.get_wallet_balance(self._account_type)

        coins: list[CoinBalance] = []
        for entry in raw_coins:
            coin = entry.get("coin", "")
            total = float(entry.get("walletBalance", 0) or 0)
            available = float(entry.get("availableToWithdraw", 0) or 0)

            if total == 0:
                continue  # skip zero balances

            if coin_filter and coin.upper() != coin_filter.upper():
                continue  # skip coins that don't match the filter

            coins.append(CoinBalance(coin=coin, total=total, available=available))

        coins.sort(key=lambda c: c.coin)

        return WalletBalance(account_type=self._account_type, coins=coins)
