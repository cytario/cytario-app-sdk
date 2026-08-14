"""CLI-level tests for ``run`` flag injection (SDS-CY-080302).

Exercises the wiring in :mod:`cytario_app_sdk.cli` — that ``CYTARIO_PARAMETERS``
is read from the environment and appended to the algorithm command as
``--<name> <value>`` flags before ``run_job`` is called. The broker and boto3
session are stubbed so the flag-injection path is reached without network or
credentials; the translation primitives themselves are covered in
``tests/runtime/test_params.py``.
"""

from __future__ import annotations

import json
import os
from typing import Any

import pytest
from typer.testing import CliRunner

import cytario_app_sdk.broker as broker_mod
import cytario_app_sdk.runtime as runtime_mod
from cytario_app_sdk.cli import app

RUNNER = CliRunner()


@pytest.fixture
def captured_command(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Stub the broker + boto3 + run_job so ``run`` reaches the spawn boundary.

    Returns a dict whose ``command`` key receives the argv passed to ``run_job``.
    """
    captured: dict[str, Any] = {}

    class _StubBroker:
        @classmethod
        def from_env(cls, **_: Any) -> _StubBroker:
            return cls()

    class _StubSession:
        def client(self, _name: str) -> None:
            return None

    def _stub_run_job(_s3: Any, **kwargs: Any) -> int:
        captured["command"] = kwargs["command"]
        return 0

    # broker_boto3_session and run_job are lazy-imported from the runtime/broker
    # modules at call time, so patch them on their owning modules.
    monkeypatch.setattr(broker_mod, "BrokerClient", _StubBroker)
    monkeypatch.setattr(broker_mod, "broker_boto3_session", lambda *_args, **_kwargs: _StubSession())
    monkeypatch.setattr(runtime_mod, "run_job", _stub_run_job)
    return captured


def _invoke_run(extra_env: dict[str, str] | None = None) -> Any:
    old = os.environ.copy()
    if extra_env:
        os.environ.update(extra_env)
    try:
        return RUNNER.invoke(app, ["run", "--", "python", "/app/segment.py"])
    finally:
        os.environ.clear()
        os.environ.update(old)


def test_run_without_parameters_passes_command_unchanged(
    captured_command: dict[str, Any],
) -> None:
    result = _invoke_run()
    assert result.exit_code == 0, result.output
    assert captured_command["command"] == ["python", "/app/segment.py"]


def test_run_appends_scalar_flags_in_order(
    captured_command: dict[str, Any],
) -> None:
    result = _invoke_run(
        {"CYTARIO_PARAMETERS": json.dumps({"diameter": 30, "model": "cyto3", "channels": "0,0"})},
    )
    assert result.exit_code == 0, result.output
    assert captured_command["command"] == [
        "python",
        "/app/segment.py",
        "--diameter",
        "30",
        "--model",
        "cyto3",
        "--channels",
        "0,0",
    ]


def test_run_renders_booleans_and_omits_false(
    captured_command: dict[str, Any],
) -> None:
    result = _invoke_run(
        {"CYTARIO_PARAMETERS": json.dumps({"normalize": True, "skip": False, "diameter": 30})},
    )
    assert result.exit_code == 0, result.output
    assert captured_command["command"] == [
        "python",
        "/app/segment.py",
        "--normalize",
        "--diameter",
        "30",
    ]


def test_run_with_invalid_parameters_json_still_runs(
    captured_command: dict[str, Any],
) -> None:
    # Invalid JSON must not crash the job; the algorithm runs with no flags.
    result = _invoke_run({"CYTARIO_PARAMETERS": "{not json"})
    assert result.exit_code == 0, result.output
    assert captured_command["command"] == ["python", "/app/segment.py"]
