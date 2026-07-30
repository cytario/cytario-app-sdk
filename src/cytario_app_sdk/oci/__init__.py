"""OCI Distribution sub-package."""

from __future__ import annotations

from cytario_app_sdk.oci.client import RegistryClient
from cytario_app_sdk.oci.manifest import APPDEF_ANNOTATION_KEY, attach_definition_annotation

__all__ = ["APPDEF_ANNOTATION_KEY", "RegistryClient", "attach_definition_annotation"]
