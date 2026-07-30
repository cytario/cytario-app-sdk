"""Tests for cytario_app_sdk.oci.client.RegistryClient."""

from __future__ import annotations

import hashlib
import json
import re

import httpx
import pytest

from cytario_app_sdk.errors import RegistryError
from cytario_app_sdk.oci.client import RegistryClient
from cytario_app_sdk.oci.manifest import MANIFEST_ACCEPT, OCI_MANIFEST_MEDIA_TYPE


def _digest(payload: bytes) -> str:
    return f"sha256:{hashlib.sha256(payload).hexdigest()}"


def test_fetch_manifest_returns_manifest_media_type_and_digest(
    httpx_mock: pytest.FuncFixture,
    registry_url: str,
    repository: str,
    image_tag: str,
    image_manifest: dict,
    image_manifest_digest: str,
    image_manifest_media_type: str,
) -> None:
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
    with RegistryClient(registry=registry_url, user="u", secret="s") as client:
        manifest, media_type, digest = client.fetch_manifest(repository, image_tag)
    assert manifest == image_manifest
    assert media_type == image_manifest_media_type
    assert digest == image_manifest_digest


def test_fetch_manifest_sends_accept_header(
    httpx_mock: pytest.FuncFixture,
    registry_url: str,
    repository: str,
    image_tag: str,
    image_manifest: dict,
    image_manifest_digest: str,
) -> None:
    seen_accept: dict[str, str] = {}

    def _cb(request: httpx.Request) -> httpx.Response:
        seen_accept["value"] = request.headers.get("accept", "")
        return httpx.Response(
            200,
            headers={
                "Docker-Content-Digest": image_manifest_digest,
                "Content-Type": OCI_MANIFEST_MEDIA_TYPE,
            },
            content=json.dumps(image_manifest).encode("utf-8"),
        )

    httpx_mock.add_callback(
        _cb,
        method="GET",
        url=f"{registry_url}/v2/{repository}/manifests/{image_tag}",
    )
    with RegistryClient(registry=registry_url, user="u", secret="s") as client:
        client.fetch_manifest(repository, image_tag)
    assert MANIFEST_ACCEPT in seen_accept["value"]


def test_fetch_manifest_404_raises_registry_error(
    httpx_mock: pytest.FuncFixture,
    registry_url: str,
    repository: str,
    image_tag: str,
) -> None:
    httpx_mock.add_response(
        method="GET",
        url=f"{registry_url}/v2/{repository}/manifests/{image_tag}",
        status_code=404,
        json={"errors": [{"code": "NAME_UNKNOWN", "message": "unknown name"}]},
    )
    with (
        RegistryClient(registry=registry_url, user="u", secret="s") as client,
        pytest.raises(RegistryError) as exc_info,
    ):
        client.fetch_manifest(repository, image_tag)
    assert exc_info.value.status_code == 404


def test_fetch_manifest_missing_digest_header_raises(
    httpx_mock: pytest.FuncFixture,
    registry_url: str,
    repository: str,
    image_tag: str,
    image_manifest: dict,
) -> None:
    httpx_mock.add_response(
        method="GET",
        url=f"{registry_url}/v2/{repository}/manifests/{image_tag}",
        status_code=200,
        headers={"Content-Type": OCI_MANIFEST_MEDIA_TYPE},
        json=image_manifest,
    )
    with (
        RegistryClient(registry=registry_url, user="u", secret="s") as client,
        pytest.raises(RegistryError, match="Docker-Content-Digest"),
    ):
        client.fetch_manifest(repository, image_tag)


def test_fetch_manifest_non_json_body_raises(
    httpx_mock: pytest.FuncFixture,
    registry_url: str,
    repository: str,
    image_tag: str,
    image_manifest_digest: str,
) -> None:
    httpx_mock.add_response(
        method="GET",
        url=f"{registry_url}/v2/{repository}/manifests/{image_tag}",
        status_code=200,
        headers={
            "Docker-Content-Digest": image_manifest_digest,
            "Content-Type": OCI_MANIFEST_MEDIA_TYPE,
        },
        content=b"<<not json>>",
    )
    with (
        RegistryClient(registry=registry_url, user="u", secret="s") as client,
        pytest.raises(RegistryError, match="not valid JSON"),
    ):
        client.fetch_manifest(repository, image_tag)


def test_put_manifest_returns_registry_digest(
    httpx_mock: pytest.FuncFixture,
    registry_url: str,
    repository: str,
    image_tag: str,
) -> None:
    manifest = {
        "schemaVersion": 2,
        "mediaType": OCI_MANIFEST_MEDIA_TYPE,
        "config": {"mediaType": "x", "digest": "sha256:0", "size": 0},
        "layers": [],
        "annotations": {"org.cytario.appdef.v1": "{}"},
    }
    expected_digest = "sha256:registry-reported-digest"

    def _cb(request: httpx.Request) -> httpx.Response:
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
        url=re.compile(rf"{registry_url}/v2/{repository}/manifests/{re.escape(image_tag)}"),
    )
    with RegistryClient(registry=registry_url, user="u", secret="s") as client:
        digest = client.put_manifest(repository, image_tag, manifest, media_type=OCI_MANIFEST_MEDIA_TYPE)
    assert digest == expected_digest


