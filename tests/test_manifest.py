"""Tests for cytario_app_sdk.oci.manifest.build_app_definition_manifest."""

from __future__ import annotations

import hashlib
import json

from cytario_app_sdk.oci.manifest import (
    EMPTY_CONFIG_BYTES,
    EMPTY_CONFIG_DIGEST,
    EMPTY_CONFIG_MEDIA_TYPE,
    EMPTY_CONFIG_SIZE,
    OCI_MANIFEST_MEDIA_TYPE,
    build_app_definition_manifest,
)

ARTIFACT_TYPE = "application/vnd.cytario.app-definition.v1+json"


def test_empty_config_digest_matches_empty_bytes() -> None:
    """The manifest's `config.digest` must equal the SHA-256 of the bytes the
    SDK pushes as the empty config blob. A previous hardcoded constant had a
    one-character typo (e3b0c443 vs e3b0c442) that made Harbor reject the
    manifest with MANIFEST_BLOB_UNKNOWN for a blob that could never exist.
    Deriving the digest from the bytes (and asserting it here) makes that
    class of bug impossible.
    """
    assert f"sha256:{hashlib.sha256(EMPTY_CONFIG_BYTES).hexdigest()}" == EMPTY_CONFIG_DIGEST
    assert EMPTY_CONFIG_SIZE == len(EMPTY_CONFIG_BYTES) == 0


def test_manifest_has_required_oci_fields(subject_descriptor) -> None:  # type: ignore[misc]
    layer = {"mediaType": ARTIFACT_TYPE, "digest": "sha256:abc", "size": 10}
    manifest = build_app_definition_manifest(
        subject_descriptor=subject_descriptor,
        definition_blob_descriptor=layer,
        definition_media_type=ARTIFACT_TYPE,
    )
    assert manifest["schemaVersion"] == 2
    assert manifest["mediaType"] == OCI_MANIFEST_MEDIA_TYPE
    assert manifest["artifactType"] == ARTIFACT_TYPE
    assert manifest["subject"] == subject_descriptor
    assert manifest["layers"] == [layer]


def test_manifest_uses_oci_empty_config() -> None:
    layer = {"mediaType": ARTIFACT_TYPE, "digest": "sha256:abc", "size": 10}
    manifest = build_app_definition_manifest(
        subject_descriptor={
            "mediaType": OCI_MANIFEST_MEDIA_TYPE,
            "digest": "sha256:subj",
            "size": 1,
        },
        definition_blob_descriptor=layer,
        definition_media_type=ARTIFACT_TYPE,
    )
    assert manifest["config"] == {
        "mediaType": EMPTY_CONFIG_MEDIA_TYPE,
        "digest": EMPTY_CONFIG_DIGEST,
        "size": 0,
    }


def test_manifest_is_json_serializable(subject_descriptor) -> None:  # type: ignore[misc]
    layer = {"mediaType": ARTIFACT_TYPE, "digest": "sha256:abc", "size": 10}
    manifest = build_app_definition_manifest(
        subject_descriptor=subject_descriptor,
        definition_blob_descriptor=layer,
        definition_media_type=ARTIFACT_TYPE,
    )
    # Must round-trip through JSON without loss.
    round_tripped = json.loads(json.dumps(manifest))
    assert round_tripped == manifest
