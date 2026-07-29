"""Tests for cytario_app_sdk.config.load_config."""

from __future__ import annotations

from pathlib import Path

import pytest

from cytario_app_sdk.config import ConnectionConfig, load_config
from cytario_app_sdk.errors import ConfigError


def test_load_valid_config(tmp_path: Path) -> None:
    cfg = tmp_path / "conn.yaml"
    cfg.write_text(
        "registry: https://harbor.example.com\nuser: robot$cat\nsecret: tok\n",
        encoding="utf-8",
    )
    conn = load_config(cfg)
    assert conn.registry == "https://harbor.example.com"
    assert conn.user == "robot$cat"
    assert conn.secret == "tok"


def test_missing_file_raises(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="not found"):
        load_config(tmp_path / "nope.yaml")


def test_invalid_yaml_raises(tmp_path: Path) -> None:
    cfg = tmp_path / "bad.yaml"
    cfg.write_text("registry: [unclosed", encoding="utf-8")
    with pytest.raises(ConfigError, match="invalid YAML"):
        load_config(cfg)


def test_missing_field_raises(tmp_path: Path) -> None:
    cfg = tmp_path / "partial.yaml"
    cfg.write_text("registry: https://x\nuser: u\n", encoding="utf-8")  # no secret
    with pytest.raises(ConfigError, match="invalid config"):
        load_config(cfg)


def test_extra_field_raises(tmp_path: Path) -> None:
    cfg = tmp_path / "extra.yaml"
    cfg.write_text(
        "registry: https://x\nuser: u\nsecret: s\nextra: nope\n",
        encoding="utf-8",
    )
    with pytest.raises(ConfigError, match="invalid config"):
        load_config(cfg)


def test_non_mapping_raises(tmp_path: Path) -> None:
    cfg = tmp_path / "list.yaml"
    cfg.write_text("- a\n- b\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="mapping"):
        load_config(cfg)


def test_connection_config_model() -> None:
    conn = ConnectionConfig(registry="https://x", user="u", secret="s")
    assert conn.registry == "https://x"
