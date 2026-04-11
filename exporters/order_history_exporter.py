"""
exporters/order_history_exporter.py — writes order history data to CSV.

Responsibilities:
  - Accept an OrderHistory dataclass (produced by OrderHistoryService).
  - Declare the CSV schema (headers + row mapping).
  - Delegate all filesystem operations to CsvExporter.

The output path is now supplied externally via PathProvider, keeping this
class free of any path-construction logic. This makes the exporter trivially
unit-testable: simply pass any pathlib.Path you like.

Column order:
  order_id, symbol, side, order_type, price, qty, order_status,
  created_ts, updated_ts,            ← raw API timestamps
  created_date, created_time,        ← derived from createdTime
  updated_date, updated_time         ← derived from updatedTime

Example (production)::

    from exporters.path_provider   import PathProvider
    from exporters.order_history_exporter import OrderHistoryExporter

    provider = PathProvider(base_dir=config.exported_dir, symbol=config.symbol)
    exporter = OrderHistoryExporter(provider.order_history_path())
    path     = exporter.export(history)

Example (unit test — no real filesystem needed)::

    import pathlib
    from unittest.mock import patch

    fake_path = pathlib.Path("/tmp/test_orderHistory.csv")
    exporter  = OrderHistoryExporter(fake_path)
    # … assert on exporter.headers, exporter.rows(mock_history), etc.
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


class OrderHistoryExporter(CsvExporter):
    """
    Exports an OrderHistory to a CSV file at a caller-supplied path.

    The path is intentionally *not* constructed here — use PathProvider to
    build it and inject it at the call site. This separation means:

    * The exporter has a single responsibility (serialise → CSV).
    * Tests can pass any path without touching ``data/exported/``.
    * main.py can inspect the path before the API call is made.

    Args:
        output_path: Full destination path, e.g.
                     ``data/exported/CCUSDT_orderHistory.csv``.
                     Obtain this from ``PathProvider.order_history_path()``.
    """

    def __init__(self, output_path: str | pathlib.Path) -> None:
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
