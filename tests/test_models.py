"""Tests for cytario_app_sdk.models.AppDefinition."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml
from pydantic import ValidationError

from cytario_app_sdk.models import AppDefinition, DataRole, ImageRef, ParameterField, _parse_memory


def test_load_example_yaml(example_app_yaml: Path) -> None:
    raw = yaml.safe_load(example_app_yaml.read_text(encoding="utf-8"))
    app = AppDefinition.model_validate(raw)
    assert app.schema_version == 1
    assert app.application_id == "cellseg"
    assert app.name == "Cell Segmentation"
    assert app.image.repository == "cytario/cellseg"
    assert app.image.tag == "1.0.0"
    assert app.image.digest is None
    assert app.parameter_schema[0].name == "diameter"
    assert app.parameter_schema[0].type == "number"
    assert app.parameter_schema[0].default == 30
    assert app.data_roles[0].name == "image"
    assert app.data_roles[0].kind == "input"
    assert app.data_roles[1].name == "segmentation"
    assert app.data_roles[1].kind == "output"


def test_image_ref_requires_exactly_one_of_tag_or_digest() -> None:
    with pytest.raises(ValidationError, match="exactly one of `tag` or `digest`"):
        ImageRef(repository="cytario/x", tag="1.0.0", digest="sha256:abc")
    with pytest.raises(ValidationError, match="exactly one of `tag` or `digest`"):
        ImageRef(repository="cytario/x")


def test_image_ref_rejects_uppercase_repository() -> None:
    with pytest.raises(ValidationError, match="lowercase"):
        ImageRef(repository="Cytario/X", tag="1.0.0")


def test_parameter_field_rejects_invalid_type() -> None:
    with pytest.raises(ValidationError, match="type must be one of"):
        ParameterField(name="x", label="X", type="bogus", required=False)


def test_parameter_field_enum_requires_options() -> None:
    with pytest.raises(ValidationError, match="enum field requires a non-empty"):
        ParameterField(name="x", label="X", type="enum", required=False)


def test_data_role_rejects_invalid_kind() -> None:
    with pytest.raises(ValidationError, match="kind must be 'input' or 'output'"):
        DataRole(name="image", kind="bogus")


def test_application_id_rejects_whitespace() -> None:
    with pytest.raises(ValidationError, match="whitespace"):
        AppDefinition.model_validate(
            {
                "applicationId": "cell seg",
                "name": "Cell Seg",
                "description": "x",
                "image": {"repository": "cytario/x", "tag": "1.0.0"},
            },
        )


def test_extra_fields_rejected() -> None:
    with pytest.raises(ValidationError, match="extra"):
        AppDefinition.model_validate(
            {
                "applicationId": "cellseg",
                "name": "Cell Seg",
                "description": "x",
                "image": {"repository": "cytario/x", "tag": "1.0.0"},
                "unknownField": 1,
            },
        )


def test_access_control_fields_rejected() -> None:
    """Access-control fields no longer live on the app-definition — a YAML
    that still carries them must be rejected so authors stop embedding
    entitlement in the image. Entitlement is governed by the catalog
    connection's access scope on the Cytario side."""
    with pytest.raises(ValidationError, match="extra"):
        AppDefinition.model_validate(
            {
                "applicationId": "cellseg",
                "name": "Cell Seg",
                "description": "x",
                "image": {"repository": "cytario/x", "tag": "1.0.0"},
                "consumerGroups": ["cellbio-team"],
            },
        )
    with pytest.raises(ValidationError, match="extra"):
        AppDefinition.model_validate(
            {
                "applicationId": "cellseg",
                "name": "Cell Seg",
                "description": "x",
                "image": {"repository": "cytario/x", "tag": "1.0.0"},
                "maintainerGroups": ["imaging-platform"],
            },
        )


def test_data_roles_require_input_and_output() -> None:
    with pytest.raises(ValidationError, match="at least one input and one output"):
        AppDefinition.model_validate(
            {
                "applicationId": "cellseg",
                "name": "Cell Seg",
                "description": "x",
                "image": {"repository": "cytario/x", "tag": "1.0.0"},
                "dataRoles": [{"name": "image", "kind": "input"}],
            },
        )


