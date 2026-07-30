"""Connection config: load from YAML via typer-config, validate with pydantic.

The CLI accepts an explicit `--config <path>` flag (consumed by typer-config's
`@use_config`). When omitted, the Typer callback `auto_yaml_conf_callback`
auto-discovers a config in well-known locations so the developer does not have
to repeat `--config conn.yaml` on every invocation:

  1. `./cytario-app-sdk.yaml` — project-local (committed for a team, or a
     personal untracked file at the repo root).
  2. `~/.config/cytario-app-sdk/config.yaml` — user-global default.

The first existing file wins. If neither exists, the callback is a no-op and
the command falls back to `--registry/--user/--secret` flags.
"""

from __future__ import annotations

from pathlib import Path

import click  # noqa: TC002  # typer-config eval_str resolves our annotations at runtime
import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from typer import BadParameter

from cytario_app_sdk.errors import ConfigError

LOCAL_CONFIG_NAME = "cytario-app-sdk.yaml"
USER_CONFIG_DIR = Path.home() / ".config" / "cytario-app-sdk"
USER_CONFIG_NAME = "config.yaml"


class ConnectionConfig(BaseModel):
    """Registry connection settings shared across commands."""

    model_config = ConfigDict(extra="forbid")

    registry: str = Field(..., description="OCI registry base URL, e.g. https://harbor.example.com")
    user: str = Field(..., description="Registry username (often a robot account).")
    secret: str = Field(..., description="Registry password / robot token.")


def load_config(path: str | Path) -> ConnectionConfig:
    """Load and validate a YAML connection config file.

    The file is a flat mapping with `registry`, `user`, `secret` keys — exactly
    the options typer-config would inject from the same file.
    """
    path = Path(path)
    if not path.is_file():
        msg = f"config file not found: {path}"
        raise ConfigError(msg)
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        msg = f"invalid YAML in config file {path}: {exc}"
        raise ConfigError(msg) from exc
    if not isinstance(raw, dict):
        msg = f"config file {path} must be a mapping, got {type(raw).__name__}"
        raise ConfigError(msg)
    try:
        return ConnectionConfig.model_validate(raw)
    except ValidationError as exc:
        msg = f"invalid config in {path}: {exc}"
        raise ConfigError(msg) from exc


__all__ = [
    "LOCAL_CONFIG_NAME",
    "USER_CONFIG_DIR",
    "USER_CONFIG_NAME",
    "ConnectionConfig",
    "auto_yaml_conf_callback",
    "load_config",
]


def _resolve_config_path(param_value: str | None) -> Path | None:
    """Resolve the `--config` value to a path, auto-discovering when omitted.

    An explicit non-empty `param_value` is required to exist (mirrors the
    strict `typer_config` loader). When empty/None, the well-known locations
    are probed in order; the first hit wins. Returns None when nothing is
    found so the caller can proceed with `--registry/--user/--secret` flags.
    """
    if param_value:
        path = Path(param_value)
        if not path.is_file():
            msg = f"config file not found: {path}"
            raise BadParameter(msg)
        return path
    cwd_config = Path.cwd() / LOCAL_CONFIG_NAME
    if cwd_config.is_file():
        return cwd_config
    user_config = USER_CONFIG_DIR / USER_CONFIG_NAME
    if user_config.is_file():
        return user_config
    return None


def auto_yaml_conf_callback(
    ctx: click.Context,
    param: click.Parameter,
    param_value: str | None,
) -> str | None:
    """Typer callback for `--config`: load YAML, auto-discover when omitted.

    Compatible with `typer_config.decorators.use_config`. Merges the loaded
    mapping into `ctx.default_map` so Typer populates the command's
    `registry`/`user`/`secret` options from it before the command body runs.
    """
    path = _resolve_config_path(param_value)
    if path is None:
        return param_value
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        msg = f"invalid YAML in config file {path}: {exc}"
        raise BadParameter(msg, ctx=ctx, param=param) from exc
    if raw is None:
        return param_value
    if not isinstance(raw, dict):
        msg = f"config file {path} must be a mapping, got {type(raw).__name__}"
        raise BadParameter(msg, ctx=ctx, param=param)
    ctx.default_map = ctx.default_map or {}
    ctx.default_map.update(raw)
    return param_value
