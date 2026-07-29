"""End-to-end CLI tests using typer.testing.CliRunner + respx mocks."""

from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import TYPE_CHECKING

import httpx
from typer.testing import CliRunner

from cytario_app_sdk.cli import app
from cytario_app_sdk.oci.manifest import EMPTY_CONFIG_DIGEST, OCI_MANIFEST_MEDIA_TYPE

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


def test_register_pushes_empty_config_blob_before_manifest(
    httpx_mock: pytest.FuncFixture,
    example_app_yaml: Path,
    example_connection_yaml: Path,
    registry_url: str,
    repository: str,
    image_tag: str,
    image_manifest_digest: str,
    image_manifest_size: int,
) -> None:
    """Harbor rejects a manifest whose referenced blobs don't exist
    (MANIFEST_BLOB_UNKNOWN, HTTP 400). The referrer manifest's `config` is the
    OCI empty-config blob (sha256:e3b0c443..., size 0), which Harbor does not
    treat as implicitly present — the SDK must push it before the manifest.
    """
    httpx_mock.add_response(
        method="HEAD",
        url=f"{registry_url}/v2/{repository}/manifests/{image_tag}",
        status_code=200,
        headers={
            "Docker-Content-Digest": image_manifest_digest,
            "Content-Length": str(image_manifest_size),
            "Content-Type": OCI_MANIFEST_MEDIA_TYPE,
        },
    )

    pushed_digests: list[str] = []
    push_order: list[str] = []

    def _blob_cb(request: httpx.Request) -> httpx.Response:
        digest = request.url.params.get("digest")
        assert digest is not None, "blob upload POST missing digest query param"
        pushed_digests.append(digest)
        push_order.append(f"blob:{digest}")
        return httpx.Response(
            201,
            headers={
                "Location": f"{registry_url}/v2/{repository}/blobs/{digest}",
                "Docker-Content-Digest": digest,
            },
        )

    httpx_mock.add_callback(
        _blob_cb,
        method="POST",
        url=re.compile(re.escape(f"{registry_url}/v2/{repository}/blobs/uploads/") + r"(\?.*)?$"),
        is_reusable=True,
    )

    def _manifest_cb(request: httpx.Request) -> httpx.Response:
        push_order.append("manifest")
        digest = _digest(request.content)
        return httpx.Response(
            201,
            headers={
                "Location": f"{registry_url}/v2/{repository}/manifests/{digest}",
                "Docker-Content-Digest": digest,
            },
        )

    httpx_mock.add_callback(
        _manifest_cb,
        method="PUT",
        url=re.compile(rf"{registry_url}/v2/{repository}/manifests/sha256:[a-f0-9]+"),
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

    # The empty config blob must be pushed.
    assert EMPTY_CONFIG_DIGEST in pushed_digests, (
        f"empty config blob {EMPTY_CONFIG_DIGEST} was not pushed; pushed digests: {pushed_digests}"
    )
    # And it must be pushed BEFORE the manifest (Harbor checks blobs at
    # manifest-push time, so pushing it after would still 400).
    empty_blob_idx = push_order.index(f"blob:{EMPTY_CONFIG_DIGEST}")
    manifest_idx = push_order.index("manifest")
    assert empty_blob_idx < manifest_idx, f"empty config blob pushed after manifest: order={push_order}"


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