def test_definition_document_round_trips_runtime_schema(example_app_yaml: Path) -> None:
    """The definition_document MUST match the Cytario runtime AppDefinition
    field names so it passes validateAppDefinition unchanged."""
    raw = yaml.safe_load(example_app_yaml.read_text(encoding="utf-8"))
    app = AppDefinition.model_validate(raw)
    doc = app.definition_document
    assert doc["schemaVersion"] == 1
    assert doc["applicationId"] == "cellseg"
    assert doc["name"] == "Cell Segmentation"
    assert doc["description"] == "Runs Cellpose on a 16-bit OME-TIFF."
    assert doc["parameterSchema"][0]["name"] == "diameter"
    assert doc["dataRoles"][0] == {"name": "image", "kind": "input"}
    assert doc["dataRoles"][1] == {"name": "segmentation", "kind": "output"}
    # Access-control fields are NOT projected into the discovery document —
    # entitlement is governed by the catalog connection's access scope on
    # the Cytario side, not by the image.
    assert "consumerGroups" not in doc
    assert "maintainerGroups" not in doc
    # versions is NOT emitted — discovered from image tags at listing time.
    assert "versions" not in doc


def test_definition_document_is_pii_free(example_app_yaml: Path) -> None:
    raw = yaml.safe_load(example_app_yaml.read_text(encoding="utf-8"))
    app = AppDefinition.model_validate(raw)
    doc = app.definition_document
    assert "secret" not in doc
    assert "token" not in doc
    assert "user" not in doc
    # The image ref is not projected into the discovery document — the catalog
    # adapter resolves the image from the registry at listing time.
    assert "image" not in doc


def test_artifact_type_is_stable(example_app_yaml: Path) -> None:
    raw = yaml.safe_load(example_app_yaml.read_text(encoding="utf-8"))
    app = AppDefinition.model_validate(raw)
    assert app.artifact_type == "application/vnd.cytario.app-definition.v1+json"


def test_digest_image_ref_round_trips() -> None:
    app = AppDefinition.model_validate(
        {
            "applicationId": "cellseg",
            "name": "Cell Seg",
            "description": "x",
            "image": {"repository": "cytario/cellseg", "digest": "sha256:abc"},
        },
    )
    assert app.image.digest == "sha256:abc"
    assert app.image.tag is None


# ---------------------------------------------------------------------------
# resources block (SRS-CY-414108)
# ---------------------------------------------------------------------------


def _app_dict(**extra: Any) -> dict[str, Any]:
    base = {
        "applicationId": "cellseg",
        "name": "Cell Seg",
        "description": "x",
        "image": {"repository": "cytario/x", "tag": "1.0.0"},
        "dataRoles": [{"name": "image", "kind": "input"}, {"name": "out", "kind": "output"}],
    }
    base.update(extra)
    return base


def test_resources_block_optional() -> None:
    app = AppDefinition.model_validate(_app_dict())
    assert app.resources is None


def test_resources_block_accepted(example_app_yaml: Path) -> None:
    raw = yaml.safe_load(example_app_yaml.read_text(encoding="utf-8"))
    app = AppDefinition.model_validate(raw)
    assert app.resources is not None
    req = app.resources.requests
    assert req.cpu == "2000m"
    assert req.memory == "8Gi"
    assert req.memory_per_input_gb == "1Gi"
    assert req.ephemeral_storage == "20Gi"
    assert req.ephemeral_storage_per_input_gb == "2Gi"
    assert req.gpu == 1


def test_resources_defaults_when_partial() -> None:
    app = AppDefinition.model_validate(
        _app_dict(resources={"requests": {"memory": "4Gi"}}),
    )
    req = app.resources.requests
    assert req.cpu == "1000m"
    assert req.memory == "4Gi"
    assert req.memory_per_input_gb == "0"
    assert req.ephemeral_storage == "1Gi"
    assert req.ephemeral_storage_per_input_gb == "0"
    assert req.gpu == 0


def test_resources_requires_memory() -> None:
    with pytest.raises(ValidationError, match="memory"):
        AppDefinition.model_validate(_app_dict(resources={"requests": {"cpu": "2000m"}}))


def test_resources_rejects_missing_requests() -> None:
    with pytest.raises(ValidationError, match="requests"):
        AppDefinition.model_validate(_app_dict(resources={}))


