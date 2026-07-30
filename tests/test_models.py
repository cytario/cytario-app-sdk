"""Tests for cytario_app_sdk.models.AppDefinition."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from cytario_app_sdk.models import AppDefinition, DataRole, ImageRef, ParameterField


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
    assert app.consumer_groups == ["cellbio-team"]
    assert app.maintainer_groups == ["imaging-platform"]


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
    assert doc["consumerGroups"] == ["cellbio-team"]
    assert doc["maintainerGroups"] == ["imaging-platform"]
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
