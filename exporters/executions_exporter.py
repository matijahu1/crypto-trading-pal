"""
exporters/executions_exporter.py — writes execution history data to CSV.

Responsibilities:
  - Accept an ExecutionHistory dataclass (produced by ExecutionsService)
  - Declare the CSV schema (headers + row mapping)
  - Delegate all filesystem operations to CsvExporter

Output path is dynamic: data/<SYMBOL>_executions.csv
Use the factory function make_exporter(symbol) to build the correct instance.
"""

from __future__ import annotations

import pathlib
from typing import Any

from exporters.csv_exporter import CsvExporter
from services.executions import ExecutionHistory

HEADERS = [
    "exec_id",
    "symbol",
    "side",
    "exec_price",
    "exec_qty",
    "exec_fee",
    "exec_fee_rate",
    "exec_type",
    "date",
    "time",
]


def make_exporter(
    symbol: str, output_dir: str | pathlib.Path = "data"
) -> "ExecutionsExporter":
    """
    Build an ExecutionsExporter with the correct filename for *symbol*.

    Args:
        symbol:     Futures symbol, e.g. "ZECUSDT".
        output_dir: Directory to write into (default: data/).

    Returns:
        ExecutionsExporter configured for data/<SYMBOL>_executions.csv
    """
    filename = f"{symbol.upper()}_executions.csv"
    return ExecutionsExporter(pathlib.Path(output_dir) / filename)


class ExecutionsExporter(CsvExporter):
    """Exports an ExecutionHistory to a CSV file."""

    def __init__(self, output_path: str | pathlib.Path) -> None:
        """
        Args:
            output_path: Full destination path, e.g. data/ZECUSDT_executions.csv.
                         Use make_exporter(symbol) to construct this correctly.
        """
        super().__init__(output_path)

    @property
    def headers(self) -> list[str]:
        return HEADERS

    def rows(self, data: ExecutionHistory) -> list[list[Any]]:
        return [
            [
                e.exec_id,
                e.symbol,
                e.side,
                e.exec_price,
                e.exec_qty,
                e.exec_fee,
                e.exec_fee_rate,
                e.exec_type,
                e.date,
                e.time,
            ]
            for e in data.executions
        ]
