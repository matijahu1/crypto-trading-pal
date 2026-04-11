"""
Unit tests for RecentExecutionService and RecentExecutionExporter.
"""

import csv
import pathlib
import pytest
from typing import Any

from services.recent_executions import RecentExecutionClientProtocol, RecentExecutionService, RecentExecutionHistory, RecentExecution
from exporters.recent_executions_exporter import (
    RecentExecutionsExporter,
    make_recent_exporter,
    HEADERS,
)
from api.bybit_client import BybitAPIError

# ---------------------------------------------------------------------------
# Stub client
# ---------------------------------------------------------------------------

class StubRecentExecutionClient(RecentExecutionClientProtocol):
    def __init__(self, executions: list[dict] | None = None, raise_error: bool = False) -> None:
        self._executions = executions or []
        self._raise_error = raise_error
        
        # Tracking-Attribute für Assertions in den Tests
        self.last_symbol: str | None = None
        self.last_category: str | None = None
        self.last_limit: int | None = None
        self.last_exec_type: str | None = None

    def get_executions(
        self, 
        symbol: str | None = None, 
        category: str = "linear", 
        limit: int = 100,
        exec_type: str | None = None
    ) -> list[dict[str, Any]]:
        """
        Implementierung folgt exakt dem RecentExecutionClientProtocol.
        """
        # Speichere die übergebenen Argumente für Tests
        self.last_symbol = symbol
        self.last_category = category
        self.last_limit = limit
        self.last_exec_type = exec_type

        if self._raise_error:
            raise BybitAPIError("Bybit API error")
            
        # Simuliere die Rückgabe (begrenzt durch das Limit)
        return self._executions[:limit]

# ---------------------------------------------------------------------------
# Sample Data
# ---------------------------------------------------------------------------

SAMPLE_RAW = [
    {
        "execId": "exec-101",
        "orderId": "order-abc",
        "symbol": "BTCUSDT",
        "side": "Buy",
        "execPrice": "50000.0",
        "execQty": "0.1",
        "execType": "Trade",
        "execTime": "1700000000000",
    }
]

# ===========================================================================
# RecentExecutionService tests
# ===========================================================================

class TestRecentExecutionService:

    def test_default_limit_is_ten(self):
        # Arrange
        client = StubRecentExecutionClient(executions=SAMPLE_RAW)
        service = RecentExecutionService(client=client) # default_limit=10 in __init__

        # Act
        service.get_recent_fills("BTCUSDT")

        # Assert
        assert client.last_limit == 10

    def test_explicit_limit_parameter_overrides_default(self):
        # Arrange
        client = StubRecentExecutionClient(executions=SAMPLE_RAW)
        service = RecentExecutionService(client=client)

        # Act
        service.get_recent_fills("BTCUSDT", limit=5)

        # Assert
        assert client.last_limit == 5

    def test_order_id_is_mapped_correctly(self):
        # Arrange
        client = StubRecentExecutionClient(executions=SAMPLE_RAW)
        service = RecentExecutionService(client=client)

        # Act
        result = service.get_recent_fills("BTCUSDT")

        # Assert
        assert result.executions[0].order_id == "order-abc"

    def test_returns_correct_history_structure(self):
        # Arrange
        client = StubRecentExecutionClient(executions=SAMPLE_RAW)
        service = RecentExecutionService(client=client)

        # Act
        result = service.get_recent_fills("BTCUSDT")

        # Assert
        assert isinstance(result, RecentExecutionHistory)
        assert result.count == 1
        assert result.symbol == "BTCUSDT"

# ===========================================================================
# RecentExecutionExporter tests
# ===========================================================================

class TestRecentExecutionExporter:

    def test_exporter_writes_order_id_column(self, tmp_path):
        # Arrange
        output = tmp_path / "test_recent.csv"
        exporter = RecentExecutionsExporter(output)
        history = RecentExecutionHistory(
            symbol="BTCUSDT",
            count=1,
            executions=[
                RecentExecution(
                    exec_id="exec-1", order_id="order-1", symbol="BTCUSDT",
                    side="Buy", price=50000.0, qty=0.1, exec_type="Trade",
                    date="2023-11-14", time="22:13:20"
                )
            ]
        )

        # Act
        exporter.export(history)

        # Assert
        content = output.read_text()
        rows = list(csv.reader(content.splitlines()))
        assert rows[0] == HEADERS
        assert "order_id" in rows[0]
        assert rows[1][1] == "order-1"  # Check second column value

    def test_make_recent_exporter_uses_correct_suffix(self):
        # Arrange / Act
        exporter = make_recent_exporter("BTCUSDT")

        # Assert
        assert exporter._output_path.name == "BTCUSDT_recent_fills.csv"