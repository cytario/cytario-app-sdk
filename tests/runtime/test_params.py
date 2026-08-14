"""Tests for the CYTARIO_PARAMETERS → --<name> flag translation (SDS-CY-080302)."""

from __future__ import annotations

import json
import logging

from cytario_app_sdk.runtime.params import (
    PARAMETERS_ENV_VAR,
    load_parameters_from_env,
    parameters_to_flags,
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
