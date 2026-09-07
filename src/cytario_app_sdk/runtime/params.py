"""Application-parameter → algorithm-flag translation (SDS-CY-080302).

The compute plugin delivers the user-validated application parameters to a
running analysis container as a single ``CYTARIO_PARAMETERS`` environment
variable holding a JSON object keyed by the application definition's parameter
names. The wrapper-mode runtime translates that object into ``--<name>
<value>`` flags appended to the algorithm argv before spawning it, so the
algorithm image can expose a plain CLI (e.g. a Typer app) whose flag names
match the parameter names in its app-definition.

Contract:

* boolean ``true``  → the bare flag ``--<name>``
* boolean ``false`` → omitted (the algorithm default applies)
* scalar (string/number) → ``--<name>`` followed by ``str(value)``
* object keys are emitted in JSON insertion order (Python dicts preserve it)
* an empty object (or a missing/invalid ``CYTARIO_PARAMETERS`` env var)
  yields no flags, so an image predating this contract runs with its CMD
  defaults unchanged.
* a ``file``-type parameter (C-478, SRS-CY-414110) arrives as an ``s3://``
  URI that also rides ``CYTARIO_INPUT_URIS``; :func:`resolve_file_parameters`
  replaces it with the downloaded object's local path before the flags are
  built, so the algorithm receives ``--<name> <local path>``.
"""

from __future__ import annotations

import json
import logging
import os
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from pathlib import Path

__all__ = ["load_parameters_from_env", "parameters_to_flags", "resolve_file_parameters"]

_logger = logging.getLogger("cytario_app_sdk.runtime.params")

#: Environment variable carrying the user-validated application parameters as
#: a JSON object, injected by the compute plugin's Job Adapter (SDS-CY-080302).
PARAMETERS_ENV_VAR = "CYTARIO_PARAMETERS"

#: Environment variable carrying the job's input ``s3://`` URIs as a JSON
#: array, injected by the compute plugin's Job Adapter.
INPUT_URIS_ENV_VAR = "CYTARIO_INPUT_URIS"


def parameters_to_flags(parameters: dict[str, Any]) -> list[str]:
    """Translate a parameters object into ``--<name> <value>`` flag tokens.

    Args:
        parameters: The user-validated application parameters keyed by their
            app-definition name. Insertion order is preserved.

    Returns:
        A flat argv fragment list to append to the algorithm command. An
        empty mapping yields an empty list.

    """
    flags: list[str] = []
    for name, value in parameters.items():
        if isinstance(value, bool):
            if value:
                flags.append(f"--{name}")
            # false → omit (the algorithm default applies)
        else:
            flags.append(f"--{name}")
            flags.append(str(value))
    return flags


def load_parameters_from_env() -> dict[str, Any]:
    """Read and parse ``CYTARIO_PARAMETERS`` from the environment.

    Returns an empty dict when the variable is absent or empty. A value that
    is not a JSON object is logged as a warning and treated as empty so a
    malformed env var never crashes the job — the algorithm runs with its
    defaults rather than failing to spawn.
    """
    raw = os.environ.get(PARAMETERS_ENV_VAR, "").strip()
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        _logger.warning(
            "%s is not valid JSON (%s); running the algorithm with its defaults",
            PARAMETERS_ENV_VAR,
            exc,
        )
        return {}
    if not isinstance(parsed, dict):
        _logger.warning(
            "%s must be a JSON object, got %s; running the algorithm with its defaults",
            PARAMETERS_ENV_VAR,
            type(parsed).__name__,
        )
        return {}
    return parsed


def resolve_file_parameters(
    parameters: dict[str, Any],
    input_uris: list[str],
    downloaded: list[Path],
) -> dict[str, Any]:
    """Replace ``file``-parameter ``s3://`` values with local paths (C-478).

    The compute plugin resolves a ``file``-type parameter (SRS-CY-414110) to
    its ``s3://<bucket>/<key>`` URI, delivers it in ``CYTARIO_PARAMETERS``, and
    appends the same URI to ``CYTARIO_INPUT_URIS`` so the wrapper downloads
    it alongside the data-role inputs. The wrapper has no app-definition in
    the container, so a file parameter is identified deterministically: a
    parameter value that exactly equals an input URI **is** a file parameter.

    Args:
        parameters: The parsed ``CYTARIO_PARAMETERS`` object.
        input_uris: The source URIs passed to :func:`download_inputs`, in
            order.
        downloaded: The local paths written by :func:`download_inputs`, in
            order (its return value).

    Returns:
        A new parameters mapping with every matched URI value replaced by the
        downloaded object's local path.

    Raises:
        ValueError: the URIs and downloaded paths do not align — the file the
            algorithm needs cannot be located, so the run must fail loudly
            rather than pass a bogus ``s3://`` flag to the algorithm.

    """
    if len(input_uris) != len(downloaded):
        msg = f"input URIs ({len(input_uris)}) and downloaded paths ({len(downloaded)}) do not align"
        raise ValueError(msg)
    uri_to_path = dict(zip(input_uris, downloaded, strict=True))
    resolved: dict[str, Any] = {}
    for name, value in parameters.items():
        if isinstance(value, str) and value in uri_to_path:
            resolved[name] = str(uri_to_path[value])
        else:
            resolved[name] = value
    return resolved
