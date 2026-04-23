"""
exporters/futures_grid_bot_exporter.py — writes Futures Grid Bot detail data to CSV.

Responsibilities:
  - Accept a FuturesGridBotSnapshot dataclass (produced by FuturesGridBotService)
  - Declare the CSV schema (headers + row mapping)
  - Delegate all filesystem operations to CsvExporter

Output filename: {SYMBOL}_FuturesGridBots.csv
Use the factory function ``make_exporter(symbol, output_dir)`` to build the
correct instance without having to construct the path manually.

Column reference
----------------
bot_id              Bybit-assigned bot identifier
symbol              Futures symbol, e.g. CCUSDT
bot_status          Current bot state (Running / Stopped / …)
upper_price         Upper bound of the grid range
lower_price         Lower bound of the grid range
grid_num            Number of grid lines
leverage            Leverage applied
direction           Long / Short / Neutral
investment          Initial capital invested (USDT)
total_investment    Total capital including any top-ups (USDT)
grid_profit         Realised grid profit to date (USDT)
unrealized_pnl      Unrealised PnL on open position (USDT)
filled_open_qty     Cumulative buy-side fill volume
filled_close_qty    Cumulative sell-side fill volume
created_date        UTC date the bot was created  (e.g. 2024-03-15)
created_time        UTC time the bot was created  (e.g. 10:22:05)
stopped_date        UTC date the bot was stopped  (empty if still running)
stopped_time        UTC time the bot was stopped  (empty if still running)

Change log:
  - Initial implementation.
"""

from __future__ import annotations

import pathlib
from typing import Any

from exporters.csv_exporter import CsvExporter
from services.futures_grid_bot import FuturesGridBotSnapshot

HEADERS = [
    "bot_id",
    "symbol",
    "bot_status",
    "upper_price",
    "lower_price",
    "grid_num",
    "leverage",
    "direction",
    "investment",
    "total_investment",
    "grid_profit",
    "unrealized_pnl",
    "filled_open_qty",
    "filled_close_qty",
    "created_date",
    "created_time",
    "stopped_date",
    "stopped_time",
]


def make_exporter(
    symbol: str,
    output_dir: str | pathlib.Path = "data/exported",
) -> "FuturesGridBotExporter":
    """
    Build a FuturesGridBotExporter with the correct filename for *symbol*.

    Args:
        symbol:     Futures symbol, e.g. ``"CCUSDT"``.
        output_dir: Directory to write into (default: ``data/exported``).

    Returns:
        FuturesGridBotExporter configured for
        ``<output_dir>/<SYMBOL>_FuturesGridBots.csv``.
    """
    filename = f"{symbol.upper()}_FuturesGridBots.csv"
    return FuturesGridBotExporter(pathlib.Path(output_dir) / filename)


class FuturesGridBotExporter(CsvExporter):
    """Exports a FuturesGridBotSnapshot to a CSV file."""

    def __init__(self, output_path: str | pathlib.Path) -> None:
        """
        Args:
            output_path: Full destination path, e.g.
                         ``data/exported/CCUSDT_FuturesGridBots.csv``.
                         Use ``make_exporter(symbol)`` to construct this
                         path correctly without hard-coding the filename.
        """
        super().__init__(output_path)

    @property
    def headers(self) -> list[str]:
        return HEADERS

    def rows(self, data: FuturesGridBotSnapshot) -> list[list[Any]]:
        return [
            [
                bot.bot_id,
                bot.symbol,
                bot.bot_status,
                bot.upper_price,
                bot.lower_price,
                bot.grid_num,
                bot.leverage,
                bot.direction,
                bot.investment,
                bot.total_investment,
                bot.grid_profit,
                bot.unrealized_pnl,
                bot.filled_open_qty,
                bot.filled_close_qty,
                bot.created_date,
                bot.created_time,
                bot.stopped_date,
                bot.stopped_time,
            ]
            for bot in data.bots
        ]
