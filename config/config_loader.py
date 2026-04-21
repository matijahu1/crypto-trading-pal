"""
config/config_loader.py — load and validate the application configuration.

Responsibilities:
  - Locate data/config.json relative to the project root.
  - Create the data/ directory if it does not exist.
  - Parse and validate the JSON structure.
  - Return a typed AppConfig dataclass.
  - Raise ConfigError with a clear message on any problem.

Config file location: data/config.json

Change log:
  - PathsConfig gains ``exported_dir`` (default: ``data/exported/``).
    This is the folder written to by all CSV exporters.
    Override in config.json under ``"paths": { "exported_dir": "..." }``.
  - AppConfig exposes ``exported_dir`` as a convenience property that always
    returns an absolute pathlib.Path ready to hand to PathProvider.
"""

from __future__ import annotations

import json
import pathlib
from dataclasses import dataclass, field

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

_PROJECT_ROOT = pathlib.Path(__file__).parent.parent
DATA_DIR = _PROJECT_ROOT / "data"
CONFIG_PATH = DATA_DIR / "config.json"


# ---------------------------------------------------------------------------
# All known action names
# ---------------------------------------------------------------------------

ALL_ACTIONS: list[str] = [
    "balances",
    "futures_positions",
    "trade_history",
    "order_history",
    "recent_executions",
    "generate_lifo_report",
    "open_orders",
]


# ---------------------------------------------------------------------------
# Error type
# ---------------------------------------------------------------------------


class ConfigError(Exception):
    """Raised when config.json is missing, invalid JSON, or missing keys."""


# ---------------------------------------------------------------------------
# Config dataclasses
# ---------------------------------------------------------------------------


@dataclass
class LoggingConfig:
    enabled: bool = True
    level: str = "INFO"
    log_to_file: bool = False


@dataclass
class PathsConfig:
    log_file: str = "bybit_bot.log"
    exported_dir: str = "data/exported"
    """
    Directory for all exported CSV files.
    Relative paths are resolved from the project root.
    Override in config.json: ``"paths": { "exported_dir": "data/exported" }``
    """


@dataclass
class ActionsConfig:
    """Controls which batch export steps are executed by main.py."""

    enabled: list[str] = field(default_factory=lambda: list(ALL_ACTIONS))


@dataclass
class RequestSettingsConfig:
    """Symbol-level settings used by the symbol-specific batch exports."""

    symbol: str = "BTCUSDT"
    lookback_days_default: int = 30
    recent_executions_limit: int = 10


@dataclass
class AppConfig:
    """Typed representation of config.json."""

    logging: LoggingConfig = field(default_factory=LoggingConfig)
    paths: PathsConfig = field(default_factory=PathsConfig)
    actions: ActionsConfig = field(default_factory=ActionsConfig)
    request_settings: RequestSettingsConfig = field(
        default_factory=RequestSettingsConfig
    )

    # ------------------------------------------------------------------
    # Convenience properties
    # ------------------------------------------------------------------

    @property
    def log_file_path(self) -> pathlib.Path:
        return DATA_DIR / self.paths.log_file

    @property
    def enabled_actions(self) -> list[str]:
        return self.actions.enabled

    @property
    def symbol(self) -> str:
        return self.request_settings.symbol

    @property
    def lookback_days_default(self) -> int:
        return self.request_settings.lookback_days_default

    @property
    def recent_executions_limit(self) -> int:
        return self.request_settings.recent_executions_limit

    @property
    def exported_dir(self) -> pathlib.Path:
        """
        Absolute path to the CSV output folder.

        If ``paths.exported_dir`` in config.json is a relative path it is
        resolved against the project root, so it works regardless of the
        working directory from which the script is launched.

        Example::

            config.exported_dir  # → PosixPath("/home/user/project/data/exported")
        """
        p = pathlib.Path(self.paths.exported_dir)
        return p if p.is_absolute() else _PROJECT_ROOT / p


# ---------------------------------------------------------------------------
# Loader
# ---------------------------------------------------------------------------


def load_config(config_path: pathlib.Path = CONFIG_PATH) -> AppConfig:
    config_path.parent.mkdir(parents=True, exist_ok=True)

    if not config_path.exists():
        _write_template(config_path)
        return AppConfig()

    try:
        raw = config_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ConfigError(f"Cannot read {config_path}: {exc}") from exc

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ConfigError(f"{config_path} contains invalid JSON — {exc}") from exc

    if not isinstance(data, dict):
        raise ConfigError(f"{config_path} must be a JSON object at the top level")

    return _parse(data, config_path)


