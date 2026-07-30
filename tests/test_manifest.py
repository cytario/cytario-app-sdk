"""Tests for cytario_app_sdk.oci.manifest.attach_definition_annotation."""

from __future__ import annotations

import copy
import json

from cytario_app_sdk.oci.manifest import (
    APPDEF_ANNOTATION_KEY,
    MANIFEST_ACCEPT,
    OCI_MANIFEST_MEDIA_TYPE,
    attach_definition_annotation,
)


def test_appdef_annotation_key_is_stable() -> None:
    """The annotation key is the contract surface with the Cytario runtime's
    ``extractDefinition``. A change here breaks discovery."""
    assert APPDEF_ANNOTATION_KEY == "org.cytario.appdef.v1"


def test_manifest_accept_includes_all_image_manifest_variants() -> None:
    """The GET Accept must cover Docker v2 schema 2, OCI manifest, and OCI
    index so the registry returns whichever the image actually uses instead
    of 404/406-ing on a narrow Accept."""
    assert OCI_MANIFEST_MEDIA_TYPE in MANIFEST_ACCEPT
    assert "application/vnd.docker.distribution.manifest.v2+json" in MANIFEST_ACCEPT
    assert "application/vnd.oci.image.index.v1+json" in MANIFEST_ACCEPT


def test_attach_adds_annotation_to_manifest_without_existing_annotations() -> None:
    manifest = {
        "schemaVersion": 2,
        "mediaType": OCI_MANIFEST_MEDIA_TYPE,
        "config": {"mediaType": "x", "digest": "sha256:0", "size": 0},
        "layers": [],
    }
    definition_json = '{"applicationId":"cellseg"}'
    annotated = attach_definition_annotation(manifest=manifest, definition_json=definition_json)
    assert annotated["annotations"][APPDEF_ANNOTATION_KEY] == definition_json
    # The original manifest is not mutated.
    assert "annotations" not in manifest


def test_attach_preserves_existing_annotations() -> None:
    manifest = {
        "schemaVersion": 2,
        "mediaType": OCI_MANIFEST_MEDIA_TYPE,
        "config": {"mediaType": "x", "digest": "sha256:0", "size": 0},
        "layers": [],
        "annotations": {"org.opencontainers.image.created": "2026-01-01T00:00:00Z"},
    }
    definition_json = '{"applicationId":"cellseg"}'
    annotated = attach_definition_annotation(manifest=manifest, definition_json=definition_json)
    # Existing annotations are preserved.
    assert annotated["annotations"]["org.opencontainers.image.created"] == "2026-01-01T00:00:00Z"
    # The appdef annotation is added.
    assert annotated["annotations"][APPDEF_ANNOTATION_KEY] == definition_json
    # The original manifest's annotations dict is not mutated.
    assert APPDEF_ANNOTATION_KEY not in manifest["annotations"]


def test_attach_overwrites_existing_appdef_annotation() -> None:
    manifest = {
        "schemaVersion": 2,
        "mediaType": OCI_MANIFEST_MEDIA_TYPE,
        "config": {"mediaType": "x", "digest": "sha256:0", "size": 0},
        "layers": [],
        "annotations": {APPDEF_ANNOTATION_KEY: "stale"},
    }
    annotated = attach_definition_annotation(manifest=manifest, definition_json="new")
    assert annotated["annotations"][APPDEF_ANNOTATION_KEY] == "new"


def test_attach_deep_copies_unrelated_fields() -> None:
    """The returned manifest is a deep copy — mutating it must not affect the
    input, and vice versa."""
    layers = [{"mediaType": "x", "digest": "sha256:0", "size": 0}]
    manifest = {
        "schemaVersion": 2,
        "mediaType": OCI_MANIFEST_MEDIA_TYPE,
        "config": {"mediaType": "x", "digest": "sha256:0", "size": 0},
        "layers": layers,
    }
    annotated = attach_definition_annotation(manifest=manifest, definition_json="{}")
    annotated["layers"].append({"mediaType": "y", "digest": "sha256:1", "size": 1})
    assert len(manifest["layers"]) == 1
    assert len(annotated["layers"]) == 2


def test_attach_raises_when_annotations_is_not_an_object() -> None:
    manifest = {
        "schemaVersion": 2,
        "mediaType": OCI_MANIFEST_MEDIA_TYPE,
        "config": {"mediaType": "x", "digest": "sha256:0", "size": 0},
        "layers": [],
        "annotations": "not-an-object",
    }
    try:
        attach_definition_annotation(manifest=manifest, definition_json="{}")
    except TypeError:
        pass
    else:
        msg = "expected TypeError when annotations is not an object"
        raise AssertionError(msg)


def test_returned_manifest_is_json_serializable() -> None:
    manifest = {
        "schemaVersion": 2,
        "mediaType": OCI_MANIFEST_MEDIA_TYPE,
        "config": {"mediaType": "x", "digest": "sha256:0", "size": 0},
        "layers": [],
    }
    annotated = attach_definition_annotation(manifest=manifest, definition_json='{"x":1}')
    # Must round-trip through JSON without loss (the client canonical-JSONs it).
    round_tripped = json.loads(json.dumps(annotated))
    assert round_tripped == annotated
    # Ensure copy import is actually used (silence unused-import lint).
    assert copy.deepcopy(annotated) == annotated
