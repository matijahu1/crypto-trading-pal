"""
Unit tests for FuturesPositionService.

The API client is replaced by a plain stub — no real HTTP calls,
no unittest.mock. Each test follows arrange → act → assert.
"""

import pytest

from services.futures_position import (
    FuturesPositionService,
    FuturesPosition,
    PositionSnapshot,
)
from api.bybit_client import BybitAPIError


# ---------------------------------------------------------------------------
# Stub client
# ---------------------------------------------------------------------------

class StubPositionClient:
    """
    In-memory stub satisfying PositionClientProtocol.

    Pass `positions` to control what the API returns.
    Pass `raise_error=True` to simulate a network / auth failure.
    """

    def __init__(
        self,
        positions: list[dict] | None = None,
        raise_error: bool = False,
    ) -> None:
        self._positions = positions or []
        self._raise_error = raise_error
        self.last_category: str | None = None

    def get_positions(self, category: str) -> list[dict]:
        self.last_category = category
        if self._raise_error:
            raise BybitAPIError("Bybit API error [10003]: Invalid api_key")
        return self._positions


# ---------------------------------------------------------------------------
# Realistic sample data (mirrors actual Bybit V5 position response fields)
# ---------------------------------------------------------------------------

SAMPLE_POSITIONS = [
    {
        "symbol": "BTCUSDT",
        "side": "Buy",
        "size": "0.01",
        "avgPrice": "65000.0",
        "markPrice": "65200.0",
        "unrealisedPnl": "2.0",
    },
    {
        "symbol": "ETHUSDT",
        "side": "Sell",
        "size": "0.5",
        "avgPrice": "3200.0",
        "markPrice": "3100.0",
        "unrealisedPnl": "50.0",
    },
    {
        "symbol": "SOLUSDT",
        "side": "Buy",
        "size": "0",          # zero — should be filtered out
        "avgPrice": "150.0",
        "markPrice": "0",
        "unrealisedPnl": "0",
    },
]


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def service() -> FuturesPositionService:
    """Service backed by the full SAMPLE_POSITIONS dataset."""
    client = StubPositionClient(positions=SAMPLE_POSITIONS)
    return FuturesPositionService(client=client)


# ---------------------------------------------------------------------------
# Tests — return types and structure
# ---------------------------------------------------------------------------

class TestReturnTypes:

    def test_returns_position_snapshot(self, service):
        result = service.get_positions()

        assert isinstance(result, PositionSnapshot)

    def test_positions_are_futures_position_instances(self, service):
        result = service.get_positions()

        assert all(isinstance(p, FuturesPosition) for p in result.positions)

    def test_category_preserved_in_snapshot(self):
        client = StubPositionClient(positions=SAMPLE_POSITIONS)
        svc = FuturesPositionService(client=client, category="inverse")

        result = svc.get_positions()

        assert result.category == "inverse"

    def test_category_passed_to_client(self):
        client = StubPositionClient(positions=SAMPLE_POSITIONS)
        svc = FuturesPositionService(client=client, category="inverse")

        svc.get_positions()

        assert client.last_category == "inverse"


# ---------------------------------------------------------------------------
# Tests — zero-size filtering
# ---------------------------------------------------------------------------

class TestZeroSizeFiltering:

    def test_zero_size_positions_are_excluded(self, service):
        # SOLUSDT has size "0" in SAMPLE_POSITIONS
        result = service.get_positions()

        symbols = [p.symbol for p in result.positions]
        assert "SOLUSDT" not in symbols

    def test_non_zero_positions_are_included(self, service):
        result = service.get_positions()

        symbols = [p.symbol for p in result.positions]
        assert "BTCUSDT" in symbols
        assert "ETHUSDT" in symbols

    def test_only_two_non_zero_positions_returned(self, service):
        # SAMPLE_POSITIONS has 3 entries but SOLUSDT has size 0
        result = service.get_positions()

        assert len(result.positions) == 2

    def test_empty_response_returns_empty_list(self):
        client = StubPositionClient(positions=[])
        svc = FuturesPositionService(client=client)

        result = svc.get_positions()

        assert result.positions == []

    def test_all_zero_positions_returns_empty_list(self):
        all_zero = [
            {"symbol": "BTCUSDT", "side": "Buy", "size": "0",
             "avgPrice": "0", "markPrice": "0", "unrealisedPnl": "0"},
        ]
        client = StubPositionClient(positions=all_zero)
        svc = FuturesPositionService(client=client)

        result = svc.get_positions()

        assert result.positions == []