def _parse(data: dict, config_path: pathlib.Path) -> AppConfig:
    try:
        log_raw = data.get("logging", {})
        paths_raw = data.get("paths", {})
        actions_raw = data.get("actions", {})
        request_settings_raw = data.get("request_settings", {})

        if not isinstance(log_raw, dict):
            raise ConfigError(f"'logging' must be an object in {config_path}")
        if not isinstance(paths_raw, dict):
            raise ConfigError(f"'paths' must be an object in {config_path}")
        if not isinstance(actions_raw, dict):
            raise ConfigError(f"'actions' must be an object in {config_path}")
        if not isinstance(request_settings_raw, dict):
            raise ConfigError(f"'request_settings' must be an object in {config_path}")

        logging_cfg = LoggingConfig(
            enabled=bool(log_raw.get("enabled", True)),
            level=str(log_raw.get("level", "INFO")).upper(),
            log_to_file=bool(log_raw.get("log_to_file", False)),
        )
        paths_cfg = _parse_paths(paths_raw, config_path)
        actions_cfg = _parse_actions(actions_raw, config_path)
        request_settings_cfg = _parse_request_settings(
            request_settings_raw, config_path
        )

    except (TypeError, ValueError) as exc:
        raise ConfigError(f"Invalid value in {config_path}: {exc}") from exc

    return AppConfig(
        logging=logging_cfg,
        paths=paths_cfg,
        actions=actions_cfg,
        request_settings=request_settings_cfg,
    )


def _parse_paths(paths_raw: dict, config_path: pathlib.Path) -> PathsConfig:
    log_file = str(paths_raw.get("log_file", "bybit_bot.log"))

    exported_dir = paths_raw.get("exported_dir", "data/exported")
    if not isinstance(exported_dir, str):
        raise ConfigError(
            f"'paths.exported_dir' must be a string in {config_path}, "
            f"got {type(exported_dir).__name__!r}"
        )
    if not exported_dir.strip():
        raise ConfigError(f"'paths.exported_dir' must not be empty in {config_path}")

    return PathsConfig(log_file=log_file, exported_dir=exported_dir.strip())


def _parse_actions(actions_raw: dict, config_path: pathlib.Path) -> ActionsConfig:
    enabled_raw = actions_raw.get("enabled", list(ALL_ACTIONS))

    if not isinstance(enabled_raw, list):
        raise ConfigError(f"'actions.enabled' must be a JSON array in {config_path}")

    enabled: list[str] = []
    for item in enabled_raw:
        if not isinstance(item, str):
            raise ConfigError(
                f"Every entry in 'actions.enabled' must be a string, "
                f"got {type(item).__name__!r} in {config_path}"
            )
        if item not in ALL_ACTIONS:
            raise ConfigError(
                f"Unknown action {item!r} in {config_path}. Known actions: {ALL_ACTIONS}"
            )
        enabled.append(item)

    return ActionsConfig(enabled=enabled)


def _parse_request_settings(
    request_settings_raw: dict, config_path: pathlib.Path
) -> RequestSettingsConfig:
    symbol = request_settings_raw.get("symbol", "ACCOUNT")
    if not symbol:
        symbol = "ACCOUNT"
    if not isinstance(symbol, str):
        raise ConfigError(
            f"'request_settings.symbol' must be a string, got {type(symbol).__name__!r}"
        )
    if not symbol.strip():
        raise ConfigError("'request_settings.symbol' must not be empty")

    lookback_days_default = request_settings_raw.get("lookback_days_default", 30)
    if not isinstance(lookback_days_default, int) or isinstance(
        lookback_days_default, bool
    ):
        raise ConfigError("'request_settings.lookback_days_default' must be an integer")
    if lookback_days_default < 1:
        raise ConfigError("'request_settings.lookback_days_default' must be positive")

    recent_executions_limit = request_settings_raw.get("recent_executions_limit", 10)
    if not isinstance(recent_executions_limit, int) or isinstance(
        recent_executions_limit, bool
    ):
        raise ConfigError(
            "'request_settings.recent_executions_limit' must be an integer"
        )
    if recent_executions_limit < 1:
        raise ConfigError("'request_settings.recent_executions_limit' must be positive")

    return RequestSettingsConfig(
        symbol=symbol.strip(),
        lookback_days_default=lookback_days_default,
        recent_executions_limit=recent_executions_limit,
    )


def _write_template(config_path: pathlib.Path) -> None:
    template = {
        "logging": {
            "enabled": True,
            "level": "INFO",
            "log_to_file": False,
        },
        "paths": {
            "log_file": "bybit_bot.log",
            "exported_dir": "data/exported",
        },
        "actions": {
            "enabled": list(ALL_ACTIONS),
        },
        "request_settings": {
            "symbol": "BTCUSDT",
            "lookback_days_default": 30,
            "recent_executions_limit": 10,
        },
    }
    config_path.write_text(json.dumps(template, indent=4) + "\n", encoding="utf-8")
