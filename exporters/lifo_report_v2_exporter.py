"""
exporters/lifo_report_v2_exporter.py — CSV writer for LifoReportV2Service output.

Extends the v1 column set with two additional columns derived from trade-level
fee data (not available in the order-history source used by v1):

  total_fees — buy fee + proportional sell fees attributed to this lot
  net_pnl    — realized_pnl − total_fees

Output path convention: ``data/<SYMBOL>_lifo_report.csv``
Use the factory function ``make_exporter(symbol)`` to build the correct instance.
"""

from __future__ import annotations

import pathlib
from typing import Any

from exporters.csv_exporter import CsvExporter
from services.lifo_report_v2 import LotRecordV2

HEADERS = [
    "entry_date",
    "exit_date",
    "total_qty",
    "matched_qty",
    "open_qty",
    "status",
    "entry_price",
    "exit_price",
    "realized_pnl",
    "total_fees",
    "net_pnl",
]


def make_exporter(
    symbol: str,
    output_dir: str | pathlib.Path = "data",
) -> "LifoReportV2Exporter":
    """
    Build a LifoReportV2Exporter with the correct filename for *symbol*.

    Args:
        symbol:     Futures symbol, e.g. "ZECUSDT".  Uppercased automatically.
        output_dir: Directory to write into (default: data/).

    Returns:
        LifoReportV2Exporter configured for
        <output_dir>/<SYMBOL>_lifo_report.csv
    """
    filename = f"{symbol.upper()}_lifo_report.csv"
    return LifoReportV2Exporter(pathlib.Path(output_dir) / filename)


class LifoReportV2Exporter(CsvExporter):
    """Exports a list of LotRecordV2 objects to a CSV file."""

    def __init__(self, output_path: str | pathlib.Path) -> None:
        """
        Args:
            output_path: Full destination path, e.g. data/ZECUSDT_lifo_report.csv.
                         Use make_exporter(symbol) to construct this correctly.
        """
        super().__init__(output_path)

    @property
    def headers(self) -> list[str]:
        return HEADERS

    def rows(self, data: list[LotRecordV2]) -> list[list[Any]]:
        return [
            [
                r.entry_date,
                r.exit_date,
                r.total_qty,
                r.matched_qty,
                r.open_qty,
                r.status,
                r.entry_price,
                r.exit_price if r.exit_price is not None else "",
                r.realized_pnl,
                r.total_fees,
                r.net_pnl,
            ]
            for r in data
        ]
