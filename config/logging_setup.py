"""
config/logging_setup.py — configure Python's logging module from AppConfig.

Responsibilities:
  - Map the config level string to a logging constant
  - Attach a StreamHandler (console) and optionally a FileHandler (data/)
  - Capture pybit's own logger so raw HTTP traffic appears in the log

Call setup_logging(config) once, at the very start of main() or build_app(),
before any API calls are made.
"""

from __future__ import annotations

import logging
import pathlib

from config.config_loader import AppConfig, DATA_DIR

# Log format used by all handlers
_FORMAT = "%(asctime)s  %(levelname)-8s  %(name)s — %(message)s"
_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"


def setup_logging(config: AppConfig) -> None:
    """
    Configure the root logger and (optionally) a file handler.

    After this call:
      - Console output is always active when logging.enabled is True
      - File output is written to data/<log_file> when log_to_file is True
      - pybit's internal logger is set to the same level, so raw HTTP
        request details appear at DEBUG level

    Args:
        config: The loaded AppConfig from load_config().
    """
    if not config.logging.enabled:
        # Silence everything — useful for test runs
        logging.disable(logging.CRITICAL)
        return

    level = _resolve_level(config.logging.level)
    formatter = logging.Formatter(_FORMAT, datefmt=_DATE_FORMAT)

    handlers: list[logging.Handler] = [_console_handler(level, formatter)]

    if config.logging.log_to_file:
        handlers.append(_file_handler(config.log_file_path, level, formatter))

    # Configure the root logger
    root = logging.getLogger()
    root.setLevel(level)
    # Remove any handlers added by a previous call (idempotent)
    root.handlers.clear()
    for h in handlers:
        root.addHandler(h)

    # Propagate pybit's internal logs through the root logger.
    # pybit uses the logger named "pybit.unified_trading" for HTTP traffic.
    logging.getLogger("pybit").setLevel(level)

    logging.getLogger(__name__).debug(
        "Logging initialised — level=%s, log_to_file=%s",
        config.logging.level,
        config.logging.log_to_file,
    )


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------

def _resolve_level(level_str: str) -> int:
    """
    Convert a level name string to a logging int constant.

    Falls back to INFO for unrecognised strings.
    """
    numeric = getattr(logging, level_str.upper(), None)
    if not isinstance(numeric, int):
        logging.warning("Unknown log level %r — defaulting to INFO", level_str)
        return logging.INFO
    return numeric


def _console_handler(level: int, formatter: logging.Formatter) -> logging.StreamHandler:
    handler = logging.StreamHandler()
    handler.setLevel(level)
    handler.setFormatter(formatter)
    return handler


def _file_handler(
    path: pathlib.Path,
    level: int,
    formatter: logging.Formatter,
) -> logging.FileHandler:
    path.parent.mkdir(parents=True, exist_ok=True)
    handler = logging.FileHandler(path, encoding="utf-8")
    handler.setLevel(level)
    handler.setFormatter(formatter)
    return handler
