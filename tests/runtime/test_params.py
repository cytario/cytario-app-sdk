"""Tests for the CYTARIO_PARAMETERS → --<name> flag translation (SDS-CY-080302)."""

from __future__ import annotations

import json
import logging

import pytest

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

    def test_misaligned_lists_raise(self, tmp_path) -> None:
        with pytest.raises(ValueError, match="do not align"):
            resolve_file_parameters(
                {"pipelineConfig": "s3://bucket/cfg.yaml"},
                ["s3://bucket/cfg.yaml", "s3://bucket/other"],
                [tmp_path / "in" / "cfg.yaml"],
            )
