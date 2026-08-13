"""Credential-broker client (SRS-CY-416102).

A running analysis container calls the broker endpoint to obtain short-lived
STS storage credentials scoped to the submitting user's organization and the
job's validated output prefix (SRS-CY-416103). The broker validates the
job-scoped token against the running-jobs ledger (SRS-CY-416102(c)); a token
whose ledger row has been removed mints nothing.

This client is the single call site for that HTTP exchange. It is shared by
both modes the SDK supports:

- **Library mode** — the algorithm's Python code imports the SDK, calls
  :meth:`BrokerClient.credentials`, and uses boto3 directly with the returned
  keys. The SDK never touches S3.
- **Wrapper mode** — the SDK's ``run`` subcommand uses the client to keep
  fresh boto3 credentials for the download/upload phases around the
  algorithm's subprocess.

The minted STS credentials are short-lived (≤ 1 hour; SRS-CY-416103). The
grant token carried by :class:`BrokerClient` has a longer lifetime — the
realm's maximum offline-session validity (SRS-CY-416104) — and is the
authorization to *keep* minting. The grant is a **refresh token**: the
broker redeems it at the identity service on every call (SRS-CY-416102(a))
to obtain a fresh, unexpired access token for STS, so a job whose startup
outlives the access token's short ``exp`` still mints. When the realm
enables refresh-token rotation, the broker returns the rotated refresh
token and the client overwrites its in-memory token, so the next mint
presents the current (rotated) token; a replayed (leaked) refresh token
dies on the first legitimate refresh. The client refreshes the STS
credentials on demand, requesting a fresh mint from the broker whenever
the cached credentials would expire within ``refresh_margin``. A job
running longer than the realm max cannot refresh anymore; this is the
spec's accepted, risk-assessed limitation.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING

import httpx

from cytario_app_sdk.broker.env import BrokerConfig, config_from_env
from cytario_app_sdk.broker.exceptions import (
    BrokerProtocolError,
    BrokerUnreachable,
    GrantExpired,
    GrantRevoked,
)

if TYPE_CHECKING:
    from types import TracebackType

    from typing_extensions import Self


__all__ = ["BrokerClient", "BrokerCredentials"]


@dataclass(frozen=True)
class BrokerCredentials:
    """Short-lived STS credentials minted by the broker (SRS-CY-416103).

    Frozen so they can be cached and handed to boto3 without the caller
    worrying about mutation. Treat the secret fields as sensitive: do not
    log them.
    """

    access_key_id: str
    secret_access_key: str
    session_token: str
    expiration: datetime


def _parse_expiration(raw: str) -> datetime:
    """Parse the broker's ``expiration`` field into a timezone-aware datetime.

    The broker emits ISO-8601 with a ``Z`` suffix (AWS STS convention); accept
    either ``Z`` or an explicit offset. A naive datetime is treated as UTC.
    """
    candidate = raw.replace("Z", "+00:00") if raw.endswith("Z") else raw
    try:
        parsed = datetime.fromisoformat(candidate)
    except ValueError as exc:  # pragma: no cover - defensive; broker contract is ISO
        msg = f"broker returned a non-ISO expiration: {raw!r}"
        raise BrokerProtocolError(msg) from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


class BrokerClient:
    """Client for the Cytario credential-broker endpoint (SRS-CY-416102).

    Thread-safe. A background refresh thread (wrapper mode) and the main
    thread can both call :meth:`credentials` concurrently; the first caller
    to observe near-expiry triggers a single mint.

    The client owns no credential material at construction; the first
    :meth:`credentials` call (or an explicit :meth:`refresh`) mints on
    demand. Use :meth:`from_env` to build one from the standard container
    environment variables.
    """

    def __init__(
        self,
        config: BrokerConfig,
        *,
        refresh_margin: timedelta = timedelta(minutes=5),
        http_client: httpx.Client | None = None,
    ) -> None:
        """Construct a broker client.

        Args:
            config: Resolved broker configuration (endpoint, token, job id).
            refresh_margin: Minimum remaining lifetime below which cached
                credentials are considered stale and re-minted. Default 5 min
                — comfortably under the 1-hour STS ceiling, so a boto3 call
                triggered immediately after :meth:`credentials` returns has a
                full window to complete.
            http_client: Optional pre-configured ``httpx.Client`` for tests
                or custom transports (timeouts, retries, mTLS). The client
                closes an http client it owns on :meth:`close`; one passed
                in here is left for the caller to manage.

        """
        self._config = config
        # The grant token from the environment is a refresh token; the
        # broker refreshes it on every call and returns a rotated refresh
        # token. ``_refresh_token`` is the mutable rotation state — the
        # frozen ``BrokerConfig.token`` is only the initial value. A restart
        # mid-job loses this state, but an AWS Batch restart is a new job
        # (new grant), so the original env-var token is also stale then.
        self._refresh_token = config.token
        self._refresh_margin = refresh_margin
        self._http = http_client if http_client is not None else httpx.Client(timeout=httpx.Timeout(10.0))
        self._owns_http = http_client is None
        self._lock = threading.Lock()
        self._cached: BrokerCredentials | None = None

    @classmethod
    def from_env(
        cls,
        *,
        refresh_margin: timedelta = timedelta(minutes=5),
        environ: dict[str, str] | None = None,
        http_client: httpx.Client | None = None,
    ) -> BrokerClient:
        """Build a client from ``CYTARIO_BROKER_*`` and ``AWS_BATCH_JOB_ID``.

        See :func:`cytario_app_sdk.broker.env.config_from_env` for the
        environment contract. Raises :class:`BrokerConfigError` on a missing
        variable — a deployment defect, not a transient condition.
        """
        config = config_from_env(environ=environ)
        return cls(config, refresh_margin=refresh_margin, http_client=http_client)

    @property
    def config(self) -> BrokerConfig:
        """The resolved broker configuration (endpoint, token, job id)."""
        return self._config

    def credentials(self) -> BrokerCredentials:
        """Return cached credentials, minting or refreshing on demand.

        On first call, mints. On subsequent calls, returns the cache if it
        has at least ``refresh_margin`` of remaining life; otherwise mints a
        fresh set. Raises:

        - :class:`GrantRevoked` (broker 403) — the job's ledger row was
          removed (cancel or terminal state).
        - :class:`GrantExpired` (broker 401) — the grant is past the realm
          max offline-session validity (SRS-CY-416104).
        - :class:`BrokerUnreachable` — a network error prevented the call.
        - :class:`BrokerProtocolError` — 5xx or a malformed response body.
        """
        with self._lock:
            if self._cached is not None and self._is_fresh(self._cached):
                return self._cached
            self._cached = self._mint()
            return self._cached

    def refresh(self) -> BrokerCredentials:
        """Force a fresh mint, discarding any cache.

        Useful when a boto3 call has just failed with an expired-credential
        error and the caller wants to rule out a stale cache. Same error
        surface as :meth:`credentials`.
        """
        with self._lock:
            self._cached = self._mint()
            return self._cached

    def close(self) -> None:
        """Release the owned ``httpx.Client``, if any.

        Idempotent. A client-passed ``http_client`` is left for the caller to
        manage (the SDK never closes one it does not own).
        """
        if self._owns_http:
            self._http.close()

    def __enter__(self) -> Self:
        """Enter context: return self for use as a context manager."""
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        """Release the owned http client on context-manager exit."""
        self.close()

    # --- internals ---------------------------------------------------------

    def _is_fresh(self, creds: BrokerCredentials) -> bool:
        """Return True if cached creds outlive ``now + refresh_margin``."""
        return creds.expiration > datetime.now(timezone.utc) + self._refresh_margin

    def _mint(self) -> BrokerCredentials:
        """Call the broker and parse the response. Caller holds ``_lock``."""
        if self._http.is_closed:
            msg = "broker client is closed (http client released)"
            raise BrokerUnreachable(msg)
        body = {"token": self._refresh_token, "jobId": self._config.job_id}
        try:
            response = self._http.post(self._config.endpoint, json=body)
        except httpx.HTTPError as exc:
            msg = f"broker unreachable at {self._config.endpoint}: {exc}"
            raise BrokerUnreachable(msg) from exc

        if response.status_code == 403:
            msg = "broker revoked the grant (job cancelled or reached terminal state)"
            raise GrantRevoked(msg)
        if response.status_code == 401:
            msg = "grant token expired (past realm max offline-session validity)"
            raise GrantExpired(msg)
        if response.status_code >= 400:
            body_text = response.text
            msg = f"broker returned HTTP {response.status_code}"
            raise BrokerProtocolError(msg, status_code=response.status_code, body=body_text)

        try:
            payload = response.json()
        except ValueError as exc:
            msg = f"broker returned a non-JSON body: {response.text!r}"
            raise BrokerProtocolError(msg, status_code=response.status_code, body=response.text) from exc

        # Refresh-token rotation: the broker returns the rotated
        # refresh token so the next mint presents the current token. A
        # response without ``refreshToken`` (rotation off at the realm)
        # keeps the original token — backward compatible.
        new_refresh_token = payload.get("refreshToken")
        if isinstance(new_refresh_token, str) and new_refresh_token:
            self._refresh_token = new_refresh_token

        try:
            creds = BrokerCredentials(
                access_key_id=payload["accessKeyId"],
                secret_access_key=payload["secretAccessKey"],
                session_token=payload["sessionToken"],
                expiration=_parse_expiration(payload["expiration"]),
            )
        except (KeyError, TypeError) as exc:
            msg = f"broker response missing required field: {exc}"
            raise BrokerProtocolError(msg, status_code=response.status_code, body=response.text) from exc
        return creds
