"""Read broker configuration from the container environment.

The Cytario compute plugin injects three environment variables into each
running analysis container (SRS-CY-416101):

- ``CYTARIO_BROKER_ENDPOINT`` — the full cytario-web origin URL of the broker
  endpoint, e.g. ``https://app.example.com/api/broker``. A path-only
  value is unreachable from the container's network.
- ``CYTARIO_BROKER_TOKEN`` — the job-scoped offline-capable grant token (a
  refresh token; the broker redeems it at the identity service on every
  call, SRS-CY-416102(a)) the broker validates against the running-jobs
  ledger (SRS-CY-416102(c)).
- ``AWS_BATCH_JOB_ID`` — the provider job identifier, injected by AWS Batch
  itself; the broker correlates it with the token's job-binding claim.

This module is the single site that reads those variables, so callers inject
a :class:`BrokerConfig` explicitly in tests rather than monkey-patching
``os.environ``.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from cytario_app_sdk.broker.exceptions import BrokerConfigError


@dataclass(frozen=True)
class BrokerConfig:
    """Resolved broker configuration — endpoint, token, and job id.

    Carries no secrets beyond the job-scoped grant token, which is short-lived
    (realm max offline-session validity) and ledger-revocable
    (SRS-CY-416104). Treat it as sensitive: do not log the token.
    """

    endpoint: str
    token: str
    job_id: str


def _require(name: str) -> str:
    """Read a required, non-empty environment variable."""
    value = os.environ.get(name, "").strip()
    if not value:
        msg = (
            f"missing required environment variable {name}; the Cytario compute "
            "plugin must inject CYTARIO_BROKER_ENDPOINT, CYTARIO_BROKER_TOKEN, "
            "and AWS_BATCH_JOB_ID into the running container"
        )
        raise BrokerConfigError(msg)
    return value


def config_from_env(
    *,
    environ: dict[str, str] | None = None,
) -> BrokerConfig:
    """Build a :class:`BrokerConfig` from the process environment.

    Pass ``environ=`` explicitly in tests to avoid reading the real process
    environment. A missing or empty variable raises :class:`BrokerConfigError`
    — a deployment defect, not a transient condition.
    """
    src = environ if environ is not None else os.environ
    endpoint = (src.get("CYTARIO_BROKER_ENDPOINT") or "").strip()
    token = (src.get("CYTARIO_BROKER_TOKEN") or "").strip()
    job_id = (src.get("AWS_BATCH_JOB_ID") or "").strip()
    missing = [
        name
        for name, val in (
            ("CYTARIO_BROKER_ENDPOINT", endpoint),
            ("CYTARIO_BROKER_TOKEN", token),
            ("AWS_BATCH_JOB_ID", job_id),
        )
        if not val
    ]
    if missing:
        joined = ", ".join(missing)
        msg = (
            f"missing required environment variable(s): {joined}; the Cytario "
            "compute plugin must inject CYTARIO_BROKER_ENDPOINT, "
            "CYTARIO_BROKER_TOKEN, and AWS_BATCH_JOB_ID into the running container"
        )
        raise BrokerConfigError(msg)
    return BrokerConfig(endpoint=endpoint, token=token, job_id=job_id)


__all__ = ["BrokerConfig", "config_from_env"]
