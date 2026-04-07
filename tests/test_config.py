"""
Unit tests for config/config_loader.py and config/logging_setup.py.

All tests use tmp_path so no real data/ directory is ever touched.
Each test follows arrange → act → assert.
"""

from __future__ import annotations

import json
import logging
import pathlib

import pytest

from config.config_loader import (
    AppConfig,
    ConfigError,
    LoggingConfig,
    PathsConfig,
    load_config,
)
from config.logging_setup import setup_logging, _resolve_level


# ===========================================================================
# Helpers
# ===========================================================================

def write_config(path: pathlib.Path, data: dict) -> None:
    """Write *data* as JSON to *path*, creating parent dirs as needed."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")


FULL_CONFIG = {
    "logging": {
        "enabled":     True,
        "level":       "DEBUG",
        "log_to_file": True,
    },
    "paths": {
        "log_file": "bybit_bot.log",
    },
}


# ===========================================================================
# load_config — happy path
# ===========================================================================

class TestLoadConfigHappyPath:

    def test_returns_app_config(self, tmp_path):
        # Arrange
        cfg_path = tmp_path / "config.json"
        write_config(cfg_path, FULL_CONFIG)

        # Act
        result = load_config(cfg_path)

        # Assert
        assert isinstance(result, AppConfig)

    def test_logging_enabled_parsed(self, tmp_path):
        # Arrange
        cfg_path = tmp_path / "config.json"
        write_config(cfg_path, FULL_CONFIG)

        # Act
        result = load_config(cfg_path)

        # Assert
        assert result.logging.enabled is True

    def test_logging_level_parsed_and_uppercased(self, tmp_path):
        # Arrange — provide lowercase to prove normalisation
        data = {**FULL_CONFIG, "logging": {**FULL_CONFIG["logging"], "level": "debug"}}
        cfg_path = tmp_path / "config.json"
        write_config(cfg_path, data)

        # Act
        result = load_config(cfg_path)

        # Assert
        assert result.logging.level == "DEBUG"

    def test_log_to_file_parsed(self, tmp_path):
        # Arrange
        cfg_path = tmp_path / "config.json"
        write_config(cfg_path, FULL_CONFIG)

        # Act
        result = load_config(cfg_path)

        # Assert
        assert result.logging.log_to_file is True

    def test_log_file_path_parsed(self, tmp_path):
        # Arrange
        cfg_path = tmp_path / "config.json"
        write_config(cfg_path, FULL_CONFIG)

        # Act
        result = load_config(cfg_path)

        # Assert
        assert result.paths.log_file == "bybit_bot.log"

    def test_data_directory_is_created_if_absent(self, tmp_path):
        # Arrange — directory does not exist yet
        cfg_dir  = tmp_path / "data"
        cfg_path = cfg_dir / "config.json"
        write_config(cfg_path, FULL_CONFIG)

        # Act
        load_config(cfg_path)

        # Assert
        assert cfg_dir.is_dir()

    def test_partial_logging_section_uses_defaults(self, tmp_path):
        # Arrange — only 'enabled' provided; other keys should fall back
        partial = {"logging": {"enabled": False}}
        cfg_path = tmp_path / "config.json"
        write_config(cfg_path, partial)

        # Act
        result = load_config(cfg_path)

        # Assert
        assert result.logging.enabled is False
        assert result.logging.level == "INFO"       # default
        assert result.logging.log_to_file is False  # default

    def test_empty_object_uses_all_defaults(self, tmp_path):
        # Arrange
        cfg_path = tmp_path / "config.json"
        write_config(cfg_path, {})

        # Act
        result = load_config(cfg_path)

        # Assert — all defaults intact
        assert result.logging.enabled is True
        assert result.logging.level == "INFO"
        assert result.logging.log_to_file is False
        assert result.paths.log_file == "bybit_bot.log"

    def test_log_file_path_property_returns_absolute_path(self, tmp_path):
        # Arrange
        cfg_path = tmp_path / "config.json"
        write_config(cfg_path, FULL_CONFIG)

        # Act
        result = load_config(cfg_path)

        # Assert — log_file_path is absolute and ends with the filename
        assert result.log_file_path.is_absolute()
        assert result.log_file_path.name == "bybit_bot.log"


# ===========================================================================
# load_config — missing file
# ===========================================================================

class TestLoadConfigMissingFile:

    def test_missing_file_returns_default_config(self, tmp_path):
        # Arrange — file does not exist
        cfg_path = tmp_path / "config.json"

        # Act
        result = load_config(cfg_path)

        # Assert
        assert isinstance(result, AppConfig)

    def test_missing_file_writes_template(self, tmp_path):
        # Arrange
        cfg_path = tmp_path / "config.json"

        # Act
        load_config(cfg_path)

        # Assert — template was written
        assert cfg_path.exists()

    def test_written_template_is_valid_json(self, tmp_path):
        # Arrange
        cfg_path = tmp_path / "config.json"
        load_config(cfg_path)

        # Act
        data = json.loads(cfg_path.read_text(encoding="utf-8"))

        # Assert
        assert isinstance(data, dict)

    def test_written_template_has_logging_section(self, tmp_path):
        # Arrange
        cfg_path = tmp_path / "config.json"
        load_config(cfg_path)

        # Act
        data = json.loads(cfg_path.read_text(encoding="utf-8"))

        # Assert
        assert "logging" in data

    def test_written_template_has_paths_section(self, tmp_path):
        # Arrange
        cfg_path = tmp_path / "config.json"
        load_config(cfg_path)

        # Act
        data = json.loads(cfg_path.read_text(encoding="utf-8"))

        # Assert
        assert "paths" in data

    def test_missing_file_returns_logging_enabled_by_default(self, tmp_path):
        # Arrange
        cfg_path = tmp_path / "config.json"

        # Act
        result = load_config(cfg_path)

        # Assert
        assert result.logging.enabled is True


# ===========================================================================
# load_config — invalid file content
# ===========================================================================

class TestLoadConfigInvalidContent:

    def test_invalid_json_raises_config_error(self, tmp_path):
        # Arrange
        cfg_path = tmp_path / "config.json"
        cfg_path.write_text("{ not valid json }", encoding="utf-8")

        # Act / Assert
        with pytest.raises(ConfigError, match="invalid JSON"):
            load_config(cfg_path)

    def test_json_array_at_root_raises_config_error(self, tmp_path):
        # Arrange — top-level array instead of object
        cfg_path = tmp_path / "config.json"
        cfg_path.write_text("[1, 2, 3]", encoding="utf-8")

        # Act / Assert
        with pytest.raises(ConfigError):
            load_config(cfg_path)

    def test_logging_section_not_an_object_raises_config_error(self, tmp_path):
        # Arrange
        cfg_path = tmp_path / "config.json"
        write_config(cfg_path, {"logging": "not_an_object"})

        # Act / Assert
        with pytest.raises(ConfigError):
            load_config(cfg_path)

    def test_paths_section_not_an_object_raises_config_error(self, tmp_path):
        # Arrange
        cfg_path = tmp_path / "config.json"
        write_config(cfg_path, {"paths": 42})

        # Act / Assert
        with pytest.raises(ConfigError):
            load_config(cfg_path)

    def test_config_error_message_mentions_file(self, tmp_path):
        # Arrange
        cfg_path = tmp_path / "config.json"
        cfg_path.write_text("{{ bad", encoding="utf-8")

        # Act / Assert
        with pytest.raises(ConfigError) as exc_info:
            load_config(cfg_path)
        assert str(cfg_path) in str(exc_info.value) or "JSON" in str(exc_info.value)


# ===========================================================================
# AppConfig dataclass
# ===========================================================================

class TestAppConfig:

    def test_default_logging_config(self):
        cfg = AppConfig()

        assert cfg.logging.enabled is True
        assert cfg.logging.level == "INFO"
        assert cfg.logging.log_to_file is False

    def test_default_paths_config(self):
        cfg = AppConfig()

        assert cfg.paths.log_file == "bybit_bot.log"

    def test_log_file_path_is_inside_data_dir(self):
        cfg = AppConfig()

        # The log file must live under the project's data/ directory
        assert cfg.log_file_path.parent.name == "data"
        assert cfg.log_file_path.name == "bybit_bot.log"

    def test_custom_log_file_name_reflected_in_path(self):
        cfg = AppConfig(paths=PathsConfig(log_file="custom.log"))

        assert cfg.log_file_path.name == "custom.log"


# ===========================================================================
# _resolve_level helper
# ===========================================================================

class TestResolveLoglevel:

    def test_debug(self):
        assert _resolve_level("DEBUG") == logging.DEBUG

    def test_info(self):
        assert _resolve_level("INFO") == logging.INFO

    def test_warning(self):
        assert _resolve_level("WARNING") == logging.WARNING

    def test_error(self):
        assert _resolve_level("ERROR") == logging.ERROR

    def test_critical(self):
        assert _resolve_level("CRITICAL") == logging.CRITICAL

    def test_lowercase_accepted(self):
        assert _resolve_level("debug") == logging.DEBUG

    def test_unknown_level_returns_info(self):
        assert _resolve_level("NONSENSE") == logging.INFO


# ===========================================================================
# setup_logging
# ===========================================================================

class TestSetupLogging:

    def teardown_method(self):
        """Reset the root logger after every test to avoid state bleed."""
        root = logging.getLogger()
        root.handlers.clear()
        root.setLevel(logging.WARNING)
        logging.disable(logging.NOTSET)

    def test_disabled_logging_silences_all_output(self):
        # Arrange
        config = AppConfig(logging=LoggingConfig(enabled=False))

        # Act
        setup_logging(config)

        # Assert — logging.disable raises the threshold above CRITICAL
        assert logging.root.manager.disable >= logging.CRITICAL

    def test_enabled_logging_attaches_console_handler(self):
        # Arrange
        config = AppConfig(logging=LoggingConfig(enabled=True, level="INFO"))

        # Act
        setup_logging(config)

        # Assert
        root = logging.getLogger()
        assert any(isinstance(h, logging.StreamHandler) for h in root.handlers)

    def test_log_to_file_creates_file_handler(self, tmp_path):
        # Arrange
        log_path = tmp_path / "test.log"
        config = AppConfig(
            logging=LoggingConfig(enabled=True, level="DEBUG", log_to_file=True),
            paths=PathsConfig(log_file=log_path.name),
        )
        # Override log_file_path to point at tmp_path
        config.__class__.log_file_path = property(lambda self: log_path)

        # Act
        setup_logging(config)

        # Assert
        root = logging.getLogger()
        file_handlers = [h for h in root.handlers if isinstance(h, logging.FileHandler)]
        assert len(file_handlers) == 1

    def test_root_logger_level_set_correctly(self):
        # Arrange
        config = AppConfig(logging=LoggingConfig(enabled=True, level="DEBUG"))

        # Act
        setup_logging(config)

        # Assert
        assert logging.getLogger().level == logging.DEBUG

    def test_pybit_logger_is_captured(self):
        # Arrange
        config = AppConfig(logging=LoggingConfig(enabled=True, level="DEBUG"))

        # Act
        setup_logging(config)

        # Assert — pybit logger level is set (not NOTSET which means inherit-only)
        pybit_level = logging.getLogger("pybit").level
        assert pybit_level == logging.DEBUG

    def test_setup_logging_is_idempotent(self):
        # Arrange
        config = AppConfig(logging=LoggingConfig(enabled=True, level="INFO"))

        # Act — call twice
        setup_logging(config)
        setup_logging(config)

        # Assert — still only one handler (not doubled up)
        root = logging.getLogger()
        assert len(root.handlers) == 1


# ===========================================================================
# ActionsConfig — via load_config
# ===========================================================================

class TestActionsConfig:

    # -----------------------------------------------------------------------
    # Happy path
    # -----------------------------------------------------------------------

    def test_all_actions_enabled_by_default_when_section_absent(self, tmp_path):
        # Arrange — no "actions" key in config
        cfg_path = tmp_path / "config.json"
        write_config(cfg_path, {"logging": {"enabled": False}})

        # Act
        result = load_config(cfg_path)

        # Assert — all four actions present
        from config.config_loader import ALL_ACTIONS
        assert result.enabled_actions == ALL_ACTIONS

    def test_subset_of_actions_loaded(self, tmp_path):
        # Arrange
        cfg_path = tmp_path / "config.json"
        write_config(cfg_path, {
            "actions": {"enabled": ["export_balances", "export_trade_history"]}
        })

        # Act
        result = load_config(cfg_path)

        # Assert
        assert result.enabled_actions == ["export_balances", "export_trade_history"]

    def test_single_action_loaded(self, tmp_path):
        # Arrange
        cfg_path = tmp_path / "config.json"
        write_config(cfg_path, {
            "actions": {"enabled": ["export_order_history"]}
        })

        # Act
        result = load_config(cfg_path)

        # Assert
        assert result.enabled_actions == ["export_order_history"]

    def test_empty_enabled_list_is_accepted(self, tmp_path):
        # Arrange — user wants to run nothing (valid, not an error)
        cfg_path = tmp_path / "config.json"
        write_config(cfg_path, {"actions": {"enabled": []}})

        # Act
        result = load_config(cfg_path)

        # Assert
        assert result.enabled_actions == []

    def test_order_of_actions_is_preserved(self, tmp_path):
        # Arrange — reversed order
        cfg_path = tmp_path / "config.json"
        reversed_actions = [
            "export_order_history",
            "export_trade_history",
            "export_futures_positions",
            "export_balances",
        ]
        write_config(cfg_path, {"actions": {"enabled": reversed_actions}})

        # Act
        result = load_config(cfg_path)

        # Assert
        assert result.enabled_actions == reversed_actions

    def test_missing_file_includes_all_actions(self, tmp_path):
        # Arrange — file does not exist
        cfg_path = tmp_path / "config.json"

        # Act
        result = load_config(cfg_path)

        # Assert — defaults include everything
        from config.config_loader import ALL_ACTIONS
        assert result.enabled_actions == ALL_ACTIONS

    def test_template_written_when_file_missing_contains_actions(self, tmp_path):
        # Arrange
        cfg_path = tmp_path / "config.json"
        load_config(cfg_path)

        # Act
        data = json.loads(cfg_path.read_text(encoding="utf-8"))

        # Assert — template includes the actions section
        assert "actions" in data
        assert "enabled" in data["actions"]
        assert isinstance(data["actions"]["enabled"], list)

    def test_all_four_known_actions_in_template(self, tmp_path):
        # Arrange
        cfg_path = tmp_path / "config.json"
        load_config(cfg_path)
        data = json.loads(cfg_path.read_text(encoding="utf-8"))

        # Act / Assert
        for action in ["export_balances", "export_futures_positions",
                       "export_trade_history", "export_order_history"]:
            assert action in data["actions"]["enabled"]

    def test_enabled_actions_property_on_app_config(self, tmp_path):
        # Arrange
        cfg_path = tmp_path / "config.json"
        write_config(cfg_path, {"actions": {"enabled": ["export_balances"]}})

        # Act
        result = load_config(cfg_path)

        # Assert — property delegates to actions.enabled
        assert result.enabled_actions == result.actions.enabled

    # -----------------------------------------------------------------------
    # Validation errors
    # -----------------------------------------------------------------------

    def test_unknown_action_name_raises_config_error(self, tmp_path):
        # Arrange — typo in action name
        cfg_path = tmp_path / "config.json"
        write_config(cfg_path, {"actions": {"enabled": ["export_balances", "typo_action"]}})

        # Act / Assert
        with pytest.raises(ConfigError, match="typo_action"):
            load_config(cfg_path)

    def test_error_message_lists_known_actions(self, tmp_path):
        # Arrange
        cfg_path = tmp_path / "config.json"
        write_config(cfg_path, {"actions": {"enabled": ["bad_action"]}})

        # Act / Assert
        with pytest.raises(ConfigError) as exc_info:
            load_config(cfg_path)
        assert "export_balances" in str(exc_info.value)

    def test_actions_enabled_not_a_list_raises_config_error(self, tmp_path):
        # Arrange — "enabled" is a string, not a list
        cfg_path = tmp_path / "config.json"
        write_config(cfg_path, {"actions": {"enabled": "export_balances"}})

        # Act / Assert
        with pytest.raises(ConfigError, match="array"):
            load_config(cfg_path)

    def test_actions_section_not_an_object_raises_config_error(self, tmp_path):
        # Arrange
        cfg_path = tmp_path / "config.json"
        write_config(cfg_path, {"actions": ["export_balances"]})

        # Act / Assert
        with pytest.raises(ConfigError):
            load_config(cfg_path)

    def test_non_string_item_in_enabled_list_raises_config_error(self, tmp_path):
        # Arrange — list contains a number instead of a string
        cfg_path = tmp_path / "config.json"
        write_config(cfg_path, {"actions": {"enabled": [42]}})

        # Act / Assert
        with pytest.raises(ConfigError):
            load_config(cfg_path)
