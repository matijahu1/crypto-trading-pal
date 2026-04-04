"""
Unit tests for ExecutionsService and ExecutionsExporter.

The API client is replaced by a plain stub — no real HTTP calls,
no unittest.mock. Each test follows arrange → act → assert.
"""

import csv
import pathlib

import pytest

from services.executions import ExecutionsService, ExecutionHistory, Execution
from exporters.executions_exporter import (
    ExecutionsExporter,
    make_exporter,
    HEADERS,
)
from api.bybit_client import BybitAPIError


# ---------------------------------------------------------------------------
# Stub client
# ---------------------------------------------------------------------------

class StubExecutionsClient:
    """
    In-memory stub satisfying ExecutionsClientProtocol.

    Pass `executions` to control what the API returns.
    Pass `raise_error=True` to simulate a network / auth failure.
    """

    def __init__(
        self,
        executions: list[dict] | None = None,
        raise_error: bool = False,
    ) -> None:
        self._executions = executions or []
        self._raise_error = raise_error
        self.last_symbol: str | None = None
        self.last_category: str | None = None
        self.last_limit: int | None = None

    def get_executions(self, symbol: str, category: str, limit: int) -> list[dict]:
        self.last_symbol = symbol
        self.last_category = category
        self.last_limit = limit
        if self._raise_error:
            raise BybitAPIError("Bybit API error [10003]: Invalid api_key")
        return self._executions


# ---------------------------------------------------------------------------
# Realistic sample data (mirrors actual Bybit V5 execution response fields)
# Timestamps:
#   1700000000000 -> 2023-11-14  22:13:20
#   1700000060000 -> 2023-11-14  22:14:20
#   1700003600000 -> 2023-11-14  23:13:20
# ---------------------------------------------------------------------------

