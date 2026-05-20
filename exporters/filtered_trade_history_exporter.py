"""
exporters/filtered_trade_history_exporter.py — factory helpers for
execType-specific trade history CSV exports.

The CSV schema (headers + row mapping) is identical to the general
TradeHistoryExporter, so that class is reused directly.  Only the
output filename differs.

Filename conventions:
    Trade   execType  →  data/<SYMBOL>_tradeType_Trade.csv
    Funding execType  →  data/<SYMBOL>_tradeType_Funding.csv

Public API:
    make_trade_type_exporter(symbol, exec_type, output_dir="data")
        General factory — pass any execType string.

    make_trade_exporter(symbol, output_dir="data")
        Convenience alias for execType "Trade".

    make_funding_exporter(symbol, output_dir="data")
        Convenience alias for execType "Funding".

All three return a fully configured TradeHistoryExporter instance.
"""

from __future__ import annotations

import pathlib

from exporters.trade_history_exporter import TradeHistoryExporter


def make_trade_type_exporter(
    symbol: str,
    exec_type: str,
    output_dir: str | pathlib.Path = "data",
) -> TradeHistoryExporter:
    """
    Build a TradeHistoryExporter with a type-specific output filename.

    Args:
        symbol:     Futures symbol, e.g. "ZECUSDT".  Uppercased automatically.
        exec_type:  Execution type string, e.g. "Trade" or "Funding".
                    Used verbatim in the filename — keep the Bybit casing.
        output_dir: Directory to write into (default: data/).

    Returns:
        TradeHistoryExporter configured for
        <output_dir>/<SYMBOL>_tradeType_<exec_type>.csv
    """
    filename = f"{symbol.upper()}_tradeType_{exec_type}.csv"
    return TradeHistoryExporter(pathlib.Path(output_dir) / filename)


def make_trade_exporter(
    symbol: str,
    output_dir: str | pathlib.Path = "data",
) -> TradeHistoryExporter:
    """Convenience alias — builds the exporter for execType 'Trade'.

    Args:
        symbol:     Futures symbol, e.g. "ZECUSDT".
        output_dir: Directory to write into (default: data/).

    Returns:
        TradeHistoryExporter for <output_dir>/<SYMBOL>_tradeType_Trade.csv
    """
    return make_trade_type_exporter(symbol, "Trade", output_dir)


def make_funding_exporter(
    symbol: str,
    output_dir: str | pathlib.Path = "data",
) -> TradeHistoryExporter:
    """Convenience alias — builds the exporter for execType 'Funding'.

    Args:
        symbol:     Futures symbol, e.g. "ZECUSDT".
        output_dir: Directory to write into (default: data/).

    Returns:
        TradeHistoryExporter for <output_dir>/<SYMBOL>_tradeType_Funding.csv
    """
    return make_trade_type_exporter(symbol, "Funding", output_dir)
