"""Tests for cytario_app_sdk.oci.client.RegistryClient."""

from __future__ import annotations

import hashlib
import json
import re

import httpx
import pytest

from cytario_app_sdk.errors import RegistryError
from cytario_app_sdk.oci.client import RegistryClient
from cytario_app_sdk.oci.manifest import OCI_MANIFEST_MEDIA_TYPE


def _digest(payload: bytes) -> str:
    return f"sha256:{hashlib.sha256(payload).hexdigest()}"


def test_resolve_subject_returns_descriptor(
    httpx_mock: pytest.FuncFixture,
    registry_url: str,
    repository: str,
    image_tag: str,
    image_manifest_digest: str,
    image_manifest_size: int,
) -> None:
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
    with RegistryClient(registry=registry_url, user="u", secret="s") as client:
        descriptor = client.resolve_subject(repository, image_tag)
    assert descriptor == {
        "mediaType": OCI_MANIFEST_MEDIA_TYPE,
        "digest": image_manifest_digest,
        "size": image_manifest_size,
    }


def test_resolve_subject_404_raises_registry_error(
    httpx_mock: pytest.FuncFixture,
    registry_url: str,
    repository: str,
    image_tag: str,
) -> None:
    httpx_mock.add_response(
        method="HEAD",
        url=f"{registry_url}/v2/{repository}/manifests/{image_tag}",
        status_code=404,
        json={"errors": [{"code": "NAME_UNKNOWN", "message": "unknown name"}]},
    )
    with (
        RegistryClient(registry=registry_url, user="u", secret="s") as client,
        pytest.raises(RegistryError) as exc_info,
    ):
        client.resolve_subject(repository, image_tag)
    assert exc_info.value.status_code == 404


def test_resolve_subject_missing_digest_header_raises(
    httpx_mock: pytest.FuncFixture,
    registry_url: str,
    repository: str,
    image_tag: str,
) -> None:
    httpx_mock.add_response(
        method="HEAD",
        url=f"{registry_url}/v2/{repository}/manifests/{image_tag}",
        status_code=200,
        headers={"Content-Length": "10"},  # no Docker-Content-Digest
    )
    with (
        RegistryClient(registry=registry_url, user="u", secret="s") as client,
        pytest.raises(RegistryError, match="Docker-Content-Digest"),
    ):
        client.resolve_subject(repository, image_tag)


def test_push_blob_single_post(
    httpx_mock: pytest.FuncFixture,
    registry_url: str,
    repository: str,
) -> None:
    payload = b'{"name":"cellseg"}'

    def _cb(request: httpx.Request) -> httpx.Response:
        assert request.url.params["digest"] == _digest(payload)
        assert request.content == payload
        return httpx.Response(
            201,
            headers={
                "Location": f"{registry_url}/v2/{repository}/blobs/{_digest(payload)}",
                "Docker-Content-Digest": _digest(payload),
            },
        )

    httpx_mock.add_callback(
        _cb,
        method="POST",
        url=re.compile(re.escape(f"{registry_url}/v2/{repository}/blobs/uploads/") + r"(\?.*)?$"),
    )
    with RegistryClient(registry=registry_url, user="u", secret="s") as client:
        descriptor = client.push_blob(repository, payload)
    assert descriptor == {
        "mediaType": "application/octet-stream",
        "digest": _digest(payload),
        "size": len(payload),
    }


def test_push_blob_falls_back_to_post_then_put(
    httpx_mock: pytest.FuncFixture,
    registry_url: str,
    repository: str,
) -> None:
    payload = b'{"name":"cellseg"}'
    digest = _digest(payload)
    upload_url = f"{registry_url}/v2/{repository}/blobs/uploads/uuid-123"

    # First POST returns 202 with a Location.
    httpx_mock.add_response(
        method="POST",
        url=re.compile(re.escape(f"{registry_url}/v2/{repository}/blobs/uploads/") + r"(\?.*)?$"),
        status_code=202,
        headers={"Location": upload_url},
    )
    # Then PUT to the Location closes the upload.
    httpx_mock.add_response(
        method="PUT",
        url=re.compile(re.escape(upload_url) + r"\?.*"),
        status_code=201,
        headers={
            "Location": f"{registry_url}/v2/{repository}/blobs/{digest}",
            "Docker-Content-Digest": digest,
        },
    )
    with RegistryClient(registry=registry_url, user="u", secret="s") as client:
        descriptor = client.push_blob(repository, payload)
    assert descriptor["digest"] == digest
    assert descriptor["size"] == len(payload)


def test_push_blob_202_without_location_raises(
    httpx_mock: pytest.FuncFixture,
    registry_url: str,
    repository: str,
) -> None:
    payload = b"x"
    httpx_mock.add_response(
        method="POST",
        url=re.compile(re.escape(f"{registry_url}/v2/{repository}/blobs/uploads/") + r"(\?.*)?$"),
        status_code=202,
        # no Location header
    )
    with (
        RegistryClient(registry=registry_url, user="u", secret="s") as client,
        pytest.raises(RegistryError, match="Location"),
    ):
        client.push_blob(repository, payload)


def test_push_blob_500_raises(
    httpx_mock: pytest.FuncFixture,
    registry_url: str,
    repository: str,
) -> None:
    httpx_mock.add_response(
        method="POST",
        url=re.compile(re.escape(f"{registry_url}/v2/{repository}/blobs/uploads/") + r"(\?.*)?$"),
        status_code=500,
        text="internal error",
    )
    with (
        RegistryClient(registry=registry_url, user="u", secret="s") as client,
        pytest.raises(RegistryError) as exc_info,
    ):
        client.push_blob(repository, b"x")
    assert exc_info.value.status_code == 500


def test_push_manifest_by_digest(
    httpx_mock: pytest.FuncFixture,
    registry_url: str,
    repository: str,
) -> None:
    manifest = {
        "schemaVersion": 2,
        "mediaType": OCI_MANIFEST_MEDIA_TYPE,
        "artifactType": "application/vnd.cytario.app-definition.v1+json",
        "config": {"mediaType": "x", "digest": "sha256:0", "size": 0},
        "layers": [],
        "subject": {"mediaType": OCI_MANIFEST_MEDIA_TYPE, "digest": "sha256:subj", "size": 1},
    }
    payload = json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode("utf-8")
    expected_digest = _digest(payload)

    def _cb(request: httpx.Request) -> httpx.Response:
        assert request.content == payload
        return httpx.Response(
            201,
            headers={
                "Location": f"{registry_url}/v2/{repository}/manifests/{expected_digest}",
                "Docker-Content-Digest": expected_digest,
            },
        )

    httpx_mock.add_callback(
        _cb,
        method="PUT",
        url=re.compile(rf"{registry_url}/v2/{repository}/manifests/sha256:[a-f0-9]+"),
    )
    with RegistryClient(registry=registry_url, user="u", secret="s") as client:
        returned_digest = client.push_manifest(repository, manifest)
    assert returned_digest == expected_digest


def test_push_manifest_failure_raises(
    httpx_mock: pytest.FuncFixture,
    registry_url: str,
    repository: str,
) -> None:
    httpx_mock.add_response(
        method="PUT",
        url=re.compile(rf"{registry_url}/v2/{repository}/manifests/sha256:[a-f0-9]+"),
        status_code=400,
        json={"errors": [{"code": "MANIFEST_INVALID", "message": "bad"}]},
    )
    with (
        RegistryClient(registry=registry_url, user="u", secret="s") as client,
        pytest.raises(RegistryError) as exc_info,
    ):
        client.push_manifest(repository, {"schemaVersion": 2})
    assert exc_info.value.status_code == 400