SAMPLE_EXECUTIONS = [
    {
        "execId":    "exec-001",
        "symbol":    "ZECUSDT",
        "side":      "Buy",
        "execPrice": "30.50",
        "execQty":   "10",
        "execFee":   "0.1830",
        "feeRate":   "0.0006",
        "execType":  "Trade",
        "execTime":  "1700000000000",
    },
    {
        "execId":    "exec-002",
        "symbol":    "ZECUSDT",
        "side":      "Sell",
        "execPrice": "31.00",
        "execQty":   "5",
        "execFee":   "0.0930",
        "feeRate":   "0.0006",
        "execType":  "Trade",
        "execTime":  "1700000060000",
    },
    {
        "execId":    "exec-003",
        "symbol":    "ZECUSDT",
        "side":      "Buy",
        "execPrice": "0",
        "execQty":   "0",
        "execFee":   "0.0050",
        "feeRate":   "0.0001",
        "execType":  "Funding",
        "execTime":  "1700003600000",
    },
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def read_csv(path: pathlib.Path) -> list[list[str]]:
    """Return all rows (including the header) as a list of string lists."""
    return list(csv.reader(path.read_text(encoding="utf-8").splitlines()))


def _make_history(*execs: tuple) -> ExecutionHistory:
    """
    Build an ExecutionHistory from
    (exec_id, symbol, side, exec_price, exec_qty, exec_fee,
     exec_fee_rate, exec_type, date, time) tuples.
    """
    return ExecutionHistory(
        symbol="ZECUSDT",
        category="linear",
        executions=[
            Execution(
                exec_id=eid, symbol=sym, side=sd,
                exec_price=ep, exec_qty=eq,
                exec_fee=ef, exec_fee_rate=efr,
                exec_type=et, date=d, time=t,
            )
            for eid, sym, sd, ep, eq, ef, efr, et, d, t in execs
        ],
    )


SAMPLE_HISTORY = _make_history(
    ("exec-001", "ZECUSDT", "Buy",  30.50, 10.0, 0.1830, 0.0006, "Trade",   "2023-11-14", "22:13:20"),
    ("exec-002", "ZECUSDT", "Sell", 31.00,  5.0, 0.0930, 0.0006, "Trade",   "2023-11-14", "22:14:20"),
    ("exec-003", "ZECUSDT", "Buy",   0.0,   0.0, 0.0050, 0.0001, "Funding", "2023-11-14", "23:13:20"),
)


# ===========================================================================
# ExecutionsService tests
# ===========================================================================

class TestExecutionsService:

    # -----------------------------------------------------------------------
    # Return types and structure
    # -----------------------------------------------------------------------

    def test_returns_execution_history_dataclass(self):
        # Arrange
        client = StubExecutionsClient(executions=SAMPLE_EXECUTIONS)
        service = ExecutionsService(client=client)

        # Act
        result = service.get_executions("ZECUSDT")

        # Assert
        assert isinstance(result, ExecutionHistory)

    def test_executions_are_execution_instances(self):
        # Arrange
        client = StubExecutionsClient(executions=SAMPLE_EXECUTIONS)
        service = ExecutionsService(client=client)

        # Act
        result = service.get_executions("ZECUSDT")

        # Assert
        assert all(isinstance(e, Execution) for e in result.executions)

    def test_symbol_is_uppercased(self):
        # Arrange
        client = StubExecutionsClient(executions=SAMPLE_EXECUTIONS)
        service = ExecutionsService(client=client)

        # Act
        result = service.get_executions("zecusdt")

        # Assert
        assert result.symbol == "ZECUSDT"

    def test_symbol_uppercased_before_passing_to_client(self):
        # Arrange
        client = StubExecutionsClient(executions=SAMPLE_EXECUTIONS)
        service = ExecutionsService(client=client)

        # Act
        service.get_executions("zecusdt")

        # Assert
        assert client.last_symbol == "ZECUSDT"

    def test_category_passed_to_client(self):
        # Arrange
        client = StubExecutionsClient(executions=SAMPLE_EXECUTIONS)
        service = ExecutionsService(client=client, category="inverse")

        # Act
        service.get_executions("ZECUSDT")

        # Assert
        assert client.last_category == "inverse"

    def test_category_preserved_in_result(self):
        # Arrange
        client = StubExecutionsClient(executions=SAMPLE_EXECUTIONS)
        service = ExecutionsService(client=client, category="inverse")

        # Act
        result = service.get_executions("ZECUSDT")

        # Assert
        assert result.category == "inverse"

    def test_limit_passed_to_client(self):
        # Arrange
        client = StubExecutionsClient(executions=SAMPLE_EXECUTIONS)
        service = ExecutionsService(client=client, limit=50)

        # Act
        service.get_executions("ZECUSDT")

        # Assert
        assert client.last_limit == 50

    def test_correct_number_of_executions_returned(self):
        # Arrange — SAMPLE_EXECUTIONS has 3 entries
        client = StubExecutionsClient(executions=SAMPLE_EXECUTIONS)
        service = ExecutionsService(client=client)

        # Act
        result = service.get_executions("ZECUSDT")

        # Assert
        assert len(result.executions) == 3

    # -----------------------------------------------------------------------
    # Field mapping
    # -----------------------------------------------------------------------

    def test_exec_id_mapped(self):
        # Arrange
        client = StubExecutionsClient(executions=SAMPLE_EXECUTIONS)
        service = ExecutionsService(client=client)

        # Act
        result = service.get_executions("ZECUSDT")

        # Assert
        assert result.executions[0].exec_id == "exec-001"

    def test_side_mapped(self):
        # Arrange
        client = StubExecutionsClient(executions=SAMPLE_EXECUTIONS)
        service = ExecutionsService(client=client)

        # Act
        result = service.get_executions("ZECUSDT")

        # Assert
        assert result.executions[0].side == "Buy"
        assert result.executions[1].side == "Sell"

    def test_exec_price_mapped_as_float(self):
        # Arrange
        client = StubExecutionsClient(executions=SAMPLE_EXECUTIONS)
        service = ExecutionsService(client=client)

        # Act
        result = service.get_executions("ZECUSDT")

        # Assert
        assert isinstance(result.executions[0].exec_price, float)
        assert result.executions[0].exec_price == pytest.approx(30.50)

    def test_exec_qty_mapped_as_float(self):
        # Arrange
        client = StubExecutionsClient(executions=SAMPLE_EXECUTIONS)
        service = ExecutionsService(client=client)

        # Act
        result = service.get_executions("ZECUSDT")

        # Assert
        assert isinstance(result.executions[0].exec_qty, float)
        assert result.executions[0].exec_qty == pytest.approx(10.0)

    def test_exec_fee_mapped_as_float(self):
        # Arrange
        client = StubExecutionsClient(executions=SAMPLE_EXECUTIONS)
        service = ExecutionsService(client=client)

        # Act
        result = service.get_executions("ZECUSDT")

        # Assert
        assert isinstance(result.executions[0].exec_fee, float)
        assert result.executions[0].exec_fee == pytest.approx(0.1830)

    def test_exec_fee_rate_mapped_from_fee_rate(self):
        # Arrange
        client = StubExecutionsClient(executions=SAMPLE_EXECUTIONS)
        service = ExecutionsService(client=client)

        # Act
        result = service.get_executions("ZECUSDT")

        # Assert
        assert result.executions[0].exec_fee_rate == pytest.approx(0.0006)

    def test_exec_type_mapped(self):
        # Arrange
        client = StubExecutionsClient(executions=SAMPLE_EXECUTIONS)
        service = ExecutionsService(client=client)

        # Act
        result = service.get_executions("ZECUSDT")

        # Assert
        assert result.executions[0].exec_type == "Trade"
        assert result.executions[2].exec_type == "Funding"

    def test_date_converted_from_exec_time(self):
        # Arrange
        client = StubExecutionsClient(executions=SAMPLE_EXECUTIONS)
        service = ExecutionsService(client=client)

        # Act
        result = service.get_executions("ZECUSDT")

        # Assert
        assert result.executions[0].date == "2023-11-14"

    def test_time_converted_from_exec_time(self):
        # Arrange
        client = StubExecutionsClient(executions=SAMPLE_EXECUTIONS)
        service = ExecutionsService(client=client)

        # Act
        result = service.get_executions("ZECUSDT")

        # Assert
        assert result.executions[0].time == "22:13:20"

    def test_funding_execution_price_is_zero(self):
        # Arrange — exec-003 is a Funding execution with execPrice "0"
        client = StubExecutionsClient(executions=SAMPLE_EXECUTIONS)
        service = ExecutionsService(client=client)

        # Act
        result = service.get_executions("ZECUSDT")

        # Assert
        assert result.executions[2].exec_price == pytest.approx(0.0)
        assert result.executions[2].exec_qty   == pytest.approx(0.0)

    def test_original_order_preserved(self):
        # Arrange — API returns newest-first; service must not reorder
        client = StubExecutionsClient(executions=SAMPLE_EXECUTIONS)
        service = ExecutionsService(client=client)

        # Act
        result = service.get_executions("ZECUSDT")

        # Assert
        ids = [e.exec_id for e in result.executions]
        assert ids == ["exec-001", "exec-002", "exec-003"]

    def test_missing_optional_fields_default_gracefully(self):
        # Arrange — only side and execPrice provided
        minimal = [{"side": "Buy", "execPrice": "30.0", "execQty": "1"}]
        client = StubExecutionsClient(executions=minimal)
        service = ExecutionsService(client=client)

        # Act
        result = service.get_executions("ZECUSDT")

        # Assert — no exception; safe defaults for missing fields
        e = result.executions[0]
        assert e.exec_id == ""
        assert e.exec_fee == pytest.approx(0.0)
        assert e.exec_fee_rate == pytest.approx(0.0)
        assert e.exec_type == ""
        assert e.date == ""
        assert e.time == ""

    # -----------------------------------------------------------------------
    # Empty response
    # -----------------------------------------------------------------------

    def test_empty_response_returns_empty_executions_list(self):
        # Arrange
        client = StubExecutionsClient(executions=[])
        service = ExecutionsService(client=client)

        # Act
        result = service.get_executions("ZECUSDT")

        # Assert
        assert result.executions == []

    def test_empty_response_still_returns_execution_history(self):
        # Arrange
        client = StubExecutionsClient(executions=[])
        service = ExecutionsService(client=client)

        # Act
        result = service.get_executions("ZECUSDT")

        # Assert
        assert isinstance(result, ExecutionHistory)
        assert result.symbol == "ZECUSDT"

    # -----------------------------------------------------------------------
    # Error handling
    # -----------------------------------------------------------------------

    def test_api_error_is_propagated(self):
        # Arrange
        client = StubExecutionsClient(raise_error=True)
        service = ExecutionsService(client=client)

        # Act / Assert
        with pytest.raises(BybitAPIError):
            service.get_executions("ZECUSDT")

    def test_api_error_message_is_preserved(self):
        # Arrange
        client = StubExecutionsClient(raise_error=True)
        service = ExecutionsService(client=client)

        # Act / Assert
        with pytest.raises(BybitAPIError, match="10003"):
            service.get_executions("ZECUSDT")


# ===========================================================================
# ExecutionsExporter tests
# ===========================================================================

class TestExecutionsExporter:

    def test_file_is_created(self, tmp_path):
        # Arrange
        exporter = ExecutionsExporter(tmp_path / "ZECUSDT_executions.csv")

        # Act
        exporter.export(SAMPLE_HISTORY)

        # Assert
        assert (tmp_path / "ZECUSDT_executions.csv").exists()

    def test_data_directory_created_automatically(self, tmp_path):
        # Arrange — data/ sub-dir does not yet exist
        output = tmp_path / "data" / "ZECUSDT_executions.csv"
        exporter = ExecutionsExporter(output)

        # Act
        exporter.export(SAMPLE_HISTORY)

        # Assert
        assert output.parent.is_dir()

    def test_file_is_overwritten_not_appended(self, tmp_path):
        # Arrange — export 3 executions, then re-export 1
        output = tmp_path / "out.csv"
        exporter = ExecutionsExporter(output)
        exporter.export(SAMPLE_HISTORY)
        single = _make_history(
            ("exec-001", "ZECUSDT", "Buy", 30.5, 10.0, 0.183, 0.0006, "Trade",
             "2023-11-14", "22:13:20")
        )

        # Act
        exporter.export(single)

        # Assert — 1 data row, not 3
        rows = read_csv(output)
        assert len(rows) == 2  # header + 1 execution

    def test_correct_headers_are_written(self, tmp_path):
        # Arrange
        output = tmp_path / "out.csv"
        exporter = ExecutionsExporter(output)

        # Act
        exporter.export(SAMPLE_HISTORY)

        # Assert
        rows = read_csv(output)
        assert rows[0] == HEADERS
        assert rows[0] == [
            "exec_id", "symbol", "side",
            "exec_price", "exec_qty", "exec_fee", "exec_fee_rate",
            "exec_type", "date", "time",
        ]

    def test_correct_number_of_rows(self, tmp_path):
        # Arrange — SAMPLE_HISTORY has 3 executions
        output = tmp_path / "out.csv"
        exporter = ExecutionsExporter(output)

        # Act
        exporter.export(SAMPLE_HISTORY)

        # Assert — 1 header + 3 data rows
        rows = read_csv(output)
        assert len(rows) == 4

    def test_correct_values_trade_row(self, tmp_path):
        # Arrange
        output = tmp_path / "out.csv"
        exporter = ExecutionsExporter(output)

        # Act
        exporter.export(SAMPLE_HISTORY)

        # Assert
        rows = read_csv(output)
        assert rows[1] == [
            "exec-001", "ZECUSDT", "Buy",
            "30.5", "10.0", "0.183", "0.0006",
            "Trade", "2023-11-14", "22:13:20",
        ]

    def test_correct_values_funding_row(self, tmp_path):
        # Arrange
        output = tmp_path / "out.csv"
        exporter = ExecutionsExporter(output)

        # Act
        exporter.export(SAMPLE_HISTORY)

        # Assert — Funding execution has zero price and qty
        rows = read_csv(output)
        assert rows[3][7] == "Funding"
        assert rows[3][3] == "0.0"   # exec_price
        assert rows[3][4] == "0.0"   # exec_qty

    def test_correct_column_count(self, tmp_path):
        # Arrange — 10 columns expected
        output = tmp_path / "out.csv"
        exporter = ExecutionsExporter(output)

        # Act
        exporter.export(SAMPLE_HISTORY)

        # Assert
        rows = read_csv(output)
        assert len(rows[0]) == 10
        assert len(rows[1]) == 10

    def test_empty_history_writes_header_only(self, tmp_path):
        # Arrange
        output = tmp_path / "out.csv"
        exporter = ExecutionsExporter(output)
        empty = _make_history()

        # Act
        exporter.export(empty)

        # Assert
        rows = read_csv(output)
        assert len(rows) == 1
        assert rows[0] == HEADERS

    def test_make_exporter_builds_correct_filename(self, tmp_path):
        # Arrange / Act
        exporter = make_exporter("ZECUSDT", output_dir=tmp_path)

        # Assert
        assert exporter._output_path == tmp_path / "ZECUSDT_executions.csv"

    def test_make_exporter_uppercases_symbol(self, tmp_path):
        # Arrange / Act
        exporter = make_exporter("zecusdt", output_dir=tmp_path)

        # Assert
        assert exporter._output_path.name == "ZECUSDT_executions.csv"

    def test_make_exporter_default_dir_is_data(self):
        # Arrange / Act
        exporter = make_exporter("ZECUSDT")

        # Assert
        assert exporter._output_path == pathlib.Path("data") / "ZECUSDT_executions.csv"
