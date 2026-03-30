"""
exporters/csv_exporter.py — shared CSV writing mechanics.

Responsibilities:
  - Own the mkdir + open + csv.writer pattern used by every exporter
  - Define the abstract interface all concrete exporters must implement

Concrete exporters (BalanceExporter, FuturesPositionExporter, …) subclass
this and supply only their headers and row-building logic.
They never touch the filesystem directly.
"""

from __future__ import annotations

import csv
import pathlib
from abc import ABC, abstractmethod
from typing import Any


class CsvExporter(ABC):
    """Abstract base for all CSV file exporters."""

    def __init__(self, output_path: str | pathlib.Path) -> None:
        """
        Args:
            output_path: Destination file. Parent directories are created
                         automatically. The file is overwritten on each export.
        """
        self._output_path = pathlib.Path(output_path)

    @property
    @abstractmethod
    def headers(self) -> list[str]:
        """Column headers for the CSV file."""

    @abstractmethod
    def rows(self, data: Any) -> list[list[Any]]:
        """
        Convert *data* (a domain object) into a list of row value lists.

        Each inner list must have the same length as ``self.headers``.
        """

    def export(self, data: Any) -> pathlib.Path:
        """
        Write headers + rows to the output file and return the resolved path.

        The parent directory is created if absent.
        The file is always overwritten — never appended.

        Args:
            data: Domain object passed to ``self.rows()``.

        Returns:
            Resolved absolute path of the written file.

        Raises:
            OSError: On permission errors or invalid paths.
        """
        self._output_path.parent.mkdir(parents=True, exist_ok=True)

        with self._output_path.open("w", newline="", encoding="utf-8") as fh:
            writer = csv.writer(fh)
            writer.writerow(self.headers)
            writer.writerows(self.rows(data))

        return self._output_path.resolve()
