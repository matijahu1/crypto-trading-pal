"""
exporters/balance_exporter.py — writes wallet balance data to a CSV file.

Responsibilities:
  - Accept a WalletBalance dataclass (produced by BalanceService)
  - Declare the CSV schema (headers + row mapping)
  - Delegate all filesystem operations to CsvExporter

Default output: data/balance.csv  (relative to the working directory)
"""

from __future__ import annotations

import pathlib
from typing import Any

from exporters.csv_exporter import CsvExporter
from services.balance import WalletBalance

# CSV column headers — matches the field names in CoinBalance
HEADERS = ["coin", "total_balance", "available_balance"]

# Default output location: <project_root>/data/balance.csv
DEFAULT_OUTPUT_PATH = pathlib.Path("data") / "balance.csv"


class BalanceExporter(CsvExporter):
    """Exports WalletBalance data to a CSV file inside the data/ directory."""

    def __init__(self, output_path: str | pathlib.Path = DEFAULT_OUTPUT_PATH) -> None:
        """
        Args:
            output_path: Destination file path. Overwritten on each export.
                         Parent directories are created automatically.
                         Defaults to data/balance.csv relative to the working directory.
        """
        super().__init__(output_path)

    @property
    def headers(self) -> list[str]:
        return HEADERS

    def rows(self, data: WalletBalance) -> list[list[Any]]:
        return [[cb.coin, cb.total, cb.available] for cb in data.coins]
