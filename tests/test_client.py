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


def test_push_blob_put_preserves_state_param_from_location(
    httpx_mock: pytest.FuncFixture,
    registry_url: str,
    repository: str,
) -> None:
    """Harbor (docker/distribution) returns 202 with a `Location` carrying an
    HMAC upload-state token in `?_state=<...>` that the closing PUT must echo
    back. `blobUploadDispatcher` rejects a missing `_state` as
    BLOB_UPLOAD_INVALID (HTTP 404). Passing `params={"digest": ...}` to
    httpx.Client.put would clobber the Location's whole query string (httpx
    builds `URL(url, params=params)`, which replaces `query`), dropping
    `_state`. The client must merge `digest` into the Location's existing
    query instead.
    """
    payload = b'{"name":"cellseg"}'
    digest = _digest(payload)
    state_token = "hmac-signed-state-token-abc123"
    upload_url = f"{registry_url}/v2/{repository}/blobs/uploads/uuid-123?_state={state_token}"

    httpx_mock.add_response(
        method="POST",
        url=re.compile(re.escape(f"{registry_url}/v2/{repository}/blobs/uploads/") + r"(\?.*)?$"),
        status_code=202,
        headers={"Location": upload_url},
    )

    seen_put_urls: list[str] = []

    def _assert_state_preserved(request: httpx.Request) -> httpx.Response:
        seen_put_urls.append(str(request.url))
        assert request.url.params.get("_state") == state_token, (
            f"_state dropped from PUT URL: {request.url} — "
            "distribution would reject this with BLOB_UPLOAD_INVALID"
        )
        assert request.url.params.get("digest") == digest, f"digest missing from PUT URL: {request.url}"
        return httpx.Response(
            201,
            headers={
                "Location": f"{registry_url}/v2/{repository}/blobs/{digest}",
                "Docker-Content-Digest": digest,
            },
        )

    httpx_mock.add_callback(
        _assert_state_preserved,
        method="PUT",
        url=re.compile(re.escape(f"{registry_url}/v2/{repository}/blobs/uploads/uuid-123") + r".*"),
    )

    with RegistryClient(registry=registry_url, user="u", secret="s") as client:
        client.push_blob(repository, payload)

    assert seen_put_urls, "PUT was never issued"
    put_url = seen_put_urls[0]
    assert f"_state={state_token}" in put_url, f"_state missing from: {put_url}"
    assert "digest=" in put_url, f"digest missing from: {put_url}"


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


def test_session_cookie_from_resolve_subject_not_replayed_on_push_blob(
    httpx_mock: pytest.FuncFixture,
    registry_url: str,
    repository: str,
    image_tag: str,
    image_manifest_digest: str,
    image_manifest_size: int,
) -> None:
    """Harbor issues a `Set-Cookie: sid=...` on the HEAD subject-resolve call.

    httpx persists cookies across requests by default; if that session cookie
    is replayed on the follow-up `POST /v2/.../blobs/uploads/`, Harbor's CSRF
    middleware stops skipping `/v2/` (csrfSkipper keys off CarrySession) and
    the POST fails with HTTP 403 "CSRF token not found in request". The
    client must discard Set-Cookie so every `/v2/` request stays sessionless.
    """
    httpx_mock.add_response(
        method="HEAD",
        url=f"{registry_url}/v2/{repository}/manifests/{image_tag}",
        status_code=200,
        headers={
            "Docker-Content-Digest": image_manifest_digest,
            "Content-Length": str(image_manifest_size),
            "Content-Type": OCI_MANIFEST_MEDIA_TYPE,
            "Set-Cookie": "sid=abc-123; Path=/",
        },
    )

    cookie_seen: dict[str, str] = {}

    def _assert_no_cookie(request: httpx.Request) -> httpx.Response:
        cookie_seen["value"] = request.headers.get("cookie", "")
        assert "sid" not in cookie_seen["value"], (
            f"session cookie replayed on POST: {cookie_seen['value']!r} — "
            "Harbor CSRF middleware would reject this request"
        )
        digest = _digest(b"x")
        return httpx.Response(
            201,
            headers={
                "Location": f"{registry_url}/v2/{repository}/blobs/{digest}",
                "Docker-Content-Digest": digest,
            },
        )

    httpx_mock.add_callback(
        _assert_no_cookie,
        method="POST",
        url=re.compile(re.escape(f"{registry_url}/v2/{repository}/blobs/uploads/") + r"(\?.*)?$"),
    )

    with RegistryClient(registry=registry_url, user="u", secret="s") as client:
        client.resolve_subject(repository, image_tag)
        client.push_blob(repository, b"x")

    assert cookie_seen.get("value", "") == "", f"unexpected Cookie header on POST: {cookie_seen['value']!r}"


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
