"""Tests for the CYTARIO_PARAMETERS → --<name> flag translation (SDS-CY-080302)."""

from __future__ import annotations

import json
import logging
from pathlib import Path

from cytario_app_sdk.runtime.params import (
    PARAMETERS_ENV_VAR,
    load_parameters_from_env,
    parameters_to_flags,
    resolve_file_parameters,
)


class TestParametersToFlags:
    def test_empty_mapping_yields_no_flags(self) -> None:
        assert parameters_to_flags({}) == []

    def test_scalar_string_emits_flag_and_value(self) -> None:
        assert parameters_to_flags({"diameter": "30"}) == ["--diameter", "30"]

    def test_scalar_int_emits_flag_and_str_value(self) -> None:
        assert parameters_to_flags({"diameter": 30}) == ["--diameter", "30"]

    def test_scalar_float_emits_flag_and_str_value(self) -> None:
        assert parameters_to_flags({"threshold": 0.5}) == ["--threshold", "0.5"]

    def test_boolean_true_emits_bare_flag(self) -> None:
        assert parameters_to_flags({"normalize": True}) == ["--normalize"]

    def test_boolean_false_is_omitted(self) -> None:
        assert parameters_to_flags({"normalize": False}) == []

    def test_insertion_order_preserved(self) -> None:
        flags = parameters_to_flags(
            {"diameter": 30, "model": "cyto3", "channels": "0,0"},
        )
        assert flags == ["--diameter", "30", "--model", "cyto3", "--channels", "0,0"]

    def test_mixed_types(self) -> None:
        flags = parameters_to_flags(
            {"diameter": 30, "normalize": True, "skip": False, "model": "cyto3"},
        )
        assert flags == ["--diameter", "30", "--normalize", "--model", "cyto3"]


class TestLoadParametersFromEnv:
    def test_missing_env_returns_empty(self, monkeypatch) -> None:
        monkeypatch.delenv(PARAMETERS_ENV_VAR, raising=False)
        assert load_parameters_from_env() == {}

    def test_empty_env_returns_empty(self, monkeypatch) -> None:
        monkeypatch.setenv(PARAMETERS_ENV_VAR, "")
        assert load_parameters_from_env() == {}

    def test_whitespace_env_returns_empty(self, monkeypatch) -> None:
        monkeypatch.setenv(PARAMETERS_ENV_VAR, "   ")
        assert load_parameters_from_env() == {}

    def test_valid_object_returned(self, monkeypatch) -> None:
        params = {"diameter": 30, "model": "cyto3"}
        monkeypatch.setenv(PARAMETERS_ENV_VAR, json.dumps(params))
        assert load_parameters_from_env() == params

    def test_invalid_json_returns_empty_with_warning(
        self,
        monkeypatch,
        caplog,
    ) -> None:
        monkeypatch.setenv(PARAMETERS_ENV_VAR, "{not json")
        with caplog.at_level(logging.WARNING, logger="cytario_app_sdk.runtime.params"):
            result = load_parameters_from_env()
        assert result == {}
        assert PARAMETERS_ENV_VAR in caplog.text

    def test_non_object_json_returns_empty_with_warning(
        self,
        monkeypatch,
        caplog,
    ) -> None:
        monkeypatch.setenv(PARAMETERS_ENV_VAR, '["diameter", 30]')
        with caplog.at_level(logging.WARNING, logger="cytario_app_sdk.runtime.params"):
            result = load_parameters_from_env()
        assert result == {}
        assert PARAMETERS_ENV_VAR in caplog.text

    def test_order_preserved_from_env(self, monkeypatch) -> None:
        params = {"diameter": 30, "model": "cyto3", "channels": "0,0"}
        monkeypatch.setenv(PARAMETERS_ENV_VAR, json.dumps(params))
        loaded = load_parameters_from_env()
        assert list(loaded.keys()) == ["diameter", "model", "channels"]


class TestResolveFileParameters:
    """C-478 (SRS-CY-414110): a file parameter's s3:// value → local path."""

    def test_matched_uri_replaced_by_local_path(self, tmp_path) -> None:
        local = tmp_path / "in" / "cfg.yaml"
        resolved = resolve_file_parameters(
            {"pipelineConfig": "s3://bucket/cfg.yaml", "threshold": 0.5},
            ["s3://bucket/cfg.yaml"],
            [local],
        )
        assert resolved == {"pipelineConfig": str(local), "threshold": 0.5}

    def test_non_matching_string_value_untouched(self, tmp_path) -> None:
        resolved = resolve_file_parameters(
            {"model": "cyto3", "note": "s3://other/x"},
            ["s3://bucket/cfg.yaml"],
            [tmp_path / "in" / "cfg.yaml"],
        )
        assert resolved == {"model": "cyto3", "note": "s3://other/x"}

    def test_booleans_and_scalars_untouched(self, tmp_path) -> None:
        resolved = resolve_file_parameters(
            {"normalize": True, "skip": False, "diameter": 30},
            ["s3://bucket/cfg.yaml"],
            [tmp_path / "in" / "cfg.yaml"],
        )
        assert resolved == {"normalize": True, "skip": False, "diameter": 30}

    def test_empty_parameters_returns_empty(self) -> None:
        assert resolve_file_parameters({}, [], []) == {}

    def test_expanded_input_does_not_break_resolution(self, tmp_path) -> None:
        """C-622: a folder input expanding beside a file parameter must not raise.

        The old implementation asserted a 1:1 URI→path alignment and failed the
        whole job with "input URIs (2) and downloaded paths (12) do not align"
        as soon as the image input expanded to a tree of companion objects.
        """
        config = "s3://bucket/cfg.yaml"
        image = "s3://bucket/cases/case1/image.czi"
        resolved = resolve_file_parameters(
            {"pipelineConfig": config, "depth16": True},
            [image, config],
            [Path(f"/in/part{i}.tif") for i in range(11)] + [tmp_path / "in" / "cfg.yaml"],
        )
        assert resolved["pipelineConfig"] == str(tmp_path / "in" / "cfg.yaml")
        assert resolved["depth16"] is True

    def test_no_file_parameter_with_expanded_input_is_untouched(self) -> None:
        """Unrelated scalar/boolean params never gate resolution (C-622)."""
        params = {"depth16": True, "object-format": "parquet"}
        downloaded = [Path("/in/a"), Path("/in/b")]
        assert resolve_file_parameters(params, ["s3://bucket/img.czi"], downloaded) == params

    def test_by_source_mapping_resolves_exactly(self, tmp_path) -> None:
        """The by-source form resolves a file param whatever its siblings did."""
        config = "s3://bucket/cfg.yaml"
        image = "s3://bucket/case/img.czi"
        downloaded = {
            image: [Path("/in/img.czi"), Path("/in/img.czi/output/log.txt")],
            config: [tmp_path / "in" / "cfg.yaml"],
        }
        resolved = resolve_file_parameters({"pipelineConfig": config}, [image, config], downloaded)
        assert resolved["pipelineConfig"] == str(tmp_path / "in" / "cfg.yaml")

    def test_unresolvable_uri_left_unchanged(self) -> None:
        """A value naming an object that was not downloaded is not rewritten."""
        resolved = resolve_file_parameters(
            {"note": "s3://bucket/not-downloaded.yaml"},
            ["s3://bucket/img.czi"],
            [Path("/in/img.czi")],
        )
        assert resolved["note"] == "s3://bucket/not-downloaded.yaml"
