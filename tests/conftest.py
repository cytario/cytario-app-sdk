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
def image_manifest_size() -> int:
    return 4096


@pytest.fixture
def subject_descriptor(image_manifest_digest: str, image_manifest_size: int) -> dict[str, Any]:
    return {
        "mediaType": OCI_MANIFEST_MEDIA_TYPE,
        "digest": image_manifest_digest,
        "size": image_manifest_size,
    }


def _digest(payload: bytes) -> str:
    return f"sha256:{hashlib.sha256(payload).hexdigest()}"


@pytest.fixture
def mock_resolve_subject(
    httpx_mock: pytest.FuncFixture,
    registry_url: str,
    repository: str,
    image_tag: str,
    image_manifest_digest: str,
    image_manifest_size: int,
) -> None:
    """Mock HEAD /v2/<repo>/manifests/<tag> for subject resolution."""
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


@pytest.fixture
def mock_push_blob_single_post(
    httpx_mock: pytest.FuncFixture,
    registry_url: str,
    repository: str,
) -> None:
    """Mock the single-POST blob upload path (registry returns 201)."""

    def _cb(request: httpx.Request) -> httpx.Response:
        digest = request.url.params.get("digest")
        assert digest is not None
        assert request.content is not None
        actual = f"sha256:{hashlib.sha256(request.content).hexdigest()}"
        assert actual == digest, f"declared digest {digest} != actual {actual}"
        return httpx.Response(
            201,
            headers={
                "Location": f"{registry_url}/v2/{repository}/blobs/{digest}",
                "Docker-Content-Digest": digest,
            },
        )

    httpx_mock.add_callback(
        _cb,
        method="POST",
        url=re.compile(re.escape(f"{registry_url}/v2/{repository}/blobs/uploads/") + r"(\?.*)?$"),
    )


@pytest.fixture
def mock_push_manifest(
    httpx_mock: pytest.FuncFixture,
    registry_url: str,
    repository: str,
) -> None:
    """Mock PUT /v2/<repo>/manifests/<digest>. Returns the computed digest."""

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
        url=re.compile(rf"{registry_url}/v2/{repository}/manifests/sha256:[a-f0-9]+"),
    )


@pytest.fixture
def full_registry_mock(
    mock_resolve_subject: None,
    mock_push_blob_single_post: None,
    mock_push_manifest: None,
) -> None:
    """All three OCI endpoints mocked for an end-to-end `register`."""
