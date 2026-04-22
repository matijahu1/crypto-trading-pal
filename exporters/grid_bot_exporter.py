"""
exporters/grid_bot_exporter.py — writes Futures Grid Bot data to CSV.

Responsibilities:
  - Accept a GridBotSnapshot dataclass (produced by GridBotService)
  - Declare the CSV schema (headers + row mapping)
  - Delegate all filesystem operations to CsvExporter

Output path is dynamic: {SYMBOL}_gridBots.csv
Use the factory function make_exporter(symbol) to build the correct instance.

Column layout:
  bot_id | symbol | status | direction | upper_price | lower_price |
  grid_num | leverage | investment | total_investment | grid_profit |
  unrealized_pnl | filled_open_qty | filled_close_qty | created_time

All Decimal fields are written as plain decimal strings (str(Decimal) never
uses scientific notation for the value ranges seen in trading data).
"""

from __future__ import annotations

import pathlib
from typing import Any

from exporters.csv_exporter import CsvExporter
from services.grid_bot import GridBotSnapshot

HEADERS = [
    "bot_id",
    "symbol",
    "status",
    "direction",
    "upper_price",
    "lower_price",
    "grid_num",
    "leverage",
    "investment",
    "total_investment",
    "grid_profit",
    "unrealized_pnl",
    "filled_open_qty",
    "filled_close_qty",
    "created_time",
]


def make_exporter(
    symbol: str,
    output_dir: str | pathlib.Path = "data",
) -> "GridBotExporter":
    """
    Build a GridBotExporter with the correct filename for *symbol*.

    Args:
        symbol:     Futures symbol, e.g. "ICPUSDT".
        output_dir: Directory to write into (default: data/).

    Returns:
        GridBotExporter configured for ``data/<SYMBOL>_gridBots.csv``.
    """
    filename = f"{symbol.upper()}_gridBots.csv"
    return GridBotExporter(pathlib.Path(output_dir) / filename)


class GridBotExporter(CsvExporter):
    """Exports a GridBotSnapshot to a CSV file."""

    def __init__(self, output_path: str | pathlib.Path) -> None:
        """
        Args:
            output_path: Full destination path, e.g.
                         ``data/exported/ICPUSDT_gridBots.csv``.
                         Use ``make_exporter(symbol)`` to construct this.
        """
        super().__init__(output_path)

    @property
    def headers(self) -> list[str]:
        return HEADERS

    def rows(self, data: GridBotSnapshot) -> list[list[Any]]:
        return [
            [
                b.bot_id,
                b.symbol,
                b.status,
                b.direction,
                b.upper_price,
                b.lower_price,
                b.grid_num,
                b.leverage,
                b.investment,
                b.total_investment,
                b.grid_profit,
                b.unrealized_pnl,
                b.filled_open_qty,
                b.filled_close_qty,
                b.created_time,
            ]
            for b in data.bots
        ]
