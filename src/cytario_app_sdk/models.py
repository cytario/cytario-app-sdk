"""Pydantic models for the Cytario app-definition YAML.

The shape mirrors the analysis-application definition the Cytario compute
runtime validates: schema version, application identifier, display name,
description, a reduced parameter schema, declared input/output data roles, and
an optional compute-resource floor. The container image reference (`image`) is
what the SDK attaches the definition to as an OCI Image Format annotation on
the image manifest.

This model is the contract surface between the SDK (producer) and the
Cytario runtime's ``AppDefinition`` (consumer). The
``definition_document`` it emits MUST round-trip through the runtime's
``validateAppDefinition`` without modification, so the field names and the
reduced ``parameterSchema`` subset are pinned here and there in lockstep.

Application access (who may see/run an app, who may edit its saved configs)
is NOT part of the app-definition. Entitlement is governed by the
catalog connection's access scope on the Cytario side, not by anything
carried in the registry image — so no access-control fields live on this
model.
"""

from __future__ import annotations

import re
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

# Kubernetes-style resource quantities. Memory and ephemeral storage use binary
# suffixes (Ki, Mi, Gi, Ti, Pi, Ei); a bare integer is bytes. CPU uses millicores
# (``m`` suffix) or whole cores (integer). Fractional cores are rejected — the
# author writes ``500m`` instead of ``0.5`` (SRS-CY-414108). The app-definition
# carries only the floor (``requests``); the caller may raise it at run time,
# capped by the provider maximum (SRS-CY-415110).
_MEMORY_RE = re.compile(r"^[0-9]+(?:Ki|Mi|Gi|Ti|Pi|Ei)?$")
_CPU_RE = re.compile(r"^[0-9]+m?$")
_MEMORY_SUFFIXES: dict[str, int] = {
    "": 1,
    "Ki": 1024,
    "Mi": 1024**2,
    "Gi": 1024**3,
    "Ti": 1024**4,
    "Pi": 1024**5,
    "Ei": 1024**6,
}


def _parse_memory(value: str) -> int:
    """Parse a binary-suffixed memory quantity to bytes.

    Raise ``ValueError`` on an unparseable value. Positivity of the floor
    is enforced by the ``ResourceRequests`` model validator, not here.
    """
    if not _MEMORY_RE.match(value):
        msg = (
            f"resource quantity {value!r} must be an integer with a "
            "binary suffix (Ki, Mi, Gi, Ti, Pi, Ei) or none"
        )
        raise ValueError(msg)
    for suffix in ("Ei", "Pi", "Ti", "Gi", "Mi", "Ki", ""):
        if value.endswith(suffix):
            n = int(value[: -len(suffix)] if suffix else value)
            return n * _MEMORY_SUFFIXES[suffix]
    # Unreachable: the regex already gated the input.
    msg = f"unparseable resource quantity {value!r}"
    raise ValueError(msg)  # pragma: no cover


def _parse_cpu(value: str) -> int:
    """Parse a CPU quantity to millicores.

    ``2000m`` → 2000; ``2`` → 2000. Raise ``ValueError`` on an unparseable
    or non-positive value. Fractional cores (``0.5``) and fractional
    millicores (``2.5m``) are rejected — the author writes ``500m`` or
    ``250m`` instead (SRS-CY-414108).
    """
    if not _CPU_RE.match(value):
        msg = f"cpu quantity {value!r} must be an integer core count or millicores (e.g. '2', '2000m')"
        raise ValueError(msg)
    if value.endswith("m"):
        return int(value[:-1])
    return int(value) * 1000


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


