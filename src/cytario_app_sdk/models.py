"""Pydantic models for the Cytario app-definition YAML.

The shape mirrors the analysis-application definition the cytario-compute
Catalog Adapter validates (SDS-CY-080200): schema version, parameter schema,
declared input/output roles, and consumer/maintainer groups. The container image
reference (`image`) is what the SDK attaches the definition to as an OCI
Referrer.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class ImageRef(BaseModel):
    """Reference to the container image the app-definition attaches to."""

    model_config = ConfigDict(extra="forbid")

    repository: str = Field(
        ...,
        description="OCI repository name, e.g. 'cytario/cellseg' (lowercase, slash-namespaced).",
    )
    tag: str | None = Field(
        default=None,
        description="Image tag. Mutually exclusive with `digest`; one of the two is required.",
    )
    digest: str | None = Field(
        default=None,
        description="Image manifest digest, e.g. 'sha256:abc...'. Mutually exclusive with `tag`.",
    )

    @model_validator(mode="after")
    def _exactly_one_ref(self) -> ImageRef:
        if bool(self.tag) == bool(self.digest):
            msg = "exactly one of `tag` or `digest` must be set"
            raise ValueError(msg)
        return self

    @field_validator("repository")
    @classmethod
    def _lowercase_repository(cls, v: str) -> str:
        if v != v.lower():
            msg = f"repository must be lowercase, got {v!r}"
            raise ValueError(msg)
        return v


class RoleBinding(BaseModel):
    """A named input or output role with the media types it accepts/produces."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(..., min_length=1, description="Role name, e.g. 'image' or 'segmentation'.")
    media_types: list[str] = Field(
        ...,
        min_length=1,
        alias="mediaTypes",
        description="MIME media types the role accepts (input) or produces (output).",
    )


class GroupBindings(BaseModel):
    """Keycloak group memberships that gate visibility of the app."""

    model_config = ConfigDict(extra="forbid")

    consumers: list[str] = Field(
        default_factory=list,
        description="Groups whose members may see and run the app.",
    )
    maintainers: list[str] = Field(
        default_factory=list,
        description="Groups whose members may edit the app's saved configs.",
    )


class AppDefinition(BaseModel):
    """Top-level app-definition document loaded from the YAML."""

    model_config = ConfigDict(extra="forbid")

    schema_version: int = Field(
        default=1,
        alias="schemaVersion",
        description="App-definition schema version. Currently 1.",
    )
    name: str = Field(
        ...,
        min_length=1,
        max_length=128,
        description="Stable app identifier (lowercase, no whitespace).",
    )
    display: str | None = Field(
        default=None,
        description="Human-readable display name. Defaults to `name`.",
    )
    description: str | None = Field(default=None, description="Short prose description.")
    image: ImageRef
    parameter_schema: dict[str, Any] = Field(
        default_factory=dict,
        alias="parameterSchema",
        description=(
            "JSON Schema (draft 2020-12) describing the run-configuration form. "
            "Empty object means no parameters."
        ),
    )
    input_roles: list[RoleBinding] = Field(
        default_factory=list,
        alias="inputRoles",
        description="Input roles the app consumes (e.g. an image to analyze).",
    )
    output_roles: list[RoleBinding] = Field(
        default_factory=list,
        alias="outputRoles",
        description="Output roles the app produces (e.g. a label mask).",
    )
    groups: GroupBindings = Field(default_factory=GroupBindings)

    @field_validator("name")
    @classmethod
    def _name_pattern(cls, v: str) -> str:
        if v != v.strip() or any(c.isspace() for c in v):
            msg = f"name must not contain whitespace, got {v!r}"
            raise ValueError(msg)
        return v

    @model_validator(mode="after")
    def _display_default(self) -> AppDefinition:
        if self.display is None:
            object.__setattr__(self, "display", self.name)
        return self

    @property
    def artifact_type(self) -> str:
        """OCI artifactType for the app-definition manifest."""
        return "application/vnd.cytario.app-definition.v1+json"

    @property
    def definition_document(self) -> dict[str, Any]:
        """The JSON document pushed as the artifact's layer blob.

        This is the catalog-discovery payload: a credential-free, PII-free
        projection consumed by the Catalog Adapter (SDS-CY-080200).
        """
        return {
            "schemaVersion": self.schema_version,
            "name": self.name,
            "display": self.display,
            "description": self.description,
            "image": {
                "repository": self.image.repository,
                **({"tag": self.image.tag} if self.image.tag else {}),
                **({"digest": self.image.digest} if self.image.digest else {}),
            },
            "parameterSchema": self.parameter_schema,
            "inputRoles": [r.model_dump(by_alias=True) for r in self.input_roles],
            "outputRoles": [r.model_dump(by_alias=True) for r in self.output_roles],
            "groups": self.groups.model_dump(),
        }


__all__ = ["AppDefinition", "GroupBindings", "ImageRef", "RoleBinding"]
