"""Minimal OCI Distribution v1.1 client over httpx.

Implements only the three operations the `register` command needs:

  * `resolve_subject`   — HEAD `/v2/<name>/manifests/<ref>` to get the
    container image manifest descriptor (digest + size + mediaType) for the
    `subject` field of the referrer.
  * `push_blob`         — monolithic blob upload: POST to start a session,
    then PUT the bytes with the digest. Falls back to a single-POST upload
    when the registry supports it.
  * `push_manifest`     — PUT `/v2/<name>/manifests/<digest>` (referrer
    manifests are pushed by digest; the registry indexes them under the
    referrers list for `subject.digest`).

No Harbor-specific APIs are used. The endpoint set is the OCI Distribution
v1.1 spec: https://github.com/opencontainers/distribution-spec/blob/main/spec.md
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

import httpx
from typing_extensions import Self

from cytario_app_sdk.errors import RegistryError
from cytario_app_sdk.oci.manifest import MANIFEST_ACCEPT, OCI_MANIFEST_MEDIA_TYPE

DEFAULT_TIMEOUT = httpx.Timeout(30.0, read=120.0)


def _sha256_digest(payload: bytes) -> str:
    """Compute the `sha256:<hex>` digest of `payload` (OCI digest format)."""
    return f"sha256:{hashlib.sha256(payload).hexdigest()}"


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
        """Create a client authenticated to `registry` via HTTP Basic."""
        self._base = registry.rstrip("/")
        self._auth = httpx.BasicAuth(user, secret)
        self._timeout = timeout or DEFAULT_TIMEOUT
        self._client = httpx.Client(
            base_url=self._base,
            auth=self._auth,
            timeout=self._timeout,
            transport=transport,
            headers={"Accept": MANIFEST_ACCEPT},
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

    def resolve_subject(self, repository: str, ref: str) -> dict[str, Any]:
        """HEAD `/v2/<name>/manifests/<ref>` → the image manifest descriptor.

        `ref` is a tag or digest. Returns `{mediaType, digest, size}` — the
        shape required for the `subject` field of a referrer manifest.
        """
        url = f"/v2/{repository}/manifests/{ref}"
        resp = self._client.head(url, headers={"Accept": MANIFEST_ACCEPT})
        if resp.status_code != 200:
            raise RegistryError(
                f"could not resolve subject manifest {repository}@{ref}",
                status_code=resp.status_code,
                body=resp.text,
            )
        try:
            digest = resp.headers["Docker-Content-Digest"]
            size = int(resp.headers["Content-Length"])
            media_type = resp.headers.get("Content-Type", OCI_MANIFEST_MEDIA_TYPE)
        except KeyError as exc:
            missing = exc.args[0]
            msg = f"registry response missing required header {missing} for {repository}@{ref}"
            raise RegistryError(msg, status_code=resp.status_code, body=resp.text) from exc
        return {"mediaType": media_type, "digest": digest, "size": size}

    def push_blob(self, repository: str, payload: bytes) -> dict[str, Any]:
        """Monolithic blob upload. Returns the blob descriptor.

        Tries the single-POST form first (POST `/v2/<name>/blobs/uploads/?digest=...`).
        If the registry returns 202 (session started), falls back to POST+PUT.
        """
        digest = _sha256_digest(payload)
        # Try single-POST upload first.
        resp = self._client.post(
            f"/v2/{repository}/blobs/uploads/",
            params={"digest": digest},
            headers={"Content-Type": "application/octet-stream", "Content-Length": str(len(payload))},
            content=payload,
        )
        if resp.status_code == 201:
            return {"mediaType": "application/octet-stream", "digest": digest, "size": len(payload)}
        if resp.status_code != 202:
            raise RegistryError(
                f"blob upload start failed for {repository}",
                status_code=resp.status_code,
                body=resp.text,
            )

        # Fall back to POST + PUT. The 202 gave us a Location to PUT to.
        location = resp.headers.get("Location")
        if not location:
            msg = f"registry returned 202 without a Location header for {repository}"
            raise RegistryError(msg, status_code=resp.status_code, body=resp.text)

        put_resp = self._client.put(
            location,
            params={"digest": digest},
            headers={"Content-Type": "application/octet-stream", "Content-Length": str(len(payload))},
            content=payload,
        )
        if put_resp.status_code != 201:
            raise RegistryError(
                f"blob upload PUT failed for {repository}",
                status_code=put_resp.status_code,
                body=put_resp.text,
            )
        return {"mediaType": "application/octet-stream", "digest": digest, "size": len(payload)}

    def push_manifest(
        self,
        repository: str,
        manifest: dict[str, Any],
        *,
        reference: str | None = None,
    ) -> str:
        """PUT `/v2/<name>/manifests/<ref>`. Returns the manifest digest.

        If `reference` is None, the manifest is pushed by its own digest (the
        referrer pattern — the registry indexes it under the subject's
        referrers list).
        """
        payload = _canonical_json(manifest)
        digest = _sha256_digest(payload)
        ref = reference or digest
        resp = self._client.put(
            f"/v2/{repository}/manifests/{ref}",
            headers={"Content-Type": OCI_MANIFEST_MEDIA_TYPE},
            content=payload,
        )
        if resp.status_code != 201:
            raise RegistryError(
                f"manifest push failed for {repository}@{ref}",
                status_code=resp.status_code,
                body=resp.text,
            )
        return resp.headers.get("Docker-Content-Digest", digest)


def _canonical_json(payload: dict[str, Any]) -> bytes:
    """Serialize `payload` as deterministic UTF-8 JSON (sorted keys, no spaces).

    Deterministic serialization makes the manifest digest stable across runs,
    which lets the SDK (and the registry) treat a re-push as a no-op.
    """
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")


__all__ = ["RegistryClient"]
