"""
exporters/recent_executions_exporter.py — writes recent execution data to CSV.

Responsibilities:
  - Accept a RecentExecutionHistory dataclass
  - Declare the CSV schema (including order_id)
  - Delegate filesystem operations to CsvExporter
"""

from __future__ import annotations

import pathlib
from typing import Any

from exporters.csv_exporter import CsvExporter
from services.recent_executions import RecentExecutionHistory

HEADERS = [
    "exec_id",
    "order_id",  # Added to link with Order History
    "symbol",
    "side",
    "price",
    "qty",
    "exec_type",
    "date",
    "time",
]


def make_recent_exporter(
    symbol: str, output_dir: str | pathlib.Path = "data"
) -> "RecentExecutionsExporter":
    """
    Build a RecentExecutionsExporter with the correct filename.
    Output: data/<SYMBOL>_recent_fills.csv
    """
    filename = f"{symbol.upper()}_recent_fills.csv"
    return RecentExecutionsExporter(pathlib.Path(output_dir) / filename)


class RecentExecutionsExporter(CsvExporter):
    """Exports RecentExecutionHistory to a CSV file."""

    def __init__(self, output_path: str | pathlib.Path) -> None:
        super().__init__(output_path)

    @property
    def headers(self) -> list[str]:
        return HEADERS

    def rows(self, data: RecentExecutionHistory) -> list[list[Any]]:
        return [
            [
                e.exec_id,
                e.order_id,
                e.symbol,
                e.side,
                e.price,
                e.qty,
                e.exec_type,
                e.date,
                e.time,
            ]
            for e in data.executions
        ]