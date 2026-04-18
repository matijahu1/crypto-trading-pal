"""
exporters/open_orders_exporter.py — writes active order data to CSV.

Mirrors the structure of OrderHistoryExporter:
  - Accepts an OpenOrderSnapshot produced by OpenOrderService.
  - Declares the CSV schema (HEADERS + row mapping).
  - Handles all file I/O directly (no CsvExporter base class needed —
    the logic is simple enough to inline and avoids coupling to a base
    class that isn't part of this feature's scope).

Decimal values are written as plain strings (e.g. "2.187") so that
spreadsheet tools can parse them cleanly without float noise.

Column order:
  order_id, symbol, side, order_type, price, qty,
  order_status, created_date, created_time

If no orders are present the file is still written with headers only,
consistent with the "always produce a traceable artifact" convention
used elsewhere in this project.

Example (production)::

    from exporters.path_provider       import PathProvider
    from exporters.open_orders_exporter import OpenOrdersExporter

    provider = PathProvider(base_dir=config.exported_dir, symbol=config.symbol)
    exporter = OpenOrdersExporter(provider.open_orders_path())
    path     = exporter.export(snapshot)

Example (unit test)::

    import pathlib
    exporter = OpenOrdersExporter(pathlib.Path("/tmp/test_openOrders.csv"))
"""

from __future__ import annotations

import csv
import pathlib
from typing import Any

from services.open_orders import OpenOrder, OpenOrderSnapshot

HEADERS: list[str] = [
    "order_id",
    "symbol",
    "side",
    "order_type",
    "price",
    "qty",
    "order_status",
    "created_date",
    "created_time",
]


class OpenOrdersExporter:
    """
    Exports an OpenOrderSnapshot to a CSV file at a caller-supplied path.

    The path is intentionally *not* constructed here — use PathProvider to
    build it and inject it at the call site.

    Args:
        output_path: Full destination path, e.g.
                     ``data/exported/ICPUSDT_openOrders.csv``.
                     Obtain from ``PathProvider.open_orders_path()``.
    """

    def __init__(self, output_path: str | pathlib.Path) -> None:
        self._output_path = pathlib.Path(output_path)

    def export(self, snapshot: OpenOrderSnapshot) -> pathlib.Path:
        """
        Write *snapshot* to ``output_path`` (always overwrites).

        Writes headers even when ``snapshot.orders`` is empty so the file
        is always present and schema-correct after a successful run.

        Args:
            snapshot: The OpenOrderSnapshot from OpenOrderService.

        Returns:
            The resolved output path.
        """
        self._output_path.parent.mkdir(parents=True, exist_ok=True)

        with self._output_path.open("w", newline="", encoding="utf-8") as fh:
            writer = csv.writer(fh)
            writer.writerow(HEADERS)
            for order in snapshot.orders:
                writer.writerow(self._to_row(order))

        return self._output_path

    # ------------------------------------------------------------------
    # Private
    # ------------------------------------------------------------------

    @staticmethod
    def _to_row(o: OpenOrder) -> list[Any]:
        return [
            o.order_id,
            o.symbol,
            o.side,
            o.order_type,
            str(o.price),   # Decimal → "2.187", never float noise
            str(o.qty),
            o.order_status,
            o.created_date,
            o.created_time,
        ]
