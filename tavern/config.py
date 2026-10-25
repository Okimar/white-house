"""Application config: `data/config.toml` merged over dataclass defaults.

Adding a setting: add a field with a default to the right dataclass, document it in
DEFAULT_CONFIG_TOML, and validate it in `_validate`. Types are checked against the default value.
"""

from __future__ import annotations

import logging
import tomllib
from dataclasses import dataclass, field, fields, is_dataclass, replace
from pathlib import Path
from typing import Any

from tavern.errors import ConfigError
from tavern.util.fs import atomic_write_text

log = logging.getLogger(__name__)

LOG_LEVELS = ("debug", "info", "warning", "error")
LOOPBACK_BIND_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})


@dataclass(frozen=True)
class ServerConfig:
    host: str = "127.0.0.1"
    port: int = 8000
    open_browser: bool = True
    # Accept non-local requests (with the access token) even when bound to loopback.
    # Needed behind tunnels and reverse proxies that connect from 127.0.0.1.
    remote_access: bool = False


@dataclass(frozen=True)
class BackupConfig:
    keep: int = 20


@dataclass(frozen=True)
class Config:
    server: ServerConfig = field(default_factory=ServerConfig)
    backups: BackupConfig = field(default_factory=BackupConfig)
    log_level: str = "info"

    @property
    def remote_enabled(self) -> bool:
        """True when requests from other machines are possible (they must carry the token)."""
        return self.server.remote_access or self.server.host not in LOOPBACK_BIND_HOSTS


DEFAULT_CONFIG_TOML = """\
# Tavern settings. Delete this file to restore defaults.

log_level = "info"          # debug | info | warning | error

[server]
host = "127.0.0.1"          # "0.0.0.0" opens access from the local network (token required)
port = 8000
open_browser = true
remote_access = false       # true: accept remote requests with the token (tunnels, proxies)

[backups]
keep = 20                   # how many database snapshots to keep in data/backups
"""


def load_config(path: Path, *, create: bool = True) -> Config:
    """Read config.toml. Writes the commented default file first if it is missing and `create`."""
    if not path.exists():
        if create:
            atomic_write_text(path, DEFAULT_CONFIG_TOML)
        return Config()
    try:
        raw = tomllib.loads(path.read_text(encoding="utf-8"))
    except (tomllib.TOMLDecodeError, UnicodeDecodeError) as exc:
        raise ConfigError(f"{path}: invalid TOML: {exc}") from exc
    return config_from_dict(raw)


def with_server_overrides(config: Config, **overrides: Any) -> Config:
    """Apply command-line overrides (None values are skipped) and re-validate."""
    changes = {k: v for k, v in overrides.items() if v is not None}
    if not changes:
        return config
    updated = replace(config, server=replace(config.server, **changes))
    _validate(updated)
    return updated


def config_from_dict(raw: dict[str, Any]) -> Config:
    config = _from_dict(Config, raw, "")
    _validate(config)
    return config


def _from_dict(cls: type, data: Any, where: str) -> Any:
    if not isinstance(data, dict):
        raise ConfigError(f"[{where or 'root'}] must be a table")
    defaults = cls()
    known = {f.name for f in fields(cls)}
    kwargs: dict[str, Any] = {}
    for key, value in data.items():
        dotted = f"{where}.{key}" if where else key
        if key not in known:
            log.warning("Unknown config key %r ignored", dotted)
            continue
        default = getattr(defaults, key)
        if is_dataclass(default):
            kwargs[key] = _from_dict(type(default), value, dotted)
            continue
        expected = type(default)
        # bool is a subclass of int: `port = true` must not pass as a number.
        if isinstance(value, bool) != (expected is bool) or not isinstance(value, expected):
            raise ConfigError(
                f"{dotted}: expected {expected.__name__}, got {type(value).__name__} ({value!r})"
            )
        kwargs[key] = value
    return cls(**kwargs)


def _validate(config: Config) -> None:
    if not 1 <= config.server.port <= 65535:
        raise ConfigError(f"server.port: must be 1..65535, got {config.server.port}")
    if not config.server.host.strip():
        raise ConfigError("server.host: must not be empty")
    if config.log_level not in LOG_LEVELS:
        raise ConfigError(f"log_level: must be one of {', '.join(LOG_LEVELS)}")
    if config.backups.keep < 1:
        raise ConfigError("backups.keep: must be at least 1")
