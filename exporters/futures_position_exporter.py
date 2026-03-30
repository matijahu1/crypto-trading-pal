"""
exporters/futures_position_exporter.py — writes futures position data to CSV.

Responsibilities:
  - Accept a PositionSnapshot dataclass (produced by FuturesPositionService)
  - Declare the CSV schema (headers + row mapping)
  - Delegate all filesystem operations to CsvExporter

Default output: data/futures_positions.csv  (relative to the working directory)
"""

from __future__ import annotations

import pathlib
from typing import Any

from exporters.csv_exporter import CsvExporter
from services.futures_position import PositionSnapshot

HEADERS = [
    "symbol",
    "side",
    "size",
    "entry_price",
    "mark_price",
    "unrealized_pnl",
]

DEFAULT_OUTPUT_PATH = pathlib.Path("data") / "futures_positions.csv"


class FuturesPositionExporter(CsvExporter):
    """Exports a PositionSnapshot to a CSV file inside the data/ directory."""

    def __init__(self, output_path: str | pathlib.Path = DEFAULT_OUTPUT_PATH) -> None:
        """
        Args:
            output_path: Destination file path. Overwritten on each export.
                         Parent directories are created automatically.
                         Defaults to data/futures_positions.csv.
        """
        super().__init__(output_path)

    @property
    def headers(self) -> list[str]:
        return HEADERS

    def rows(self, data: PositionSnapshot) -> list[list[Any]]:
        return [
            [
                p.symbol,
                p.side,
                p.size,
                p.entry_price,
                p.mark_price,
                p.unrealized_pnl,
            ]
            for p in data.positions
        ]