def test_put_manifest_sends_content_type_and_canonical_body(
    httpx_mock: pytest.FuncFixture,
    registry_url: str,
    repository: str,
    image_tag: str,
) -> None:
    manifest = {
        "schemaVersion": 2,
        "mediaType": OCI_MANIFEST_MEDIA_TYPE,
        "config": {"mediaType": "x", "digest": "sha256:0", "size": 0},
        "layers": [],
    }
    seen: dict[str, object] = {}

    def _cb(request: httpx.Request) -> httpx.Response:
        seen["content_type"] = request.headers.get("content-type", "")
        seen["body"] = request.content
        return httpx.Response(201, headers={"Docker-Content-Digest": _digest(request.content)})

    httpx_mock.add_callback(
        _cb,
        method="PUT",
        url=re.compile(rf"{registry_url}/v2/{repository}/manifests/{re.escape(image_tag)}"),
    )
    with RegistryClient(registry=registry_url, user="u", secret="s") as client:
        client.put_manifest(
            repository,
            image_tag,
            manifest,
            media_type="application/vnd.docker.distribution.manifest.v2+json",
        )
    assert seen["content_type"] == "application/vnd.docker.distribution.manifest.v2+json"
    # Body is canonical JSON (sorted keys, no spaces).
    assert seen["body"] == json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode("utf-8")


def test_put_manifest_accepts_200_or_201(
    httpx_mock: pytest.FuncFixture,
    registry_url: str,
    repository: str,
    image_tag: str,
) -> None:
    httpx_mock.add_response(
        method="PUT",
        url=re.compile(rf"{registry_url}/v2/{repository}/manifests/{re.escape(image_tag)}"),
        status_code=200,
        headers={"Docker-Content-Digest": "sha256:ok"},
    )
    with RegistryClient(registry=registry_url, user="u", secret="s") as client:
        digest = client.put_manifest(
            repository, image_tag, {"schemaVersion": 2}, media_type=OCI_MANIFEST_MEDIA_TYPE
        )
    assert digest == "sha256:ok"


def test_put_manifest_falls_back_to_computed_digest_when_header_missing(
    httpx_mock: pytest.FuncFixture,
    registry_url: str,
    repository: str,
    image_tag: str,
) -> None:
    manifest = {"schemaVersion": 2, "mediaType": OCI_MANIFEST_MEDIA_TYPE}
    payload = json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode("utf-8")
    httpx_mock.add_response(
        method="PUT",
        url=re.compile(rf"{registry_url}/v2/{repository}/manifests/{re.escape(image_tag)}"),
        status_code=201,
        # no Docker-Content-Digest header
    )
    with RegistryClient(registry=registry_url, user="u", secret="s") as client:
        digest = client.put_manifest(repository, image_tag, manifest, media_type=OCI_MANIFEST_MEDIA_TYPE)
    assert digest == _digest(payload)


def test_put_manifest_failure_raises(
    httpx_mock: pytest.FuncFixture,
    registry_url: str,
    repository: str,
    image_tag: str,
) -> None:
    httpx_mock.add_response(
        method="PUT",
        url=re.compile(rf"{registry_url}/v2/{repository}/manifests/{re.escape(image_tag)}"),
        status_code=400,
        json={"errors": [{"code": "MANIFEST_INVALID", "message": "bad"}]},
    )
    with (
        RegistryClient(registry=registry_url, user="u", secret="s") as client,
        pytest.raises(RegistryError) as exc_info,
    ):
        client.put_manifest(repository, image_tag, {"schemaVersion": 2}, media_type=OCI_MANIFEST_MEDIA_TYPE)
    assert exc_info.value.status_code == 400


def test_session_cookie_from_fetch_not_replayed_on_put(
    httpx_mock: pytest.FuncFixture,
    registry_url: str,
    repository: str,
    image_tag: str,
    image_manifest: dict,
    image_manifest_digest: str,
) -> None:
    """Harbor issues a ``Set-Cookie: sid=...`` on the GET manifest fetch.

    httpx persists cookies across requests by default; if that session cookie
    is replayed on the follow-up ``PUT /v2/.../manifests/...``, Harbor's CSRF
    middleware stops skipping ``/v2/`` (csrfSkipper keys off CarrySession) and
    the PUT fails with HTTP 403 "CSRF token not found in request". The client
    must discard Set-Cookie so every ``/v2/`` request stays sessionless.
    """
    httpx_mock.add_response(
        method="GET",
        url=f"{registry_url}/v2/{repository}/manifests/{image_tag}",
        status_code=200,
        headers={
            "Docker-Content-Digest": image_manifest_digest,
            "Content-Type": OCI_MANIFEST_MEDIA_TYPE,
            "Set-Cookie": "sid=abc-123; Path=/",
        },
        json=image_manifest,
    )

    cookie_seen: dict[str, str] = {}

    def _assert_no_cookie(request: httpx.Request) -> httpx.Response:
        cookie_seen["value"] = request.headers.get("cookie", "")
        return httpx.Response(201, headers={"Docker-Content-Digest": "sha256:ok"})

    httpx_mock.add_callback(
        _assert_no_cookie,
        method="PUT",
        url=re.compile(rf"{registry_url}/v2/{repository}/manifests/{re.escape(image_tag)}"),
    )

    with RegistryClient(registry=registry_url, user="u", secret="s") as client:
        client.fetch_manifest(repository, image_tag)
        client.put_manifest(repository, image_tag, image_manifest, media_type=OCI_MANIFEST_MEDIA_TYPE)

    assert cookie_seen.get("value", "") == "", (
        f"session cookie replayed on PUT: {cookie_seen['value']!r} — "
        "Harbor CSRF middleware would reject this request"
    )