class ResourceRequests(BaseModel):
    """The compute-resource floor an application declares (SRS-CY-414108).

    Carried under ``resources.requests`` in the app-definition YAML — the
    Kubernetes ``ResourceRequirements`` shape, so the same definition is
    expressible on AWS Batch (EC2/Fargate) and a Kubernetes Job binding
    without per-provider fields (SDS §7.22). The block names no provider,
    queue, instance type, or region; it carries only algorithm-level
    resource needs.

    Memory and ephemeral-storage quantities use binary suffixes (``Ki``,
    ``Mi``, ``Gi``, ``Ti``, ``Pi``, ``Ei``); a bare integer is bytes. CPU
    uses millicores (``m``) or whole cores (integer; fractional cores are
    rejected — write ``500m`` instead of ``0.5``). GPU is an integer count
    (zero or omitted = CPU-only). The runtime validates the block alongside
    the rest of the definition; a missing ``memory`` floor, a non-positive
    floor (``memory``/``ephemeralStorage``), a negative per-GiB increment,
    or a non-integer GPU count causes the application to be treated as
    unavailable (SRS-CY-414103).
    """

    model_config = ConfigDict(extra="forbid")

    cpu: str = Field(
        default="1000m",
        description="Minimum CPU requested, in millicores ('2000m') or whole cores ('2').",
    )
    memory: str = Field(
        ...,
        description="Minimum memory floor, independent of input size (e.g. '8Gi', '8192Mi').",
    )
    memory_per_input_gb: str = Field(
        default="0",
        alias="memoryPerInputGb",
        description="Memory added per GiB of input object size (e.g. '1Gi').",
    )
    ephemeral_storage: str = Field(
        default="1Gi",
        alias="ephemeralStorage",
        description="Minimum ephemeral-storage (scratch) floor.",
    )
    ephemeral_storage_per_input_gb: str = Field(
        default="0",
        alias="ephemeralStoragePerInputGb",
        description="Ephemeral storage added per GiB of input object size.",
    )
    gpu: int = Field(
        default=0,
        ge=0,
        description="Number of GPUs required (zero or omitted = CPU-only).",
    )

    @field_validator("memory", "memory_per_input_gb", "ephemeral_storage", "ephemeral_storage_per_input_gb")
    @classmethod
    def _valid_memory_quantity(cls, v: str) -> str:
        _parse_memory(v)  # raises on malformed
        return v

    @field_validator("cpu")
    @classmethod
    def _valid_cpu_quantity(cls, v: str) -> str:
        _parse_cpu(v)  # raises on malformed
        return v

    @model_validator(mode="after")
    def _floors_positive(self) -> ResourceRequests:
        if _parse_memory(self.memory) <= 0:
            msg = f"memory floor must be positive, got {self.memory!r}"
            raise ValueError(msg)
        if _parse_memory(self.ephemeral_storage) <= 0:
            msg = f"ephemeralStorage floor must be positive, got {self.ephemeral_storage!r}"
            raise ValueError(msg)
        if _parse_cpu(self.cpu) <= 0:
            msg = f"cpu must be positive, got {self.cpu!r}"
            raise ValueError(msg)
        return self


class Resources(BaseModel):
    """The optional ``resources`` block on an app-definition (SRS-CY-414108).

    Carries the application's compute-resource floor under ``requests``. The
    app declares no ``limits`` — the caller may raise the floor at run time,
    capped by the provider maximum (SRS-CY-415110).
    """

    model_config = ConfigDict(extra="forbid")

    requests: ResourceRequests


class AppDefinition(BaseModel):
    """Top-level app-definition document loaded from the YAML.

    Carries the credential-free, PII-free metadata the Cytario runtime
    consumes at catalog-discovery time. It deliberately does NOT carry
    access-control fields — entitlement is governed by the catalog
    connection's access scope on the Cytario side, not by the image.
    """

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
    resources: Resources | None = Field(
        default=None,
        description=(
            "Optional compute-resource floor (SRS-CY-414108). Omitting the block "
            "leaves the floor at the compute provider's default (SRS-CY-415110)."
        ),
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
            "resources": (
                self.resources.model_dump(by_alias=True, exclude_none=True) if self.resources else None
            ),
        }


__all__ = ["AppDefinition", "DataRole", "ImageRef", "ParameterField", "ResourceRequests", "Resources"]
