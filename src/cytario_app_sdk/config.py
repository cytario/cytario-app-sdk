"""Connection config: load from YAML via typer-config, validate with pydantic."""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from cytario_app_sdk.errors import ConfigError


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


__all__ = ["ConnectionConfig", "load_config"]