# ---------------------------------------------------------------------------
# Tests — field mapping from API response to dataclass
# ---------------------------------------------------------------------------

class TestFieldMapping:

    def test_symbol_is_mapped(self, service):
        result = service.get_positions()

        btc = next(p for p in result.positions if p.symbol == "BTCUSDT")
        assert btc.symbol == "BTCUSDT"

    def test_side_is_mapped(self, service):
        result = service.get_positions()

        btc = next(p for p in result.positions if p.symbol == "BTCUSDT")
        assert btc.side == "Buy"

    def test_size_is_parsed_as_float(self, service):
        result = service.get_positions()

        btc = next(p for p in result.positions if p.symbol == "BTCUSDT")
        assert isinstance(btc.size, float)
        assert btc.size == pytest.approx(0.01)

    def test_entry_price_mapped_from_avg_price(self, service):
        result = service.get_positions()

        btc = next(p for p in result.positions if p.symbol == "BTCUSDT")
        assert btc.entry_price == pytest.approx(65000.0)

    def test_mark_price_is_mapped(self, service):
        result = service.get_positions()

        btc = next(p for p in result.positions if p.symbol == "BTCUSDT")
        assert btc.mark_price == pytest.approx(65200.0)

    def test_unrealized_pnl_mapped_from_unrealised_pnl(self, service):
        result = service.get_positions()

        btc = next(p for p in result.positions if p.symbol == "BTCUSDT")
        assert btc.unrealized_pnl == pytest.approx(2.0)

    def test_sell_side_position_mapped_correctly(self, service):
        result = service.get_positions()

        eth = next(p for p in result.positions if p.symbol == "ETHUSDT")
        assert eth.side == "Sell"
        assert eth.size == pytest.approx(0.5)
        assert eth.entry_price == pytest.approx(3200.0)

    def test_missing_optional_fields_default_to_zero(self):
        # markPrice and unrealisedPnl are absent — should default to 0.0
        minimal = [
            {"symbol": "BTCUSDT", "side": "Buy", "size": "0.1", "avgPrice": "60000"},
        ]
        client = StubPositionClient(positions=minimal)
        svc = FuturesPositionService(client=client)

        result = svc.get_positions()

        p = result.positions[0]
        assert p.mark_price == pytest.approx(0.0)
        assert p.unrealized_pnl == pytest.approx(0.0)

    def test_null_optional_fields_default_to_zero(self):
        # Bybit occasionally returns null for fields on flat hedged positions
        with_nulls = [
            {"symbol": "BTCUSDT", "side": "Buy", "size": "0.1",
             "avgPrice": "60000", "markPrice": None, "unrealisedPnl": None},
        ]
        client = StubPositionClient(positions=with_nulls)
        svc = FuturesPositionService(client=client)

        result = svc.get_positions()

        p = result.positions[0]
        assert p.mark_price == pytest.approx(0.0)
        assert p.unrealized_pnl == pytest.approx(0.0)


# ---------------------------------------------------------------------------
# Tests — sorting
# ---------------------------------------------------------------------------

class TestSorting:

    def test_positions_sorted_alphabetically_by_symbol(self, service):
        result = service.get_positions()

        symbols = [p.symbol for p in result.positions]
        assert symbols == sorted(symbols)

    def test_single_position_result_is_a_list(self):
        client = StubPositionClient(positions=[SAMPLE_POSITIONS[0]])
        svc = FuturesPositionService(client=client)

        result = svc.get_positions()

        assert len(result.positions) == 1


# ---------------------------------------------------------------------------
# Tests — API error handling
# ---------------------------------------------------------------------------

class TestErrorHandling:

    def test_api_error_is_propagated(self):
        client = StubPositionClient(raise_error=True)
        svc = FuturesPositionService(client=client)

        with pytest.raises(BybitAPIError):
            svc.get_positions()

    def test_api_error_message_is_preserved(self):
        client = StubPositionClient(raise_error=True)
        svc = FuturesPositionService(client=client)

        with pytest.raises(BybitAPIError, match="10003"):
            svc.get_positions()
