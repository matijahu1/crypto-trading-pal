"""
exporters/order_history_exporter.py — writes order history data to CSV.

Responsibilities:
  - Accept an OrderHistory dataclass (produced by OrderHistoryService)
  - Declare the CSV schema (headers + row mapping)
  - Delegate all filesystem operations to CsvExporter

Column order:
  order_id, symbol, side, order_type, price, qty, order_status,
  created_ts, updated_ts,            ← raw API timestamps
  created_date, created_time,        ← derived from createdTime
  updated_date, updated_time         ← derived from updatedTime

Output path is dynamic: data/<SYMBOL>_orderHistory.csv
Use the factory function make_exporter(symbol) to build the correct instance.
"""

from __future__ import annotations

import pathlib
from typing import Any

from exporters.csv_exporter import CsvExporter
from services.order_history import OrderHistory

HEADERS = [
    "order_id",
    "symbol",
    "side",
    "order_type",
    "price",
    "qty",
    "order_status",
    "created_ts",      # raw createdTime from API
    "updated_ts",      # raw updatedTime from API
    "created_date",
    "created_time",
    "updated_date",
    "updated_time",
]


def make_exporter(symbol: str, output_dir: str | pathlib.Path = "data") -> "OrderHistoryExporter":
    """
    Build an OrderHistoryExporter with the correct filename for *symbol*.

    Args:
        symbol:     Futures symbol, e.g. "ZECUSDT".
        output_dir: Directory to write into (default: data/).

    Returns:
        OrderHistoryExporter configured for data/<SYMBOL>_orderHistory.csv
    """
    filename = f"{symbol.upper()}_orderHistory.csv"
    return OrderHistoryExporter(pathlib.Path(output_dir) / filename)


class OrderHistoryExporter(CsvExporter):
    """Exports an OrderHistory to a CSV file."""

    def __init__(self, output_path: str | pathlib.Path) -> None:
        """
        Args:
            output_path: Full destination path, e.g. data/ZECUSDT_orderHistory.csv.
                         Use make_exporter(symbol) to construct this correctly.
        """
        super().__init__(output_path)

    @property
    def headers(self) -> list[str]:
        return HEADERS

    def rows(self, data: OrderHistory) -> list[list[Any]]:
        return [
            [
                o.order_id,
                o.symbol,
                o.side,
                o.order_type,
                o.price,
                o.qty,
                o.order_status,
                o.created_ts,
                o.updated_ts,
                o.created_date,
                o.created_time,
                o.updated_date,
                o.updated_time,
            ]
            for o in data.orders
        ]
