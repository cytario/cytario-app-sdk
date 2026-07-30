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
        is_reusable=True,
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
        is_reusable=True,
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


# ---------------------------------------------------------------------------
# Fixtures for the `apps` discovery scan
# ---------------------------------------------------------------------------


@pytest.fixture
def app_definition_blob(example_app_bytes: bytes) -> bytes:
    """The canonical JSON form of the example app-definition, as it would be
    stored as a registry blob. Mirrors cli.py's serialization: sort_keys + tight
    separators so the digest is stable.
    """
    import json  # noqa: PLC0415

    import yaml  # noqa: PLC0415

    from cytario_app_sdk.models import AppDefinition  # noqa: PLC0415

    raw = yaml.safe_load(example_app_bytes.decode("utf-8"))
    definition = AppDefinition.model_validate(raw)
    doc = definition.definition_document
    return json.dumps(doc, sort_keys=True, separators=(",", ":")).encode("utf-8")


@pytest.fixture
def app_definition_blob_digest(app_definition_blob: bytes) -> str:
    return _digest(app_definition_blob)


@pytest.fixture
def referrer_manifest_digest() -> str:
    return "sha256:" + "b" * 64


@pytest.fixture
def referrer_manifest(
    app_definition_blob_digest: str,
    app_definition_blob: bytes,
    image_manifest_digest: str,
    image_manifest_size: int,
) -> dict[str, Any]:
    """A referrer image manifest pointing at the app-definition blob."""
    from cytario_app_sdk.oci.manifest import (  # noqa: PLC0415
        EMPTY_CONFIG_BYTES,
        EMPTY_CONFIG_DIGEST,
        EMPTY_CONFIG_MEDIA_TYPE,
        EMPTY_CONFIG_SIZE,
        OCI_MANIFEST_MEDIA_TYPE,
    )

    empty_digest = f"sha256:{hashlib.sha256(EMPTY_CONFIG_BYTES).hexdigest()}"
    assert empty_digest == EMPTY_CONFIG_DIGEST  # sanity
    return {
        "schemaVersion": 2,
        "mediaType": OCI_MANIFEST_MEDIA_TYPE,
        "artifactType": "application/vnd.cytario.app-definition.v1+json",
        "config": {
            "mediaType": EMPTY_CONFIG_MEDIA_TYPE,
            "digest": EMPTY_CONFIG_DIGEST,
            "size": EMPTY_CONFIG_SIZE,
        },
        "layers": [
            {
                "mediaType": "application/vnd.cytario.app-definition.v1+json",
                "digest": app_definition_blob_digest,
                "size": len(app_definition_blob),
            }
        ],
        "subject": {
            "mediaType": OCI_MANIFEST_MEDIA_TYPE,
            "digest": image_manifest_digest,
            "size": image_manifest_size,
        },
    }


@pytest.fixture
def mock_catalog(
    httpx_mock: pytest.FuncFixture,
    registry_url: str,
) -> None:
    """Mock GET /v2/_catalog returning one repository (matches the discovery mocks)."""
    httpx_mock.add_response(
        method="GET",
        url=f"{registry_url}/v2/_catalog?n=100",
        status_code=200,
        json={"repositories": ["cytario/cellseg"]},
        is_reusable=True,
    )


@pytest.fixture
def mock_list_tags(
    httpx_mock: pytest.FuncFixture,
    registry_url: str,
    repository: str,
    image_tag: str,
) -> None:
    """Mock GET /v2/<repo>/tags/list."""
    httpx_mock.add_response(
        method="GET",
        url=f"{registry_url}/v2/{repository}/tags/list",
        status_code=200,
        json={"name": repository, "tags": [image_tag]},
        is_reusable=True,
    )


@pytest.fixture
def mock_list_referrers(
    httpx_mock: pytest.FuncFixture,
    registry_url: str,
    repository: str,
    image_manifest_digest: str,
    referrer_manifest_digest: str,
) -> None:
    """Mock GET /v2/<repo>/referrers/<digest> returning one app-definition referrer."""
    httpx_mock.add_response(
        method="GET",
        url=f"{registry_url}/v2/{repository}/referrers/{image_manifest_digest}",
        status_code=200,
        json={
            "schemaVersion": 2,
            "mediaType": "application/vnd.oci.image.index.v1+json",
            "manifests": [
                {
                    "mediaType": "application/vnd.oci.image.manifest.v1+json",
                    "digest": referrer_manifest_digest,
                    "size": 512,
                    "artifactType": "application/vnd.cytario.app-definition.v1+json",
                }
            ],
        },
        is_reusable=True,
    )


@pytest.fixture
def mock_pull_referrer_manifest(
    httpx_mock: pytest.FuncFixture,
    registry_url: str,
    repository: str,
    referrer_manifest_digest: str,
    referrer_manifest: dict[str, Any],
) -> None:
    """Mock GET /v2/<repo>/manifests/<referrer-digest>."""
    httpx_mock.add_response(
        method="GET",
        url=f"{registry_url}/v2/{repository}/manifests/{referrer_manifest_digest}",
        status_code=200,
        json=referrer_manifest,
        is_reusable=True,
    )


@pytest.fixture
def mock_pull_app_definition_blob(
    httpx_mock: pytest.FuncFixture,
    registry_url: str,
    repository: str,
    app_definition_blob_digest: str,
    app_definition_blob: bytes,
) -> None:
    """Mock GET /v2/<repo>/blobs/<digest> returning the app-definition JSON."""
    httpx_mock.add_response(
        method="GET",
        url=f"{registry_url}/v2/{repository}/blobs/{app_definition_blob_digest}",
        status_code=200,
        content=app_definition_blob,
        is_reusable=True,
    )


@pytest.fixture
def full_discovery_mock(
    mock_resolve_subject: None,
    mock_list_tags: None,
    mock_list_referrers: None,
    mock_pull_referrer_manifest: None,
    mock_pull_app_definition_blob: None,
) -> None:
    """All read-side endpoints mocked for an end-to-end `apps` scan of one repo."""


@pytest.fixture
def full_catalog_discovery_mock(
    mock_catalog: None,
    full_discovery_mock: None,
) -> None:
    """Catalog + per-repo discovery mocks for a full `apps` scan."""
