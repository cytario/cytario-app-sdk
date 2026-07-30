"""Tests for cytario_app_sdk.config."""

from __future__ import annotations

from pathlib import Path

import click
import pytest
from typer import BadParameter

from cytario_app_sdk.config import ConnectionConfig, auto_yaml_conf_callback, load_config
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


# ---------------------------------------------------------------------------
# auto_yaml_conf_callback — Typer --config auto-discovery
# ---------------------------------------------------------------------------


def _callback(param_value, *, default_map=None):
    """Run auto_yaml_conf_callback with a fresh context; return the mutated default_map."""
    ctx = click.Context(click.Command("test"))
    ctx.default_map = default_map or {}
    # `param` is only forwarded to BadParameter on errors; a dummy is fine.
    auto_yaml_conf_callback(ctx, click.Option(["--config"]), param_value)
    return ctx.default_map


def test_auto_discover_local_config(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A ./cytario-app-sdk.yaml in cwd is auto-discovered when --config is omitted."""
    cfg = tmp_path / "cytario-app-sdk.yaml"
    cfg.write_text(
        "registry: https://harbor.example.com\nuser: robot$cat\nsecret: tok\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    default_map = _callback(None)
    assert default_map.get("registry") == "https://harbor.example.com"
    assert default_map.get("user") == "robot$cat"
    assert default_map.get("secret") == "tok"


def test_auto_discover_user_config(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """~/.config/cytario-app-sdk/config.yaml is the fallback when no local file exists."""
    user_dir = tmp_path / "home"
    user_config_dir = user_dir / ".config" / "cytorio-app-sdk"
    user_config_dir.mkdir(parents=True)
    (user_config_dir / "config.yaml").write_text(
        "registry: https://user.example.com\nuser: robot$u\nsecret: utok\n",
        encoding="utf-8",
    )
    import cytario_app_sdk.config as cfg  # noqa: PLC0415

    monkeypatch.setattr(cfg, "USER_CONFIG_DIR", user_config_dir)
    monkeypatch.chdir(tmp_path)  # ensure no local file
    default_map = _callback(None)
    assert default_map.get("registry") == "https://user.example.com"


def test_explicit_config_wins_over_auto_discover(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An explicit --config path is used even when an auto-discoverable file exists."""
    explicit = tmp_path / "explicit.yaml"
    explicit.write_text(
        "registry: https://explicit.example.com\nuser: robot$e\nsecret: etok\n",
        encoding="utf-8",
    )
    # A local file that should NOT be picked up.
    (tmp_path / "cytario-app-sdk.yaml").write_text(
        "registry: https://ignored.example.com\nuser: robot$i\nsecret: itok\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    default_map = _callback(str(explicit))
    assert default_map.get("registry") == "https://explicit.example.com"


def test_explicit_missing_config_raises(tmp_path: Path) -> None:
    """An explicit --config pointing at a non-existent file fails fast."""
    with pytest.raises(BadParameter, match="not found"):
        _callback(str(tmp_path / "nope.yaml"))


def test_no_config_and_no_auto_discover_is_noop(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With no --config and no auto-discoverable file, the callback is a no-op."""
    import cytario_app_sdk.config as cfg  # noqa: PLC0415

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(cfg, "USER_CONFIG_DIR", tmp_path / "nonexistent")
    default_map = _callback(None, default_map={"existing": "value"})
    assert default_map == {"existing": "value"}


def test_invalid_auto_discovered_yaml_raises(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A YAML parse error in an auto-discovered file surfaces as BadParameter."""
    (tmp_path / "cytario-app-sdk.yaml").write_text("registry: [unclosed", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    with pytest.raises(BadParameter, match="invalid YAML"):
        _callback(None)


def test_non_mapping_auto_discovered_config_raises(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A non-mapping auto-discovered config is rejected."""
    (tmp_path / "cytario-app-sdk.yaml").write_text("- a\n- b\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    with pytest.raises(BadParameter, match="mapping"):
        _callback(None)
