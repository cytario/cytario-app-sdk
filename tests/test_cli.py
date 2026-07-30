"""End-to-end CLI tests using typer.testing.CliRunner + httpx mocks."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import TYPE_CHECKING

import httpx
from typer.testing import CliRunner

from cytario_app_sdk.cli import app
from cytario_app_sdk.oci.manifest import APPDEF_ANNOTATION_KEY, OCI_MANIFEST_MEDIA_TYPE

if TYPE_CHECKING:
    import pytest

RUNNER = CliRunner()


def _digest(payload: bytes) -> str:
    return f"sha256:{hashlib.sha256(payload).hexdigest()}"


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
    assert APPDEF_ANNOTATION_KEY in result.stdout


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
    assert "attached annotation" in result.stdout
    assert "new manifest digest" in result.stdout


def test_register_attaches_appdef_annotation_to_pushed_manifest(
    httpx_mock: pytest.FuncFixture,
    example_app_yaml: Path,
    example_connection_yaml: Path,
    registry_url: str,
    repository: str,
    image_tag: str,
    image_manifest: dict,
    image_manifest_digest: str,
) -> None:
    """The pushed manifest MUST carry the app-definition under the appdef
    annotation key so the Cytario runtime's ``extractDefinition`` finds it."""
    httpx_mock.add_response(
        method="GET",
        url=f"{registry_url}/v2/{repository}/manifests/{image_tag}",
        status_code=200,
        headers={
            "Docker-Content-Digest": image_manifest_digest,
            "Content-Type": OCI_MANIFEST_MEDIA_TYPE,
        },
        json=image_manifest,
    )

    pushed_manifest: dict = {}

    def _capture_put(request: httpx.Request) -> httpx.Response:
        pushed_manifest["body"] = json.loads(request.content)
        return httpx.Response(
            201,
            headers={"Docker-Content-Digest": _digest(request.content)},
        )

    httpx_mock.add_callback(
        _capture_put,
        method="PUT",
        url=re.compile(rf"{registry_url}/v2/{repository}/manifests/{re.escape(image_tag)}"),
    )

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

    body = pushed_manifest["body"]
    assert APPDEF_ANNOTATION_KEY in body["annotations"]
    annotation_value = body["annotations"][APPDEF_ANNOTATION_KEY]
    definition = json.loads(annotation_value)
    assert definition["applicationId"] == "cellseg"
    assert definition["name"] == "Cell Segmentation"
    # Existing annotations are preserved.
    assert body["annotations"]["org.opencontainers.image.created"] == "2026-01-01T00:00:00Z"


def test_register_preserves_original_manifest_media_type(
    httpx_mock: pytest.FuncFixture,
    example_app_yaml: Path,
    example_connection_yaml: Path,
    registry_url: str,
    repository: str,
    image_tag: str,
    image_manifest: dict,
    image_manifest_digest: str,
) -> None:
    """The PUT Content-Type must match the GET Content-Type so the registry
    stores the annotated manifest as the same kind of manifest."""
    httpx_mock.add_response(
        method="GET",
        url=f"{registry_url}/v2/{repository}/manifests/{image_tag}",
        status_code=200,
        headers={
            "Docker-Content-Digest": image_manifest_digest,
            "Content-Type": "application/vnd.docker.distribution.manifest.v2+json",
        },
        json=image_manifest,
    )

    seen_content_type: dict[str, str] = {}

    def _capture_put(request: httpx.Request) -> httpx.Response:
        seen_content_type["value"] = request.headers.get("content-type", "")
        return httpx.Response(201, headers={"Docker-Content-Digest": _digest(request.content)})

    httpx_mock.add_callback(
        _capture_put,
        method="PUT",
        url=re.compile(rf"{registry_url}/v2/{repository}/manifests/{re.escape(image_tag)}"),
    )

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
    assert seen_content_type["value"] == "application/vnd.docker.distribution.manifest.v2+json"


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
        "applicationId: cellseg\nimage: {repository: cytario/x}\n",  # missing tag AND digest
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
