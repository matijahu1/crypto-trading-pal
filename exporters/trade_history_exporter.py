"""
exporters/trade_history_exporter.py — writes trade history data to CSV.

Responsibilities:
  - Accept a TradeHistory dataclass (produced by TradeHistoryService)
  - Declare the CSV schema (headers + row mapping)
  - Delegate all filesystem operations to CsvExporter

Output path is dynamic: data/<SYMBOL>_tradeHistory.csv
Use the factory function make_exporter(symbol) to build the correct instance.
"""

from __future__ import annotations

import pathlib
from typing import Any

from exporters.csv_exporter import CsvExporter
from services.trade_history import TradeHistory

HEADERS = [
    "trade_id",
    "symbol",
    "side",
    "price",
    "size",
    "date",
    "time",
]


def make_exporter(symbol: str, output_dir: str | pathlib.Path = "data") -> "TradeHistoryExporter":
    """
    Build a TradeHistoryExporter with the correct filename for *symbol*.

    Args:
        symbol:     Futures symbol, e.g. "ZECUSDT".
        output_dir: Directory to write into (default: data/).

    Returns:
        TradeHistoryExporter configured for data/<SYMBOL>_tradeHistory.csv
    """
    filename = f"{symbol.upper()}_tradeHistory.csv"
    return TradeHistoryExporter(pathlib.Path(output_dir) / filename)


class TradeHistoryExporter(CsvExporter):
    """Exports a TradeHistory to a CSV file."""

    def __init__(self, output_path: str | pathlib.Path) -> None:
        """
        Args:
            output_path: Full destination path, e.g. data/ZECUSDT_tradeHistory.csv.
                         Use make_exporter(symbol) to construct this correctly.
        """
        super().__init__(output_path)

    @property
    def headers(self) -> list[str]:
        return HEADERS

    def rows(self, data: TradeHistory) -> list[list[Any]]:
        return [
            [t.trade_id, t.symbol, t.side, t.price, t.size, t.date, t.time]
            for t in data.trades
        ]
