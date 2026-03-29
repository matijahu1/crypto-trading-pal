"""
exporters/balance_exporter.py — writes wallet balance data to a CSV file.

Responsibilities:
  - Accept a WalletBalance dataclass (produced by BalanceService)
  - Write it to a CSV file with a fixed schema
  - Overwrite the file if it already exists

This module knows nothing about the API or the CLI.
It only knows the shape of the data it receives and the file it writes.
"""

from __future__ import annotations

import csv
import pathlib

from services.balance import WalletBalance

# CSV column headers — matches the field names in CoinBalance
HEADERS = ["coin", "total_balance", "available_balance"]


class BalanceExporter:
    """Exports WalletBalance data to a CSV file."""

    def __init__(self, output_path: str | pathlib.Path = "balance.csv") -> None:
        """
        Args:
            output_path: Destination file path. Overwritten on each export.
                         Defaults to balance.csv in the current working directory.
        """
        self._output_path = pathlib.Path(output_path)

    def export(self, wallet: WalletBalance) -> pathlib.Path:
        """
        Write all coins in *wallet* to a CSV file and return the file path.

        The file is always overwritten. An empty wallet produces a file with
        only the header row — no silent failures.

        Args:
            wallet: The WalletBalance dataclass returned by BalanceService.

        Returns:
            The resolved path of the written file.

        Raises:
            OSError: If the file cannot be written (permissions, bad path, etc.)
        """
        self._output_path.parent.mkdir(parents=True, exist_ok=True)

        with self._output_path.open("w", newline="", encoding="utf-8") as fh:
            writer = csv.writer(fh)
            writer.writerow(HEADERS)
            for cb in wallet.coins:
                writer.writerow([cb.coin, cb.total, cb.available])

        return self._output_path.resolve()
