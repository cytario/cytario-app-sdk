"""Attach the analysis-application definition to an image manifest as an OCI Image Format annotation.

The app-definition is carried as an OCI Image Format annotation on the
container image's manifest — not as a separate referrer artifact. Because
the annotation is part of the manifest content, the manifest's immutable
content digest binds the definition to the exact image: pinning the image by
digest also pins the definition the user saw at listing time, and the
definition cannot be swapped under a fixed image digest.

The annotation key is ``org.cytario.appdef.v1`` (the contract surface with the
Cytario runtime's ``extractDefinition``); the value is the canonical JSON
app-definition document.
OCI image manifests and image indexes carry a top-level ``annotations`` map
per the OCI Image Format spec; the SDK adds the key there. Docker v2 schema-2
manifests do not define ``annotations``, but most OCI-compliant registries
tolerate the extra field — re-push the image as an OCI image
(``docker build --output type=oci``) if your registry rejects it.
"""

from __future__ import annotations

import copy
from typing import Any

# OCI image manifest media type. Used as the default Accept and as the
# Content-Type when PUTting a manifest whose fetched media type was missing.
OCI_MANIFEST_MEDIA_TYPE = "application/vnd.oci.image.manifest.v1+json"

# Accept any image-manifest variant when *reading* a manifest: the container
# image may have been pushed as a Docker v2 schema-2 manifest, an OCI manifest,
# or a multi-arch index. Listing all of them lets the registry return whichever
# the image actually uses instead of 404/406-ing on a narrow Accept.
MANIFEST_ACCEPT = (
    f"{OCI_MANIFEST_MEDIA_TYPE}, "
    "application/vnd.docker.distribution.manifest.v2+json, "
    "application/vnd.docker.distribution.manifest.list.v2+json, "
    "application/vnd.docker.distribution.manifest.v1+json, "
    "application/vnd.oci.image.index.v1+json"
)

# The OCI Image Format annotation key carrying the app-definition document.
# The value is the canonical JSON app-definition. This key is the contract
# surface with the Cytario runtime's ``extractDefinition`` and lives in the
# SDK + runtime, not in any shared public plugin-api package.
APPDEF_ANNOTATION_KEY = "org.cytario.appdef.v1"


def attach_definition_annotation(
    *,
    manifest: dict[str, Any],
    definition_json: str,
) -> dict[str, Any]:
    """Return a copy of ``manifest`` with the app-definition annotation set.

    Adds (or overwrites) ``manifest.annotations[APPDEF_ANNOTATION_KEY]`` with
    the canonical JSON app-definition string. The input manifest is not
    mutated. The ``annotations`` map is created if absent.

    Args:
        manifest: The fetched image manifest (or image index) as a parsed dict.
        definition_json: The canonical JSON app-definition document to carry
            as the annotation value.

    Returns:
        A new manifest dict carrying the annotation. Re-push it with the same
        media type the registry returned at fetch time so the registry stores
        it as the same kind of manifest.

    """
    annotated = copy.deepcopy(manifest)
    annotations = annotated.setdefault("annotations", {})
    if not isinstance(annotations, dict):
        msg = "manifest `annotations` is present but not an object"
        raise TypeError(msg)
    annotations[APPDEF_ANNOTATION_KEY] = definition_json
    return annotated


__all__ = [
    "APPDEF_ANNOTATION_KEY",
    "MANIFEST_ACCEPT",
    "OCI_MANIFEST_MEDIA_TYPE",
    "attach_definition_annotation",
]
