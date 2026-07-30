"""Pydantic models for the Cytario app-definition YAML.

The shape mirrors the analysis-application definition the Cytario compute
runtime validates: schema version, application identifier, display name,
description, a reduced parameter schema, declared input/output data roles,
and flat consumer/maintainer group lists. The container image reference
(`image`) is what the SDK attaches the definition to as an OCI Image Format
annotation on the image manifest.

This model is the contract surface between the SDK (producer) and the
Cytario runtime's ``AppDefinition`` (consumer). The
``definition_document`` it emits MUST round-trip through the runtime's
``validateAppDefinition`` without modification, so the field names and the
reduced ``parameterSchema`` subset are pinned here and there in lockstep.
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


class ParameterField(BaseModel):
    """A single parameter field — a reduced JSON-Schema-like subset.

    Matches the Cytario runtime ``ParameterField``: a schema-driven form
    renderer and a server-side validator both consume this subset without a
    full JSON-Schema runtime. Full JSON-Schema is NOT used.
    """

    model_config = ConfigDict(extra="forbid")

    name: str = Field(..., min_length=1, description="Stable field key; unique within a definition.")
    label: str = Field(..., min_length=1, description="Human-readable label rendered in the config form.")
    type: str = Field(
        ...,
        description="Field type — drives the form widget and the server-side coercer.",
    )
    options: list[str] | None = Field(
        default=None,
        description="For `enum`: the selectable values. Ignored for other types.",
    )
    required: bool = Field(..., description="Whether the field must be supplied at run time.")
    default: str | int | float | bool | None = Field(
        default=None,
        description="Default value when the field is omitted at run time.",
    )
    minimum: float | None = Field(default=None, description="Numeric lower bound (number/integer).")
    maximum: float | None = Field(default=None, description="Numeric upper bound (number/integer).")
    description: str | None = Field(default=None, description="Free-text help rendered under the field.")

    @field_validator("type")
    @classmethod
    def _valid_type(cls, v: str) -> str:
        allowed = {"string", "number", "integer", "boolean", "enum"}
        if v not in allowed:
            msg = f"type must be one of {sorted(allowed)}, got {v!r}"
            raise ValueError(msg)
        return v

    @model_validator(mode="after")
    def _enum_requires_options(self) -> ParameterField:
        if self.type == "enum":
            if not self.options:
                msg = "enum field requires a non-empty `options` list"
                raise ValueError(msg)
            if any(not isinstance(o, str) for o in self.options):
                msg = "enum `options` must all be strings"
                raise ValueError(msg)
        return self


class DataRole(BaseModel):
    """A declared input or output role of the analysis application.

    A complete app-definition declares at least one input and one output
    role. A role names
    the kind of data the container accepts (input) or produces (output); the run
    flow constrains the user's chosen input/output storage locations to the
    org's connection prefixes. ``minCount``/``maxCount`` bound the object count
    a role accepts (defaults: min 1, max 1; -1 = unbounded).

    Note: this model intentionally does NOT carry MIME ``mediaTypes``. The
    runtime ``DataRole`` does not model media-type constraints; media-type
    validation, if required, is a run-flow concern
    outside the app-definition contract.
    """

    model_config = ConfigDict(extra="forbid")

    name: str = Field(..., min_length=1, description="Role name, e.g. 'image' or 'segmentation'.")
    kind: str = Field(..., description="Whether this is an input or output role.")
    min_count: int | None = Field(
        default=None,
        alias="minCount",
        description="Minimum object count this role accepts (default 1).",
    )
    max_count: int | None = Field(
        default=None,
        alias="maxCount",
        description="Maximum object count this role accepts (default 1; -1 = unbounded).",
    )

    @field_validator("kind")
    @classmethod
    def _valid_kind(cls, v: str) -> str:
        if v not in ("input", "output"):
            msg = f"kind must be 'input' or 'output', got {v!r}"
            raise ValueError(msg)
        return v


class AppDefinition(BaseModel):
    """Top-level app-definition document loaded from the YAML."""

    model_config = ConfigDict(extra="forbid")

    schema_version: int = Field(
        default=1,
        alias="schemaVersion",
        description="App-definition schema version. Currently 1.",
    )
    application_id: str = Field(
        ...,
        alias="applicationId",
        min_length=1,
        max_length=128,
        description="Stable app identifier (lowercase, no whitespace).",
    )
    name: str = Field(
        ...,
        min_length=1,
        max_length=128,
        description="Human-readable name shown in the picker.",
    )
    description: str = Field(..., description="Short prose description.")
    image: ImageRef
    parameter_schema: list[ParameterField] = Field(
        default_factory=list,
        alias="parameterSchema",
        description=(
            "Reduced parameter-schema subset (not full JSON-Schema) consumed by "
            "the schema-driven config form and the server-side validator."
        ),
    )
    data_roles: list[DataRole] = Field(
        default_factory=list,
        alias="dataRoles",
        description="Input/output data roles the app consumes/produces.",
    )
    consumer_groups: list[str] = Field(
        default_factory=list,
        alias="consumerGroups",
        description="Keycloak group display names whose members may see and run the app.",
    )
    maintainer_groups: list[str] = Field(
        default_factory=list,
        alias="maintainerGroups",
        description="Keycloak group display names whose members may edit the app's saved configs.",
    )

    @field_validator("application_id")
    @classmethod
    def _application_id_pattern(cls, v: str) -> str:
        if v != v.strip() or any(c.isspace() for c in v):
            msg = f"applicationId must not contain whitespace, got {v!r}"
            raise ValueError(msg)
        return v

    @model_validator(mode="after")
    def _at_least_one_input_and_output(self) -> AppDefinition:
        kinds = {r.kind for r in self.data_roles}
        if self.data_roles and (not (kinds & {"input"}) or not (kinds & {"output"})):
            msg = "dataRoles must declare at least one input and one output role"
            raise ValueError(msg)
        return self

    @property
    def artifact_type(self) -> str:
        """OCI artifactType for the app-definition manifest."""
        return "application/vnd.cytario.app-definition.v1+json"

    @property
    def definition_document(self) -> dict[str, Any]:
        """The JSON document pushed as the artifact's layer blob.

        This is the catalog-discovery payload: a credential-free, PII-free
        projection consumed by the Cytario runtime at listing time. It round-
        trips through the runtime's ``validateAppDefinition`` unchanged,
        so the field names here MUST match the runtime's ``AppDefinition``.

        ``versions`` is deliberately NOT emitted here — available versions are
        discovered by the Cytario runtime from the image's tags/manifests at
        listing time, not authored in the definition document.
        """
        return {
            "schemaVersion": self.schema_version,
            "applicationId": self.application_id,
            "name": self.name,
            "description": self.description,
            "parameterSchema": [
                f.model_dump(by_alias=True, exclude_none=True) for f in self.parameter_schema
            ],
            "dataRoles": [r.model_dump(by_alias=True, exclude_none=True) for r in self.data_roles],
            "consumerGroups": list(self.consumer_groups),
            "maintainerGroups": list(self.maintainer_groups),
        }


__all__ = ["AppDefinition", "DataRole", "ImageRef", "ParameterField"]
