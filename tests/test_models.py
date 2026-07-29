"""Tests for cytario_app_sdk.models.AppDefinition."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from cytario_app_sdk.models import AppDefinition, ImageRef, RoleBinding


def test_load_example_yaml(example_app_yaml: Path) -> None:
    raw = yaml.safe_load(example_app_yaml.read_text(encoding="utf-8"))
    app = AppDefinition.model_validate(raw)
    assert app.schema_version == 1
    assert app.name == "cellseg"
    assert app.display == "Cell Segmentation"
    assert app.image.repository == "cytario/cellseg"
    assert app.image.tag == "1.0.0"
    assert app.image.digest is None
    assert app.parameter_schema["properties"]["diameter"]["default"] == 30
    assert app.input_roles[0].media_types == ["image/tiff", "application/vnd.cytario.ome-tiff"]
    assert app.output_roles[0].name == "segmentation"
    assert app.groups.consumers == ["cellbio-team"]
    assert app.groups.maintainers == ["imaging-platform"]


def test_display_defaults_to_name() -> None:
    app = AppDefinition(
        name="cellseg",
        image=ImageRef(repository="cytario/cellseg", tag="1.0.0"),
    )
    assert app.display == "cellseg"


def test_image_ref_requires_exactly_one_of_tag_or_digest() -> None:
    with pytest.raises(ValidationError, match="exactly one of `tag` or `digest`"):
        ImageRef(repository="cytario/x", tag="1.0.0", digest="sha256:abc")
    with pytest.raises(ValidationError, match="exactly one of `tag` or `digest`"):
        ImageRef(repository="cytario/x")


def test_image_ref_rejects_uppercase_repository() -> None:
    with pytest.raises(ValidationError, match="lowercase"):
        ImageRef(repository="Cytario/X", tag="1.0.0")


def test_role_binding_requires_media_types() -> None:
    with pytest.raises(ValidationError, match="mediaTypes"):
        RoleBinding(name="image", mediaTypes=[])  # type: ignore[call-arg]


def test_name_rejects_whitespace() -> None:
    with pytest.raises(ValidationError, match="whitespace"):
        AppDefinition(
            name="cell seg",
            image=ImageRef(repository="cytario/x", tag="1.0.0"),
        )


def test_extra_fields_rejected() -> None:
    with pytest.raises(ValidationError, match="extra"):
        AppDefinition.model_validate(
            {
                "name": "cellseg",
                "image": {"repository": "cytario/x", "tag": "1.0.0"},
                "unknownField": 1,
            },
        )


def test_definition_document_is_pii_free(example_app_yaml: Path) -> None:
    raw = yaml.safe_load(example_app_yaml.read_text(encoding="utf-8"))
    app = AppDefinition.model_validate(raw)
    doc = app.definition_document
    # The catalog-discovery payload must not carry credentials or PII.
    assert "secret" not in doc
    assert "token" not in doc
    assert "user" not in doc
    assert doc["image"]["repository"] == "cytario/cellseg"
    assert doc["image"]["tag"] == "1.0.0"
    assert "digest" not in doc["image"]


def test_artifact_type_is_stable(example_app_yaml: Path) -> None:
    raw = yaml.safe_load(example_app_yaml.read_text(encoding="utf-8"))
    app = AppDefinition.model_validate(raw)
    assert app.artifact_type == "application/vnd.cytario.app-definition.v1+json"


def test_digest_image_ref_round_trips() -> None:
    app = AppDefinition(
        name="cellseg",
        image=ImageRef(repository="cytario/cellseg", digest="sha256:abc"),
    )
    doc = app.definition_document
    assert doc["image"]["digest"] == "sha256:abc"
    assert "tag" not in doc["image"]
