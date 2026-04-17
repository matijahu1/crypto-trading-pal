"""
exporters/lifo_report_exporter.py — writes LIFO lot records to CSV.

Mirrors the structure of OrderHistoryExporter: accepts a typed data object,
declares the column schema, and delegates file I/O to CsvExporter.

The output path is supplied externally via PathProvider (see
``PathProvider.lifo_report_path()``), keeping this class path-agnostic
and trivially unit-testable.

Column order:
  entry_date, exit_date, total_qty, matched_qty, open_qty,
  status, entry_price, exit_price, realized_pnl

Example (production)::

    from exporters.path_provider       import PathProvider
    from exporters.lifo_report_exporter import LifoReportExporter

    provider = PathProvider(base_dir=config.exported_dir, symbol=config.symbol)
    exporter = LifoReportExporter(provider.lifo_report_path())
    path     = exporter.export(lot_records)

Example (unit test)::

    import pathlib
    exporter = LifoReportExporter(pathlib.Path("/tmp/test_lifo.csv"))
"""

from __future__ import annotations

import csv
import pathlib
from typing import Any

from services.lifo_report import LotRecord

HEADERS: list[str] = [
    "entry_date",
    "exit_date",
    "total_qty",
    "matched_qty",
    "open_qty",
    "status",
    "entry_price",
    "exit_price",
    "realized_pnl",
]


class LifoReportExporter:
    """
    Writes a list of LotRecord objects to a CSV file.

    Args:
        output_path: Full destination path, e.g.
                     ``data/exported/ICPUSDT_lifo_inventory.csv``.
    """

    def __init__(self, output_path: str | pathlib.Path) -> None:
        self._output_path = pathlib.Path(output_path)

    def export(self, records: list[LotRecord]) -> pathlib.Path:
        """
        Write *records* to ``output_path`` (overwrites any existing file).

        Args:
            records: List of LotRecord objects from LifoReportService.

        Returns:
            The resolved output path.
        """
        self._output_path.parent.mkdir(parents=True, exist_ok=True)

        with self._output_path.open("w", newline="", encoding="utf-8") as fh:
            writer = csv.writer(fh)
            writer.writerow(HEADERS)
            for record in records:
                writer.writerow(self._to_row(record))

        return self._output_path

    # ------------------------------------------------------------------
    # Private
    # ------------------------------------------------------------------

    @staticmethod
    def _to_row(r: LotRecord) -> list[Any]:
        return [
            r.entry_date,
            r.exit_date,
            r.total_qty,
            r.matched_qty,
            r.open_qty,
            r.status,
            r.entry_price,
            r.exit_price if r.exit_price is not None else "",
            r.realized_pnl,
        ]
