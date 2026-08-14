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
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any

__all__ = ["load_parameters_from_env", "parameters_to_flags"]

_logger = logging.getLogger("cytario_app_sdk.runtime.params")

#: Environment variable carrying the user-validated application parameters as
#: a JSON object, injected by the compute plugin's Job Adapter (SDS-CY-080302).
PARAMETERS_ENV_VAR = "CYTARIO_PARAMETERS"


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
