"""
config/config_loader.py — load and validate the application configuration.

Responsibilities:
  - Locate data/config.json relative to the project root
  - Create the data/ directory if it does not exist
  - Parse and validate the JSON structure
  - Return a typed AppConfig dataclass
  - Raise ConfigError with a clear message on any problem

Config file location: data/config.json
"""

from __future__ import annotations

import json
import pathlib
from dataclasses import dataclass, field


# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

# Project root = the directory that contains this config/ package
_PROJECT_ROOT = pathlib.Path(__file__).parent.parent
DATA_DIR       = _PROJECT_ROOT / "data"
CONFIG_PATH    = DATA_DIR / "config.json"


# ---------------------------------------------------------------------------
# Error type
# ---------------------------------------------------------------------------

class ConfigError(Exception):
    """Raised when config.json is missing, invalid JSON, or missing keys."""


# ---------------------------------------------------------------------------
# Config dataclass — one field per supported config key
# ---------------------------------------------------------------------------

@dataclass
class LoggingConfig:
    enabled:     bool = True
    level:       str  = "INFO"
    log_to_file: bool = False


@dataclass
class PathsConfig:
    log_file: str = "bybit_bot.log"


@dataclass
class AppConfig:
    """Typed representation of config.json."""

    logging: LoggingConfig = field(default_factory=LoggingConfig)
    paths:   PathsConfig   = field(default_factory=PathsConfig)

    @property
    def log_file_path(self) -> pathlib.Path:
        """Absolute path to the log file (always inside data/)."""
        return DATA_DIR / self.paths.log_file


# ---------------------------------------------------------------------------
# Loader
# ---------------------------------------------------------------------------

def load_config(config_path: pathlib.Path = CONFIG_PATH) -> AppConfig:
    """
    Load and validate the application configuration from a JSON file.

    The data/ directory is created automatically if absent.
    If config.json does not exist, a default AppConfig is returned and a
    template file is written so the user knows what to fill in.

    Args:
        config_path: Path to the JSON file (default: data/config.json).

    Returns:
        Validated AppConfig dataclass.

    Raises:
        ConfigError: If the file contains invalid JSON or a required key
                     has the wrong type.
    """
    # Ensure the data directory exists before we try to read from it
    config_path.parent.mkdir(parents=True, exist_ok=True)

    if not config_path.exists():
        _write_template(config_path)
        return AppConfig()   # safe defaults

    try:
        raw = config_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ConfigError(f"Cannot read {config_path}: {exc}") from exc

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ConfigError(
            f"{config_path} contains invalid JSON — {exc}"
        ) from exc

    if not isinstance(data, dict):
        raise ConfigError(
            f"{config_path} must be a JSON object at the top level"
        )

    return _parse(data, config_path)


def _parse(data: dict, config_path: pathlib.Path) -> AppConfig:
    """Validate types and build the AppConfig from the raw dict."""
    try:
        log_raw   = data.get("logging", {})
        paths_raw = data.get("paths",   {})

        if not isinstance(log_raw, dict):
            raise ConfigError(f"'logging' must be an object in {config_path}")
        if not isinstance(paths_raw, dict):
            raise ConfigError(f"'paths' must be an object in {config_path}")

        logging_cfg = LoggingConfig(
            enabled=bool(log_raw.get("enabled",     True)),
            level=str(log_raw.get("level",          "INFO")).upper(),
            log_to_file=bool(log_raw.get("log_to_file", False)),
        )
        paths_cfg = PathsConfig(
            log_file=str(paths_raw.get("log_file", "bybit_bot.log")),
        )

    except (TypeError, ValueError) as exc:
        raise ConfigError(
            f"Invalid value in {config_path}: {exc}"
        ) from exc

    return AppConfig(logging=logging_cfg, paths=paths_cfg)


def _write_template(config_path: pathlib.Path) -> None:
    """Write a commented template so the user knows what to configure."""
    template = {
        "logging": {
            "enabled":     True,
            "level":       "INFO",
            "log_to_file": False,
        },
        "paths": {
            "log_file": "bybit_bot.log",
        },
    }
    config_path.write_text(
        json.dumps(template, indent=4) + "\n",
        encoding="utf-8",
    )
