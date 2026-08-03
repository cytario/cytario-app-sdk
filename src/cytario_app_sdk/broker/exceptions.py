"""Exceptions raised by the credential-broker client.

The broker endpoint (SRS-CY-416102) mints short-lived STS storage credentials
for a running analysis container. Failures fall into a small number of
distinct, actionable categories, mapped onto typed exceptions so callers
(library or wrapper mode) can distinguish them.

All broker exceptions inherit from :class:`BrokerError`, which itself inherits
from :class:`~cytario_app_sdk.errors.AppSdkError` so the existing CLI error
handler still catches them.
"""

from __future__ import annotations

from cytario_app_sdk.errors import AppSdkError


class BrokerError(AppSdkError):
    """Base class for all broker-client errors."""


class BrokerConfigError(BrokerError):
    """Raised when required broker environment variables are missing or empty.

    The container must receive ``CYTARIO_BROKER_ENDPOINT``,
    ``CYTARIO_BROKER_TOKEN`` and ``AWS_BATCH_JOB_ID`` (the last is injected by
    AWS Batch itself). A missing variable is a deployment / image-config
    defect, not a transient condition — surfaced as a distinct error so a
    wrapper entrypoint can fail fast with a clear message before the algorithm
    starts.
    """


class GrantRevoked(BrokerError):
    """The broker rejected the token because the job's ledger row was removed.

    Returned as HTTP 403 (SRS-CY-416102(c)) when the running-jobs ledger no
    longer has a row for this ``jobId`` — either the user cancelled the job
    (SRS-CY-37406) or the reconciler removed the row after a terminal state
    (SRS-CY-416104/416106). Storage access has been withdrawn; the container
    should exit promptly, not retry.
    """


class GrantExpired(BrokerError):
    """The grant token is past the realm's maximum offline-session validity.

    Returned as HTTP 401 — the offline grant has aged past the absolute upper
    bound on refresh (SRS-CY-416104). The container cannot obtain fresh
    credentials; further broker calls will keep failing. A long-running job
    hitting this is the spec's accepted, risk-assessed limitation.
    """


class BrokerUnreachable(BrokerError):
    """A network error prevented the broker call from completing.

    Wraps :class:`httpx.HTTPError` (connect timeout, DNS, TLS, etc.). May be
    transient; callers may retry with backoff. Distinguished from
    :class:`BrokerProtocolError` so a retry loop can treat transport failures
    as retryable and 5xx/shape failures as bugs to surface.
    """


class BrokerProtocolError(BrokerError):
    """The broker returned an unexpected response (5xx or malformed body).

    A 4xx other than 401/403, a 5xx, or a 200 whose body is not the documented
    ``{accessKeyId, secretAccessKey, sessionToken, expiration}`` shape. These
    indicate a broker bug or version skew, not a credential-grant problem —
    surfaced with the status and body for diagnostics. Not expected to be
    retryable in general, though a transient 5xx is left to the caller's
    judgement.
    """

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        body: str = "",
    ) -> None:
        """Store the HTTP status code and response body for diagnostics."""
        super().__init__(message)
        self.status_code = status_code
        self.body = body


__all__ = [
    "BrokerConfigError",
    "BrokerError",
    "BrokerProtocolError",
    "BrokerUnreachable",
    "GrantExpired",
    "GrantRevoked",
]
