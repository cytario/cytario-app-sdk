"""Read broker configuration from the container environment.

The Cytario compute plugin injects two environment variables into each
running analysis container (SRS-CY-416101, SDS-CY-080403):

- ``CYTARIO_BROKER_ENDPOINT`` — the full cytario-web origin URL of the broker
  endpoint, e.g. ``https://app.example.com/api/broker``. A path-only
  value is unreachable from the container's network.
- ``CYTARIO_BROKER_TOKEN`` — the per-job opaque session token (SRS-CY-416110)
  bound at submission to exactly one job and its batch. It carries no claim,
  no scope and no expiry of its own; the broker resolves it server-side to
  the job's ledger row, which is the only thing that scopes a mint. The
  batch's OAuth material never enters the container.

The provider correlation variable — ``AWS_BATCH_JOB_ID`` (:data:`JOB_ID_ENV_VAR`) —
is optional informational metadata: the broker derives the job from the
token, never from the request body, so no request-body field influences
which job a mint is scoped to. It is named by a constant rather than
inlined so a non-AWS binding has one place to change; naming it as a
provider seam would be speculative until a second binding exists.

This module is the single site that reads those variables, so callers inject
a :class:`BrokerConfig` explicitly in tests rather than monkey-patching
``os.environ``.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import TYPE_CHECKING

from cytario_app_sdk.broker.exceptions import BrokerConfigError

if TYPE_CHECKING:
    from collections.abc import Mapping

#: The environment variable carrying the provider's job identifier in a
#: container. Correlation is informational only — the broker scopes a mint to
#: the job resolved from the presented token, not from anything in the request —
#: but the identifier is kept on :class:`BrokerConfig` for diagnostics.
JOB_ID_ENV_VAR = "AWS_BATCH_JOB_ID"


@dataclass(frozen=True)
class BrokerConfig:
    """Resolved broker configuration — endpoint, token, and optional job id.

    ``token`` is the per-job opaque session token (SRS-CY-416110): it is
    revocable individually via the job's ledger row and carries no expiry
    of its own. Treat it as sensitive: do not log the token. ``job_id``
    is optional informational metadata (empty when the provider correlation
    variable is absent); the broker never reads it.
    """

    endpoint: str
    token: str
    job_id: str = ""


def config_from_env(
    *,
    environ: Mapping[str, str] | None = None,
) -> BrokerConfig:
    """Build a :class:`BrokerConfig` from the process environment.

    Pass ``environ=`` explicitly in tests to avoid reading the real process
    environment. ``CYTARIO_BROKER_ENDPOINT`` and ``CYTARIO_BROKER_TOKEN``
    are required — a missing or empty one raises
    :class:`BrokerConfigError`, a deployment defect, not a transient
    condition. The provider job-id variable (:data:`JOB_ID_ENV_VAR`) is
    optional and defaults to ``""``: the broker derives the job from the
    token, so the correlation identifier is informational only.
    """
    src = environ if environ is not None else os.environ
    endpoint = (src.get("CYTARIO_BROKER_ENDPOINT") or "").strip()
    token = (src.get("CYTARIO_BROKER_TOKEN") or "").strip()
    missing = [
        name
        for name, val in (
            ("CYTARIO_BROKER_ENDPOINT", endpoint),
            ("CYTARIO_BROKER_TOKEN", token),
        )
        if not val
    ]
    if missing:
        joined = ", ".join(missing)
        msg = (
            f"missing required environment variable(s): {joined}; the Cytario "
            "compute plugin must inject CYTARIO_BROKER_ENDPOINT and "
            "CYTARIO_BROKER_TOKEN into the running container"
        )
        raise BrokerConfigError(msg)
    job_id = (src.get(JOB_ID_ENV_VAR) or "").strip()
    return BrokerConfig(endpoint=endpoint, token=token, job_id=job_id)


__all__ = [
    "JOB_ID_ENV_VAR",
    "BrokerConfig",
    "config_from_env",
]
