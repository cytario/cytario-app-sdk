"""Minimal OCI Distribution v1.1 client over httpx.

Implements only the two operations the ``register`` command needs to attach
the app-definition as an OCI Image Format annotation on the image manifest:

  * ``fetch_manifest`` — ``GET /v2/<name>/manifests/<ref>`` to retrieve the
    current image manifest (its media type + digest come from the response
    headers).
  * ``put_manifest``   — ``PUT /v2/<name>/manifests/<ref>`` to store the
    annotated manifest back under the original tag. The new content digest
    (returned by the registry) pins both the image and the definition.

No Harbor-specific APIs are used. The endpoint set is the OCI Distribution
v1.1 spec: https://github.com/opencontainers/distribution-spec/blob/main/spec.md
"""

from __future__ import annotations

import hashlib
import http.cookiejar
import json
from typing import Any

import httpx
from typing_extensions import Self

from cytario_app_sdk.errors import RegistryError
from cytario_app_sdk.oci.manifest import MANIFEST_ACCEPT, OCI_MANIFEST_MEDIA_TYPE

DEFAULT_TIMEOUT = httpx.Timeout(30.0, read=120.0)


def _canonical_json(payload: dict[str, Any]) -> bytes:
    """Serialize ``payload`` as deterministic UTF-8 JSON (sorted keys, no spaces).

    Deterministic serialization makes the manifest digest stable across runs,
    which lets the SDK (and the registry) treat a re-push as a no-op when the
    content is unchanged.
    """
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")


class _NoSessionCookieJar(http.cookiejar.CookieJar):
    """Cookie jar that silently discards ``Set-Cookie`` from responses.

    The OCI Distribution client authenticates with HTTP Basic (the
    ``Authorization`` header), so it has no use for cookies. Persisting the
    registry's session cookie is actively harmful on Harbor: Harbor's CSRF
    middleware skips ``/v2/`` paths only when the request does NOT carry a
    session (see ``csrfSkipper`` in ``src/server/middleware/csrf/csrf.go``).
    httpx's ``Client`` persists cookies across requests by default, so a
    ``Set-Cookie: sid=...`` issued on an earlier ``GET /v2/.../manifests/...``
    would be replayed on the follow-up ``PUT /v2/.../manifests/...``, flipping
    ``CarrySession`` to true and making the PUT subject to CSRF enforcement —
    it fails with HTTP 403 ``{"errors":[{"code":"FORBIDDEN","message":"CSRF
    token not found in request"}]}``. Discarding ``Set-Cookie`` keeps every
    ``/v2/`` request sessionless so Harbor's CSRF skipper applies.
    """

    def extract_cookies(self, response: object, request: object) -> None:  # noqa: ARG002
        return None


class RegistryClient:
    """A synchronous OCI Distribution v1.1 client."""

    def __init__(
        self,
        *,
        registry: str,
        user: str,
        secret: str,
        timeout: httpx.Timeout | None = None,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        """Create a client authenticated to ``registry`` via HTTP Basic."""
        self._base = registry.rstrip("/")
        self._auth = httpx.BasicAuth(user, secret)
        self._timeout = timeout or DEFAULT_TIMEOUT
        self._client = httpx.Client(
            base_url=self._base,
            auth=self._auth,
            timeout=self._timeout,
            transport=transport,
            headers={"Accept": MANIFEST_ACCEPT},
            cookies=_NoSessionCookieJar(),
        )

    def close(self) -> None:
        """Close the underlying HTTP client."""
        self._client.close()

    def __enter__(self) -> Self:
        """Enter context manager, returning self."""
        return self

    def __exit__(self, *exc: object) -> None:
        """Close the client on context exit."""
        self.close()

    # ------------------------------------------------------------------
    # OCI Distribution endpoints
    # ------------------------------------------------------------------

    def fetch_manifest(
        self,
        repository: str,
        ref: str,
    ) -> tuple[dict[str, Any], str, str]:
        """``GET /v2/<name>/manifests/<ref>`` → ``(manifest, media_type, digest)``.

        ``ref`` is a tag or digest. Returns the parsed manifest, the manifest's
        media type (from ``Content-Type``), and its content digest (from
        ``Docker-Content-Digest``). The media type is echoed back on the
        subsequent ``put_manifest`` so the registry stores the annotated manifest
        as the same kind.
        """
        url = f"/v2/{repository}/manifests/{ref}"
        resp = self._client.get(url, headers={"Accept": MANIFEST_ACCEPT})
        if resp.status_code != 200:
            raise RegistryError(
                f"could not fetch manifest {repository}@{ref}",
                status_code=resp.status_code,
                body=resp.text,
            )
        try:
            manifest = resp.json()
        except json.JSONDecodeError as exc:
            msg = f"manifest response for {repository}@{ref} was not valid JSON"
            raise RegistryError(msg, status_code=resp.status_code, body=resp.text) from exc
        media_type = resp.headers.get("Content-Type", OCI_MANIFEST_MEDIA_TYPE)
        digest = resp.headers.get("Docker-Content-Digest", "")
        if not digest:
            msg = f"registry response missing Docker-Content-Digest for {repository}@{ref}"
            raise RegistryError(msg, status_code=resp.status_code, body=resp.text)
        return manifest, media_type, digest

    def put_manifest(
        self,
        repository: str,
        ref: str,
        manifest: dict[str, Any],
        *,
        media_type: str,
    ) -> str:
        """``PUT /v2/<name>/manifests/<ref>`` → the new manifest digest.

        Stores ``manifest`` under ``ref`` (the original tag) with the given
        ``media_type`` as ``Content-Type`` so the registry treats it as the same
        kind of manifest that was fetched. Returns the new content digest the
        registry reports (from ``Docker-Content-Digest``); because the
        app-definition annotation is now part of the manifest content, this
        digest pins both the image and the definition.
        """
        payload = _canonical_json(manifest)
        url = f"/v2/{repository}/manifests/{ref}"
        resp = self._client.put(
            url,
            headers={"Content-Type": media_type},
            content=payload,
        )
        if resp.status_code not in (200, 201):
            raise RegistryError(
                f"manifest push failed for {repository}@{ref}",
                status_code=resp.status_code,
                body=resp.text,
            )
        digest = resp.headers.get("Docker-Content-Digest")
        if not digest:
            # Compute the digest ourselves so callers always get one.
            digest = f"sha256:{hashlib.sha256(payload).hexdigest()}"
        return digest


__all__ = ["RegistryClient"]
