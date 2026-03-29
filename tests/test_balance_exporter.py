"""
Unit tests for BalanceExporter.

Uses pytest's tmp_path fixture so no files are ever written to the real
project directory. Each test is self-contained: arrange → act → assert.
"""

import csv
import pathlib

import pytest

from exporters.balance_exporter import BalanceExporter, HEADERS
from services.balance import CoinBalance, WalletBalance


# ---------------------------------------------------------------------------
# Realistic sample data
# ---------------------------------------------------------------------------

def make_wallet(*coins: tuple[str, float, float]) -> WalletBalance:
    """
    Helper: build a WalletBalance from (coin, total, available) tuples.

    Example:
        make_wallet(("BTC", 0.25, 0.20), ("ETH", 1.50, 1.20))
    """
    return WalletBalance(
        account_type="UNIFIED",
        coins=[CoinBalance(coin=c, total=t, available=a) for c, t, a in coins],
    )


SAMPLE_WALLET = make_wallet(
    ("BTC",  0.25,  0.20),
    ("ETH",  1.50,  1.20),
    ("USDT", 500.0, 480.0),
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

class TestBalanceExporter:

    def test_file_is_created(self, tmp_path):
        # Arrange
        output = tmp_path / "data" / "balance.csv"
        exporter = BalanceExporter(output_path=output)

        # Act
        exporter.export(SAMPLE_WALLET)

        # Assert
        assert output.exists()

    def test_data_directory_is_created_automatically(self, tmp_path):
        # Arrange — the data/ sub-directory does not exist yet
        output = tmp_path / "data" / "balance.csv"
        exporter = BalanceExporter(output_path=output)

        # Act
        exporter.export(SAMPLE_WALLET)

        # Assert
        assert output.parent.is_dir()

    def test_returns_resolved_absolute_path(self, tmp_path):
        # Arrange
        output = tmp_path / "data" / "balance.csv"
        exporter = BalanceExporter(output_path=output)

        # Act
        result = exporter.export(SAMPLE_WALLET)

        # Assert
        assert result == output.resolve()
        assert result.is_absolute()

    def test_file_is_overwritten_not_appended(self, tmp_path):
        # Arrange — export once, then export again with different data
        output = tmp_path / "balance.csv"
        exporter = BalanceExporter(output_path=output)
        exporter.export(SAMPLE_WALLET)

        single_coin_wallet = make_wallet(("BTC", 0.25, 0.20))

        # Act
        exporter.export(single_coin_wallet)

        # Assert — only 1 data row, not 3 from the first run
        rows = read_csv(output)
        assert len(rows) == 2  # header + 1 coin

    def test_correct_headers_are_written(self, tmp_path):
        # Arrange
        output = tmp_path / "balance.csv"
        exporter = BalanceExporter(output_path=output)

        # Act
        exporter.export(SAMPLE_WALLET)

        # Assert
        rows = read_csv(output)
        assert rows[0] == HEADERS
        assert rows[0] == ["coin", "total_balance", "available_balance"]

    def test_correct_number_of_rows(self, tmp_path):
        # Arrange — SAMPLE_WALLET has 3 coins
        output = tmp_path / "balance.csv"
        exporter = BalanceExporter(output_path=output)

        # Act
        exporter.export(SAMPLE_WALLET)

        # Assert — 1 header row + 3 data rows
        rows = read_csv(output)
        assert len(rows) == 4

    def test_correct_values_for_each_coin(self, tmp_path):
        # Arrange
        output = tmp_path / "balance.csv"
        exporter = BalanceExporter(output_path=output)

        # Act
        exporter.export(SAMPLE_WALLET)

        # Assert — skip header row, check each data row
        rows = read_csv(output)
        assert rows[1] == ["BTC",  "0.25", "0.2"]
        assert rows[2] == ["ETH",  "1.5",  "1.2"]
        assert rows[3] == ["USDT", "500.0", "480.0"]

    def test_coin_column_values(self, tmp_path):
        # Arrange
        output = tmp_path / "balance.csv"
        exporter = BalanceExporter(output_path=output)

        # Act
        exporter.export(SAMPLE_WALLET)

        # Assert — first column of each data row
        rows = read_csv(output)
        coins = [row[0] for row in rows[1:]]
        assert coins == ["BTC", "ETH", "USDT"]

    def test_empty_wallet_writes_header_only(self, tmp_path):
        # Arrange
        output = tmp_path / "balance.csv"
        exporter = BalanceExporter(output_path=output)
        empty_wallet = make_wallet()  # no coins

        # Act
        exporter.export(empty_wallet)

        # Assert — file exists with only the header row
        assert output.exists()
        rows = read_csv(output)
        assert rows == [["coin", "total_balance", "available_balance"]]

    def test_empty_wallet_produces_no_data_rows(self, tmp_path):
        # Arrange
        output = tmp_path / "balance.csv"
        exporter = BalanceExporter(output_path=output)
        empty_wallet = make_wallet()

        # Act
        exporter.export(empty_wallet)

        # Assert
        rows = read_csv(output)
        assert len(rows) == 1  # header only, zero data rows

    def test_single_coin_wallet(self, tmp_path):
        # Arrange
        output = tmp_path / "balance.csv"
        exporter = BalanceExporter(output_path=output)
        wallet = make_wallet(("SOL", 10.5, 9.0))

        # Act
        exporter.export(wallet)

        # Assert
        rows = read_csv(output)
        assert len(rows) == 2
        assert rows[1] == ["SOL", "10.5", "9.0"]

    def test_default_output_path_is_under_data_directory(self):
        # Arrange / Act
        exporter = BalanceExporter()

        # Assert — default path must be data/balance.csv (not bare balance.csv)
        assert exporter._output_path == pathlib.Path("data") / "balance.csv"
