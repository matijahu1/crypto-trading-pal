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
# All known action names — used for validation and as template defaults
# ---------------------------------------------------------------------------

ALL_ACTIONS: list[str] = [
    "export_balances",
    "export_futures_positions",
    "export_trade_history",
    "export_order_history",
]


# ---------------------------------------------------------------------------
# Error type
# ---------------------------------------------------------------------------

class ConfigError(Exception):
    """Raised when config.json is missing, invalid JSON, or missing keys."""


# ---------------------------------------------------------------------------
# Config dataclasses — one field per supported config key
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
class ActionsConfig:
    """Controls which batch export steps are executed by main.py."""

    enabled: list[str] = field(default_factory=lambda: list(ALL_ACTIONS))
    """
    List of action names to run.  Order is preserved.

    Recognised values:
      "export_balances"          — fetch wallet balances → data/balance.csv
      "export_futures_positions" — fetch open positions  → data/futures_positions.csv
      "export_trade_history"     — fetch trade history   → data/<SYMBOL>_tradeHistory.csv
      "export_order_history"     — fetch order history   → data/<SYMBOL>_orderHistory.csv
    """


@dataclass
class AppConfig:
    """Typed representation of config.json."""

    logging: LoggingConfig = field(default_factory=LoggingConfig)
    paths:   PathsConfig   = field(default_factory=PathsConfig)
    actions: ActionsConfig = field(default_factory=ActionsConfig)

    @property
    def log_file_path(self) -> pathlib.Path:
        """Absolute path to the log file (always inside data/)."""
        return DATA_DIR / self.paths.log_file

    @property
    def enabled_actions(self) -> list[str]:
        """Convenience accessor — the ordered list of actions to execute."""
        return self.actions.enabled


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
        log_raw     = data.get("logging", {})
        paths_raw   = data.get("paths",   {})
        actions_raw = data.get("actions", {})

        if not isinstance(log_raw, dict):
            raise ConfigError(f"'logging' must be an object in {config_path}")
        if not isinstance(paths_raw, dict):
            raise ConfigError(f"'paths' must be an object in {config_path}")
        if not isinstance(actions_raw, dict):
            raise ConfigError(f"'actions' must be an object in {config_path}")

        logging_cfg = LoggingConfig(
            enabled=bool(log_raw.get("enabled",     True)),
            level=str(log_raw.get("level",          "INFO")).upper(),
            log_to_file=bool(log_raw.get("log_to_file", False)),
        )
        paths_cfg = PathsConfig(
            log_file=str(paths_raw.get("log_file", "bybit_bot.log")),
        )
        actions_cfg = _parse_actions(actions_raw, config_path)

    except (TypeError, ValueError) as exc:
        raise ConfigError(
            f"Invalid value in {config_path}: {exc}"
        ) from exc

    return AppConfig(logging=logging_cfg, paths=paths_cfg, actions=actions_cfg)


def _parse_actions(actions_raw: dict, config_path: pathlib.Path) -> ActionsConfig:
    """
    Parse and validate the 'actions' block.

    The 'enabled' key must be a list of strings.  Unknown action names
    raise ConfigError so typos are caught early rather than silently skipped.
    """
    enabled_raw = actions_raw.get("enabled", list(ALL_ACTIONS))

    if not isinstance(enabled_raw, list):
        raise ConfigError(
            f"'actions.enabled' must be a JSON array in {config_path}"
        )

    enabled: list[str] = []
    for item in enabled_raw:
        if not isinstance(item, str):
            raise ConfigError(
                f"Every entry in 'actions.enabled' must be a string, "
                f"got {type(item).__name__!r} in {config_path}"
            )
        if item not in ALL_ACTIONS:
            raise ConfigError(
                f"Unknown action {item!r} in {config_path}. "
                f"Known actions: {ALL_ACTIONS}"
            )
        enabled.append(item)

    return ActionsConfig(enabled=enabled)


def _write_template(config_path: pathlib.Path) -> None:
    """Write a starter template so the user knows what to configure."""
    template = {
        "logging": {
            "enabled":     True,
            "level":       "INFO",
            "log_to_file": False,
        },
        "paths": {
            "log_file": "bybit_bot.log",
        },
        "actions": {
            "enabled": list(ALL_ACTIONS),
        },
    }
    config_path.write_text(
        json.dumps(template, indent=4) + "\n",
        encoding="utf-8",
    )
