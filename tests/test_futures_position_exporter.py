"""
Unit tests for FuturesPositionExporter.

Uses pytest's tmp_path fixture so no files are written to the real data/
directory. Each test follows arrange → act → assert.
"""

import csv
import pathlib

import pytest

from exporters.futures_position_exporter import FuturesPositionExporter, HEADERS
from services.futures_position import FuturesPosition, PositionSnapshot


# ---------------------------------------------------------------------------
# Realistic sample data
# ---------------------------------------------------------------------------

def make_snapshot(*positions: tuple) -> PositionSnapshot:
    """
    Helper: build a PositionSnapshot from tuples of
    (symbol, side, size, entry_price, mark_price, unrealized_pnl).
    """
    return PositionSnapshot(
        category="linear",
        positions=[
            FuturesPosition(
                symbol=s, side=sd, size=sz,
                entry_price=ep, mark_price=mp, unrealized_pnl=pnl,
            )
            for s, sd, sz, ep, mp, pnl in positions
        ],
    )


SAMPLE_SNAPSHOT = make_snapshot(
    ("BTCUSDT", "Buy",  0.01, 65000.0, 65200.0,  2.0),
    ("ETHUSDT", "Sell", 0.5,  3200.0,  3100.0,  50.0),
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def read_csv(path: pathlib.Path) -> list[list[str]]:
    """Return all rows (including the header) as a list of string lists."""
    return list(csv.reader(path.read_text(encoding="utf-8").splitlines()))


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestFuturesPositionExporter:

    def test_file_is_created(self, tmp_path):
        # Arrange
        output = tmp_path / "data" / "futures_positions.csv"
        exporter = FuturesPositionExporter(output_path=output)

        # Act
        exporter.export(SAMPLE_SNAPSHOT)

        # Assert
        assert output.exists()

    def test_data_directory_is_created_automatically(self, tmp_path):
        # Arrange — data/ sub-directory does not yet exist
        output = tmp_path / "data" / "futures_positions.csv"
        exporter = FuturesPositionExporter(output_path=output)

        # Act
        exporter.export(SAMPLE_SNAPSHOT)

        # Assert
        assert output.parent.is_dir()

    def test_returns_resolved_absolute_path(self, tmp_path):
        # Arrange
        output = tmp_path / "futures_positions.csv"
        exporter = FuturesPositionExporter(output_path=output)

        # Act
        result = exporter.export(SAMPLE_SNAPSHOT)

        # Assert
        assert result == output.resolve()
        assert result.is_absolute()

    def test_file_is_overwritten_not_appended(self, tmp_path):
        # Arrange — write with two rows, then overwrite with one row
        output = tmp_path / "futures_positions.csv"
        exporter = FuturesPositionExporter(output_path=output)
        exporter.export(SAMPLE_SNAPSHOT)

        single = make_snapshot(("BTCUSDT", "Buy", 0.01, 65000.0, 65200.0, 2.0))

        # Act
        exporter.export(single)

        # Assert — only 1 data row, not 2 from the first export
        rows = read_csv(output)
        assert len(rows) == 2  # header + 1 position

    def test_correct_headers_are_written(self, tmp_path):
        # Arrange
        output = tmp_path / "futures_positions.csv"
        exporter = FuturesPositionExporter(output_path=output)

        # Act
        exporter.export(SAMPLE_SNAPSHOT)

        # Assert
        rows = read_csv(output)
        assert rows[0] == HEADERS
        assert rows[0] == ["symbol", "side", "size", "entry_price", "mark_price", "unrealized_pnl"]

    def test_correct_number_of_rows(self, tmp_path):
        # Arrange — SAMPLE_SNAPSHOT has 2 positions
        output = tmp_path / "futures_positions.csv"
        exporter = FuturesPositionExporter(output_path=output)

        # Act
        exporter.export(SAMPLE_SNAPSHOT)

        # Assert — 1 header + 2 data rows
        rows = read_csv(output)
        assert len(rows) == 3

    def test_correct_values_for_btc_position(self, tmp_path):
        # Arrange
        output = tmp_path / "futures_positions.csv"
        exporter = FuturesPositionExporter(output_path=output)

        # Act
        exporter.export(SAMPLE_SNAPSHOT)

        # Assert
        rows = read_csv(output)
        assert rows[1] == ["BTCUSDT", "Buy", "0.01", "65000.0", "65200.0", "2.0"]

    def test_correct_values_for_eth_position(self, tmp_path):
        # Arrange
        output = tmp_path / "futures_positions.csv"
        exporter = FuturesPositionExporter(output_path=output)

        # Act
        exporter.export(SAMPLE_SNAPSHOT)

        # Assert
        rows = read_csv(output)
        assert rows[2] == ["ETHUSDT", "Sell", "0.5", "3200.0", "3100.0", "50.0"]

    def test_empty_snapshot_writes_header_only(self, tmp_path):
        # Arrange
        output = tmp_path / "futures_positions.csv"
        exporter = FuturesPositionExporter(output_path=output)
        empty = make_snapshot()  # no positions

        # Act
        exporter.export(empty)

        # Assert — file exists with only the header
        assert output.exists()
        rows = read_csv(output)
        assert len(rows) == 1
        assert rows[0] == HEADERS

    def test_single_position_snapshot(self, tmp_path):
        # Arrange
        output = tmp_path / "futures_positions.csv"
        exporter = FuturesPositionExporter(output_path=output)
        single = make_snapshot(("SOLUSDT", "Buy", 10.0, 150.0, 155.0, 50.0))

        # Act
        exporter.export(single)

        # Assert
        rows = read_csv(output)
        assert len(rows) == 2
        assert rows[1][0] == "SOLUSDT"

    def test_default_output_path_is_under_data_directory(self):
        # Arrange / Act
        exporter = FuturesPositionExporter()

        # Assert
        assert exporter._output_path == pathlib.Path("data") / "futures_positions.csv"
