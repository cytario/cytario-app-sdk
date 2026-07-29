"""Build the OCI image manifest for an app-definition referrer artifact.

Per OCI Distribution v1.1, a referrer is an image manifest with:
  - `artifactType` set to the app-definition media type
  - `subject` pointing at the container image manifest descriptor
  - a config descriptor (the OCI empty config) and a single layer holding the
    app-definition JSON document

The registry indexes the manifest under the referrers list for `subject.digest`,
so the Catalog Adapter can discover it via `GET /v2/<name>/referrers/<digest>`.
"""

from __future__ import annotations

from typing import Any, TypedDict

# OCI image-spec empty config: a well-known zero-byte blob.
# https://github.com/opencontainers/image-spec/blob/main/manifest.md#guidelines-for-creating-an-artifact-manifest
EMPTY_CONFIG_DIGEST = "sha256:e3b0c44398fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
EMPTY_CONFIG_SIZE = 0
EMPTY_CONFIG_MEDIA_TYPE = "application/vnd.oci.empty.v1+json"

OCI_MANIFEST_MEDIA_TYPE = "application/vnd.oci.image.manifest.v1+json"


class Descriptor(TypedDict):
    """OCI descriptor: mediaType + digest + size (+ optional annotations)."""

    mediaType: str  # OCI field names are camelCase per the spec
    digest: str
    size: int


def build_app_definition_manifest(
    *,
    subject_descriptor: Descriptor,
    definition_blob_descriptor: Descriptor,
    definition_media_type: str,
) -> dict[str, Any]:
    """Build the app-definition referrer manifest.

    Args:
        subject_descriptor: Descriptor of the container image manifest the
            app-definition attaches to (resolved by the client via a HEAD/GET
            on the image tag/digest).
        definition_blob_descriptor: Descriptor of the already-pushed app-definition
            JSON blob (the manifest's single layer).
        definition_media_type: The `artifactType` to set on the manifest —
            `application/vnd.cytario.app-definition.v1+json`.

    Returns:
        The OCI image manifest as a JSON-serializable dict. Its digest is
        computed by the caller (or the registry on push).

    """
    return {
        "schemaVersion": 2,
        "mediaType": OCI_MANIFEST_MEDIA_TYPE,
        "artifactType": definition_media_type,
        "config": {
            "mediaType": EMPTY_CONFIG_MEDIA_TYPE,
            "digest": EMPTY_CONFIG_DIGEST,
            "size": EMPTY_CONFIG_SIZE,
        },
        "layers": [definition_blob_descriptor],
        "subject": subject_descriptor,
    }


__all__ = [
    "EMPTY_CONFIG_DIGEST",
    "EMPTY_CONFIG_MEDIA_TYPE",
    "EMPTY_CONFIG_SIZE",
    "OCI_MANIFEST_MEDIA_TYPE",
    "Descriptor",
    "build_app_definition_manifest",
]
