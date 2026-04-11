"""
exporters/path_provider.py — central authority for export file paths.

Responsibilities:
  - Accept a base output directory and a trading symbol at construction time.
  - Expose one typed method per export kind that returns the full output path.
  - Perform *zero* filesystem I/O so the class is trivially mockable in unit
    tests and safe to call before any API request is made.

Usage (production)::

    from exporters.path_provider import PathProvider

    provider = PathProvider(base_dir=config.exported_dir, symbol=config.symbol)
    path = provider.order_history_path()   # → PosixPath("data/exported/CCUSDT_orderHistory.csv")

Usage (unit test — no real filesystem needed)::

    from unittest.mock import MagicMock
    import pathlib

    provider = MagicMock(spec=PathProvider)
    provider.order_history_path.return_value = pathlib.Path("/tmp/test_orderHistory.csv")

Naming conventions (mirrors legacy filenames):
  order_history   → <SYMBOL>_orderHistory.csv
  trade_history   → <SYMBOL>_tradeHistory.csv
  executions      → <SYMBOL>_executions.csv
  recent_fills    → <SYMBOL>_recent_fills.csv
  balances        → balance.csv               (symbol-independent)
  futures_pos     → futures_positions.csv     (symbol-independent)
"""

from __future__ import annotations

import pathlib


class PathProvider:
    """
    Generates export file paths from a base directory and a trading symbol.

    All paths are computed on construction so callers can inspect them before
    any network or filesystem operation takes place.

    Args:
        base_dir: Root directory for exported CSV files.
                  Typically ``data/exported/`` as set in config.json.
        symbol:   Futures symbol in any case, e.g. ``"CCUSDT"``.
                  Stored internally as upper-case.
    """

    def __init__(self, base_dir: str | pathlib.Path, symbol: str) -> None:
        self._base_dir = pathlib.Path(base_dir)
        self._symbol   = symbol.strip().upper()

    # ------------------------------------------------------------------
    # Public accessors
    # ------------------------------------------------------------------

    @property
    def base_dir(self) -> pathlib.Path:
        """The resolved base output directory."""
        return self._base_dir

    @property
    def symbol(self) -> str:
        """The upper-cased trading symbol."""
        return self._symbol

    # ------------------------------------------------------------------
    # Symbol-specific paths
    # ------------------------------------------------------------------

    def order_history_path(self) -> pathlib.Path:
        """Full path for the order-history CSV.

        Returns:
            e.g. ``data/exported/CCUSDT_orderHistory.csv``
        """
        return self._base_dir / f"{self._symbol}_orderHistory.csv"

    def trade_history_path(self) -> pathlib.Path:
        """Full path for the trade-history CSV.

        Returns:
            e.g. ``data/exported/CCUSDT_tradeHistory.csv``
        """
        return self._base_dir / f"{self._symbol}_tradeHistory.csv"

    def executions_path(self) -> pathlib.Path:
        """Full path for the (windowed) executions CSV.

        Returns:
            e.g. ``data/exported/CCUSDT_executions.csv``
        """
        return self._base_dir / f"{self._symbol}_executions.csv"

    def recent_fills_path(self) -> pathlib.Path:
        """Full path for the recent-fills CSV.

        Returns:
            e.g. ``data/exported/CCUSDT_recent_fills.csv``
        """
        return self._base_dir / f"{self._symbol}_recent_fills.csv"

    # ------------------------------------------------------------------
    # Symbol-independent paths
    # ------------------------------------------------------------------

    def balance_path(self) -> pathlib.Path:
        """Full path for the wallet-balance CSV (not symbol-specific).

        Returns:
            e.g. ``data/exported/balance.csv``
        """
        return self._base_dir / "balance.csv"

    def futures_positions_path(self) -> pathlib.Path:
        """Full path for the futures-positions CSV (not symbol-specific).

        Returns:
            e.g. ``data/exported/futures_positions.csv``
        """
        return self._base_dir / "futures_positions.csv"

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def ensure_dir(self) -> None:
        """Create ``base_dir`` (and any parents) if it does not yet exist.

        This is the *only* filesystem operation in the class. Call it once
        during application startup or at the beginning of a batch run.
        In unit tests, mock this method or simply don't call it.
        """
        self._base_dir.mkdir(parents=True, exist_ok=True)

    def __repr__(self) -> str:  # pragma: no cover
        return f"PathProvider(base_dir={self._base_dir!r}, symbol={self._symbol!r})"
