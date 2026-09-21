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

The provider correlation variable — ``AWS_BATCH_JOB_ID`` on the AWS Batch
binding — is optional informational metadata: the broker derives the job
from the token, never from the request body, so no request-body field
influences which job a mint is scoped to. Which variable carries it is
named by the provider binding (:data:`PROVIDER_BINDINGS`) rather than
hardcoded, so a future Kubernetes binding can correlate on a different
variable without touching the client; only the default AWS binding is
known today.

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

#: Provider bindings the SDK knows about: provider name → the environment
#: variable that carries the provider's job identifier in a container.
#: Correlation is informational only — the broker scopes a mint to the job
#: resolved from the presented token, not from anything in the request —
#: but the identifier is kept on :class:`BrokerConfig` for diagnostics.
#: The mapping is the whole "provider seam": adding a Kubernetes binding
#: later means adding one entry here, nothing else.
PROVIDER_BINDINGS: dict[str, str] = {
    "aws": "AWS_BATCH_JOB_ID",
}

#: The binding used when the caller does not name a provider.
DEFAULT_PROVIDER = "aws"


@dataclass(frozen=True)
class ProviderBinding:
    """Names the provider whose job-id correlation variable to read.

    Minimal on purpose: it exists so the AWS-named ``AWS_BATCH_JOB_ID``
    is a property of a binding rather than a hardcoded string in
    :func:`config_from_env`, letting a future non-AWS binding (e.g. a
    Kubernetes pod identifier) plug in without changing the broker client.
    It is not a plugin hook — there is no discovery, no registry, and no
    way for callers to introduce bindings the SDK does not know about.
    """

    name: str
    job_id_env_var: str


def get_provider_binding(provider: str = DEFAULT_PROVIDER) -> ProviderBinding:
    """Return the :class:`ProviderBinding` for a provider name.

    Raises :class:`KeyError`-derived :class:`BrokerConfigError`-adjacent
    behavior is intentionally avoided: unknown providers raise a plain
    ``KeyError`` because only the SDK itself should name providers.
    """
    try:
        env_var = PROVIDER_BINDINGS[provider]
    except KeyError as exc:
        msg = (
            f"unknown provider binding {provider!r}; known providers: {', '.join(sorted(PROVIDER_BINDINGS))}"
        )
        raise ValueError(msg) from exc
    return ProviderBinding(name=provider, job_id_env_var=env_var)


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
    provider: str = DEFAULT_PROVIDER


def config_from_env(
    *,
    environ: Mapping[str, str] | None = None,
    provider: str = DEFAULT_PROVIDER,
) -> BrokerConfig:
    """Build a :class:`BrokerConfig` from the process environment.

    Pass ``environ=`` explicitly in tests to avoid reading the real process
    environment. ``CYTARIO_BROKER_ENDPOINT`` and ``CYTARIO_BROKER_TOKEN``
    are required — a missing or empty one raises
    :class:`BrokerConfigError`, a deployment defect, not a transient
    condition. The provider job-id variable named by ``provider``'s binding
    (``AWS_BATCH_JOB_ID`` on the default AWS binding) is optional and
    defaults to ``""``: the broker derives the job from the token, so the
    correlation identifier is informational only.
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
    binding = get_provider_binding(provider)
    job_id = (src.get(binding.job_id_env_var) or "").strip()
    return BrokerConfig(endpoint=endpoint, token=token, job_id=job_id, provider=provider)


__all__ = [
    "DEFAULT_PROVIDER",
    "PROVIDER_BINDINGS",
    "BrokerConfig",
    "ProviderBinding",
    "config_from_env",
    "get_provider_binding",
]
