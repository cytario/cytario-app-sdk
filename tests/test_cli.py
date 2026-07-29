"""End-to-end CLI tests using typer.testing.CliRunner + respx mocks."""

from __future__ import annotations

from pathlib import Path

from typer.testing import CliRunner

from cytario_app_sdk.cli import app

RUNNER = CliRunner()


def test_register_dry_run_validates_yaml(
    example_app_yaml: Path,
    example_connection_yaml: Path,
) -> None:
    result = RUNNER.invoke(
        app,
        [
            "register",
            str(example_app_yaml),
            "--config",
            str(example_connection_yaml),
            "--dry-run",
        ],
    )
    assert result.exit_code == 0, result.stdout
    assert "registering app 'cellseg'" in result.stdout
    assert "DRY RUN" in result.stdout
    assert "application/vnd.cytario.app-definition.v1+json" in result.stdout


def test_register_dry_run_with_inline_credentials(
    example_app_yaml: Path,
    registry_url: str,
) -> None:
    result = RUNNER.invoke(
        app,
        [
            "register",
            str(example_app_yaml),
            "--registry",
            registry_url,
            "--user",
            "robot$cat",
            "--secret",
            "tok",
            "--dry-run",
        ],
    )
    assert result.exit_code == 0, result.stdout
    assert "registering app 'cellseg'" in result.stdout


def test_register_end_to_end(
    example_app_yaml: Path,
    example_connection_yaml: Path,
    full_registry_mock: None,
) -> None:
    result = RUNNER.invoke(
        app,
        [
            "register",
            str(example_app_yaml),
            "--config",
            str(example_connection_yaml),
        ],
    )
    assert result.exit_code == 0, result.stdout
    assert "registered app 'cellseg'" in result.stdout
    assert "referrer manifest sha256:" in result.stdout


def test_register_missing_app_file(
    example_connection_yaml: Path,
    tmp_path: Path,
) -> None:
    result = RUNNER.invoke(
        app,
        [
            "register",
            str(tmp_path / "nope.yaml"),
            "--config",
            str(example_connection_yaml),
        ],
    )
    # typer.Argument(exists=True) rejects the path before our handler runs.
    assert result.exit_code != 0


def test_register_invalid_app_definition(
    example_connection_yaml: Path,
    tmp_path: Path,
) -> None:
    bad = tmp_path / "bad.yaml"
    bad.write_text(
        "name: cellseg\nimage: {repository: cytario/x}\n",  # missing tag AND digest
        encoding="utf-8",
    )
    result = RUNNER.invoke(
        app,
        [
            "register",
            str(bad),
            "--config",
            str(example_connection_yaml),
            "--dry-run",
        ],
    )
    assert result.exit_code == 1, result.output
    assert "error:" in result.output


def test_register_no_connection(
    example_app_yaml: Path,
) -> None:
    # Without --dry-run, the command needs a connection and must fail.
    result = RUNNER.invoke(app, ["register", str(example_app_yaml)])
    assert result.exit_code != 0


def test_help_lists_register() -> None:
    result = RUNNER.invoke(app, ["--help"])
    assert result.exit_code == 0, result.stdout
    assert "register" in result.stdout