def test_resources_rejects_extra_field() -> None:
    with pytest.raises(ValidationError, match="extra"):
        AppDefinition.model_validate(
            _app_dict(resources={"requests": {"memory": "4Gi", "limits": {}}}),
        )


def test_resources_rejects_non_binary_memory_suffix() -> None:
    with pytest.raises(ValidationError, match="binary suffix"):
        AppDefinition.model_validate(
            _app_dict(resources={"requests": {"memory": "8G"}}),
        )


def test_resources_rejects_non_positive_memory_floor() -> None:
    with pytest.raises(ValidationError, match="memory floor must be positive"):
        AppDefinition.model_validate(
            _app_dict(resources={"requests": {"memory": "0"}}),
        )


def test_resources_rejects_negative_per_input_factor() -> None:
    with pytest.raises(ValidationError, match="binary suffix"):
        AppDefinition.model_validate(
            _app_dict(resources={"requests": {"memory": "4Gi", "memoryPerInputGb": "-1Gi"}}),
        )


def test_resources_rejects_non_integer_gpu() -> None:
    with pytest.raises(ValidationError, match="gpu"):
        AppDefinition.model_validate(
            _app_dict(resources={"requests": {"memory": "4Gi", "gpu": 1.5}}),
        )


def test_resources_rejects_negative_gpu() -> None:
    with pytest.raises(ValidationError, match="gpu"):
        AppDefinition.model_validate(
            _app_dict(resources={"requests": {"memory": "4Gi", "gpu": -1}}),
        )


def test_resources_rejects_invalid_cpu() -> None:
    with pytest.raises(ValidationError, match="cpu quantity"):
        AppDefinition.model_validate(
            _app_dict(resources={"requests": {"memory": "4Gi", "cpu": "fast"}}),
        )


def test_resources_rejects_non_positive_cpu() -> None:
    with pytest.raises(ValidationError, match="cpu must be positive"):
        AppDefinition.model_validate(
            _app_dict(resources={"requests": {"memory": "4Gi", "cpu": "0m"}}),
        )


def test_resources_accepts_bare_bytes_memory() -> None:
    app = AppDefinition.model_validate(
        _app_dict(resources={"requests": {"memory": "8589934592"}}),
    )
    assert app.resources.requests.memory == "8589934592"


def test_resources_accepts_whole_core_cpu() -> None:
    app = AppDefinition.model_validate(
        _app_dict(resources={"requests": {"memory": "4Gi", "cpu": "2"}}),
    )
    assert app.resources.requests.cpu == "2"


def test_resources_rejects_fractional_core_cpu() -> None:
    with pytest.raises(ValidationError, match="cpu quantity"):
        AppDefinition.model_validate(
            _app_dict(resources={"requests": {"memory": "4Gi", "cpu": "0.5"}}),
        )


def test_resources_rejects_fractional_millicores() -> None:
    with pytest.raises(ValidationError, match="cpu quantity"):
        AppDefinition.model_validate(
            _app_dict(resources={"requests": {"memory": "4Gi", "cpu": "2.5m"}}),
        )


@pytest.mark.parametrize(
    ("suffix", "bytes_per"),
    [
        ("", 1),
        ("Ki", 1024),
        ("Mi", 1024**2),
        ("Gi", 1024**3),
        ("Ti", 1024**4),
        ("Pi", 1024**5),
        ("Ei", 1024**6),
    ],
)
def test_memory_suffixes_parse(suffix: str, bytes_per: int) -> None:
    assert _parse_memory(f"2{suffix}") == 2 * bytes_per


def test_definition_document_projects_resources(example_app_yaml: Path) -> None:
    raw = yaml.safe_load(example_app_yaml.read_text(encoding="utf-8"))
    app = AppDefinition.model_validate(raw)
    doc = app.definition_document
    assert doc["resources"] == {
        "requests": {
            "cpu": "2000m",
            "memory": "8Gi",
            "memoryPerInputGb": "1Gi",
            "ephemeralStorage": "20Gi",
            "ephemeralStoragePerInputGb": "2Gi",
            "gpu": 1,
        }
    }


def test_definition_document_omits_resources_when_absent() -> None:
    app = AppDefinition.model_validate(_app_dict())
    doc = app.definition_document
    assert doc["resources"] is None
