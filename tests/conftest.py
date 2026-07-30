"""Shared pytest fixtures."""

from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Any

import httpx
import pytest

from cytario_app_sdk.oci.manifest import OCI_MANIFEST_MEDIA_TYPE

EXAMPLES_DIR = Path(__file__).resolve().parent.parent / "examples"
EXAMPLE_APP = EXAMPLES_DIR / "cellseg.yaml"
EXAMPLE_CONNECTION = EXAMPLES_DIR / "connection.yaml"


@pytest.fixture
def example_app_yaml() -> Path:
    return EXAMPLE_APP


@pytest.fixture
def example_connection_yaml() -> Path:
    return EXAMPLE_CONNECTION


@pytest.fixture
def example_app_bytes() -> bytes:
    return EXAMPLE_APP.read_bytes()


@pytest.fixture
def registry_url() -> str:
    return "https://harbor.example.com"


@pytest.fixture
def repository() -> str:
    return "cytario/cellseg"


@pytest.fixture
def image_tag() -> str:
    return "1.0.0"


@pytest.fixture
def image_manifest_digest() -> str:
    return "sha256:" + "a" * 64


@pytest.fixture
def image_manifest_media_type() -> str:
    return OCI_MANIFEST_MEDIA_TYPE


@pytest.fixture
def image_manifest(
    image_manifest_digest: str,
    image_manifest_media_type: str,
) -> dict[str, Any]:
    """A minimal OCI image manifest returned by ``GET /v2/<name>/manifests/<ref>``."""
    return {
        "schemaVersion": 2,
        "mediaType": image_manifest_media_type,
        "config": {
            "mediaType": "application/vnd.oci.image.config.v1+json",
            "digest": "sha256:cfg",
            "size": 1,
        },
        "layers": [
            {
                "mediaType": "application/vnd.oci.image.layer.v1.tar+gzip",
                "digest": "sha256:lyr",
                "size": 1,
            }
        ],
        "annotations": {"org.opencontainers.image.created": "2026-01-01T00:00:00Z"},
    }


def _digest(payload: bytes) -> str:
    return f"sha256:{hashlib.sha256(payload).hexdigest()}"


@pytest.fixture
def mock_fetch_manifest(
    httpx_mock: pytest.FuncFixture,
    registry_url: str,
    repository: str,
    image_tag: str,
    image_manifest: dict[str, Any],
    image_manifest_digest: str,
    image_manifest_media_type: str,
) -> None:
    """Mock GET /v2/<repo>/manifests/<tag> for manifest fetching."""
    httpx_mock.add_response(
        method="GET",
        url=f"{registry_url}/v2/{repository}/manifests/{image_tag}",
        status_code=200,
        headers={
            "Docker-Content-Digest": image_manifest_digest,
            "Content-Type": image_manifest_media_type,
        },
        json=image_manifest,
    )


@pytest.fixture
def mock_put_manifest(
    httpx_mock: pytest.FuncFixture,
    registry_url: str,
    repository: str,
    image_tag: str,
) -> None:
    """Mock PUT /v2/<repo>/manifests/<tag>. Returns the computed digest."""

    def _cb(request: httpx.Request) -> httpx.Response:
        assert request.content is not None
        digest = _digest(request.content)
        return httpx.Response(
            201,
            headers={
                "Location": f"{registry_url}/v2/{repository}/manifests/{digest}",
                "Docker-Content-Digest": digest,
            },
        )

    httpx_mock.add_callback(
        _cb,
        method="PUT",
        url=re.compile(rf"{registry_url}/v2/{repository}/manifests/{re.escape(image_tag)}"),
    )


@pytest.fixture
def full_registry_mock(
    mock_fetch_manifest: None,
    mock_put_manifest: None,
) -> None:
    """Both OCI endpoints mocked for an end-to-end `register`."""
