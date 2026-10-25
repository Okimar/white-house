import re

import pytest

from tavern.config import (
    DEFAULT_CONFIG_TOML,
    Config,
    config_from_dict,
    load_config,
    with_server_overrides,
)
from tavern.errors import ConfigError


def test_missing_file_writes_commented_defaults(tmp_path):
    path = tmp_path / "config.toml"
    assert load_config(path) == Config()
    assert path.read_text(encoding="utf-8") == DEFAULT_CONFIG_TOML


def test_default_toml_matches_dataclass_defaults(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text(DEFAULT_CONFIG_TOML, encoding="utf-8")
    assert load_config(path) == Config()


def test_values_override_defaults():
    cfg = config_from_dict({"log_level": "debug", "server": {"port": 9000}})
    assert cfg.log_level == "debug"
    assert cfg.server.port == 9000
    assert cfg.server.host == "127.0.0.1"


@pytest.mark.parametrize(
    "raw, fragment",
    [
        ({"server": {"port": "8000"}}, "server.port: expected int"),
        ({"server": {"port": True}}, "server.port: expected int"),
        ({"server": {"open_browser": 1}}, "server.open_browser: expected bool"),
        ({"server": {"port": 0}}, "1..65535"),
        ({"log_level": "loud"}, "log_level"),
        ({"backups": {"keep": 0}}, "backups.keep"),
        ({"server": 5}, "[server] must be a table"),
    ],
)
def test_invalid_values_are_rejected(raw, fragment):
    with pytest.raises(ConfigError, match=re.escape(fragment)):
        config_from_dict(raw)


def test_unknown_keys_are_ignored_with_warning(caplog):
    cfg = config_from_dict({"nope": 1, "server": {"also_nope": 2}})
    assert cfg == Config()
    assert "nope" in caplog.text and "server.also_nope" in caplog.text


def test_broken_toml_names_the_file(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text("server = [", encoding="utf-8")
    with pytest.raises(ConfigError, match="config.toml: invalid TOML"):
        load_config(path)


def test_remote_enabled():
    assert not Config().remote_enabled
    assert config_from_dict({"server": {"host": "0.0.0.0"}}).remote_enabled
    assert config_from_dict({"server": {"remote_access": True}}).remote_enabled
    assert not config_from_dict({"server": {"host": "localhost"}}).remote_enabled


def test_overrides_skip_none_and_validate():
    cfg = with_server_overrides(Config(), host=None, port=9001)
    assert cfg.server.port == 9001 and cfg.server.host == "127.0.0.1"
    with pytest.raises(ConfigError):
        with_server_overrides(Config(), port=70000)
