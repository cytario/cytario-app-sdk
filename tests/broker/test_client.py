"""Tests for the credential-broker client (SRS-CY-416102, SRS-CY-416110).

HTTP is mocked with ``pytest-httpx`` to match the rest of the suite. The
broker's response shape is the documented
``{accessKeyId, secretAccessKey, sessionToken, expiration}`` (ISO-8601 with
``Z`` suffix). Expirations are constructed relative to ``now`` so the
refresh-margin logic is exercised deterministically.

The presented token is a per-job opaque session token (C-604): the mint
body is exactly ``{"token": ...}`` (no ``jobId`` — no request field may
scope a mint, SRS-CY-416110), and the client holds no rotation state.
"""

from __future__ import annotations

import json
import threading
from datetime import datetime, timedelta, timezone

import httpx
import pytest

from cytario_app_sdk.broker import (
    BrokerClient,
    BrokerConfig,
    BrokerConfigError,
    BrokerCredentials,
    BrokerProtocolError,
    BrokerUnreachable,
    GrantExpired,
    GrantRevoked,
    config_from_env,
    get_provider_binding,
)
from cytario_app_sdk.broker import env as env_module

BROKER_URL = "https://app.example.com/api/plugin/broker"
TOKEN = "job-scoped-grant-token"
JOB_ID = "batch-job-123"


def _config() -> BrokerConfig:
    return BrokerConfig(endpoint=BROKER_URL, token=TOKEN, job_id=JOB_ID)


def _mint_response(
    *,
    expires_in: timedelta = timedelta(hours=1),
    access_key_id: str = "ASIA",
    secret: str = "secret",
    session: str = "session",
    refresh_token: str | None = None,
) -> dict[str, str]:
    """Build the broker's JSON response body with a future expiration.

    ``refresh_token`` simulates an older host that still includes a
    ``refreshToken`` field in the response body (rolling-deploy forward
    compatibility, C-604): the client must ignore it.
    """
    response: dict[str, str] = {
        "accessKeyId": access_key_id,
        "secretAccessKey": secret,
        "sessionToken": session,
        "expiration": (datetime.now(timezone.utc) + expires_in).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    if refresh_token is not None:
        response["refreshToken"] = refresh_token
    return response


def test_credentials_mints_on_first_call(httpx_mock: pytest.FuncFixture) -> None:
    """The first call mints; no prior cache."""
    httpx_mock.add_response(
        method="POST",
        url=BROKER_URL,
        status_code=200,
        json=_mint_response(access_key_id="ASIA-1"),
    )
    client = BrokerClient(_config())
    creds = client.credentials()
    assert creds.access_key_id == "ASIA-1"
    assert creds.secret_access_key == "secret"
    assert creds.session_token == "session"
    assert creds.expiration > datetime.now(timezone.utc)


def test_credentials_returns_cache_when_fresh(httpx_mock: pytest.FuncFixture) -> None:
    """A second call within the refresh margin returns the cached creds without a new HTTP call."""
    httpx_mock.add_response(
        method="POST",
        url=BROKER_URL,
        status_code=200,
        json=_mint_response(access_key_id="ASIA-1", expires_in=timedelta(hours=1)),
    )
    client = BrokerClient(_config())
    first = client.credentials()
    # No second mock registered — a second HTTP call would fail the test.
    second = client.credentials()
    assert second is first
    assert second.access_key_id == "ASIA-1"


def test_credentials_refreshes_when_within_margin(httpx_mock: pytest.FuncFixture) -> None:
    """Cached creds expiring within refresh_margin trigger a fresh mint."""
    # First mint: expires in 4 minutes (within the 5-minute default margin).
    httpx_mock.add_response(
        method="POST",
        url=BROKER_URL,
        status_code=200,
        json=_mint_response(access_key_id="ASIA-1", expires_in=timedelta(minutes=4)),
    )
    # Second mint: expires in 1 hour.
    httpx_mock.add_response(
        method="POST",
        url=BROKER_URL,
        status_code=200,
        json=_mint_response(access_key_id="ASIA-2", expires_in=timedelta(hours=1)),
    )
    client = BrokerClient(_config())
    first = client.credentials()
    assert first.access_key_id == "ASIA-1"
    # The cache is stale (within margin) → a new mint happens.
    second = client.credentials()
    assert second.access_key_id == "ASIA-2"
    assert second is not first


def test_refresh_forces_a_fresh_mint_even_when_cache_is_fresh(
    httpx_mock: pytest.FuncFixture,
) -> None:
    """``refresh`` discards the cache and mints unconditionally."""
    httpx_mock.add_response(
        method="POST",
        url=BROKER_URL,
        status_code=200,
        json=_mint_response(access_key_id="ASIA-1", expires_in=timedelta(hours=1)),
    )
    httpx_mock.add_response(
        method="POST",
        url=BROKER_URL,
        status_code=200,
        json=_mint_response(access_key_id="ASIA-2", expires_in=timedelta(hours=1)),
    )
    client = BrokerClient(_config())
    first = client.credentials()
    assert first.access_key_id == "ASIA-1"
    forced = client.refresh()
    assert forced.access_key_id == "ASIA-2"
    assert forced is not first


def test_grant_revoked_on_403(httpx_mock: pytest.FuncFixture) -> None:
    """A 403 from the broker means the ledger row was removed (cancel/terminal)."""
    httpx_mock.add_response(
        method="POST",
        url=BROKER_URL,
        status_code=403,
        text="No active job binding for this token.",
    )
    client = BrokerClient(_config())
    with pytest.raises(GrantRevoked, match="revoked"):
        client.credentials()


def test_grant_expired_on_401(httpx_mock: pytest.FuncFixture) -> None:
    """A 401 from the broker means the grant is past the realm max offline-session validity."""
    httpx_mock.add_response(
        method="POST",
        url=BROKER_URL,
        status_code=401,
        text="token expired",
    )
    client = BrokerClient(_config())
    with pytest.raises(GrantExpired, match="expired"):
        client.credentials()


def test_protocol_error_on_5xx(httpx_mock: pytest.FuncFixture) -> None:
    """A 5xx is a broker bug / version skew, surfaced as BrokerProtocolError."""
    httpx_mock.add_response(
        method="POST",
        url=BROKER_URL,
        status_code=500,
        text="internal broker error",
    )
    client = BrokerClient(_config())
    with pytest.raises(BrokerProtocolError) as exc_info:
        client.credentials()
    assert exc_info.value.status_code == 500
    assert "internal broker error" in exc_info.value.body


def test_protocol_error_on_missing_field(httpx_mock: pytest.FuncFixture) -> None:
    """A 200 missing a required key is a protocol error, not a success."""
    httpx_mock.add_response(
        method="POST",
        url=BROKER_URL,
        status_code=200,
        json={"accessKeyId": "ASIA", "secretAccessKey": "x", "sessionToken": "y"},  # no expiration
    )
    client = BrokerClient(_config())
    with pytest.raises(BrokerProtocolError, match="missing required field"):
        client.credentials()


def test_protocol_error_on_non_json_body(httpx_mock: pytest.FuncFixture) -> None:
    """A 200 with a non-JSON body is a protocol error."""
    httpx_mock.add_response(
        method="POST",
        url=BROKER_URL,
        status_code=200,
        text="<html>oops</html>",
        headers={"Content-Type": "text/html"},
    )
    client = BrokerClient(_config())
    with pytest.raises(BrokerProtocolError, match="non-JSON"):
        client.credentials()


def test_unreachable_on_network_error() -> None:
    """A transport failure is wrapped as BrokerUnreachable."""

    def _raise(request: httpx.Request) -> httpx.Response:
        msg = "connection refused"
        raise httpx.ConnectError(msg)

    transport = httpx.MockTransport(_raise)
    http_client = httpx.Client(transport=transport)
    client = BrokerClient(_config(), http_client=http_client)
    with pytest.raises(BrokerUnreachable, match="unreachable"):
        client.credentials()


def test_expiration_with_z_suffix_parses_as_utc(httpx_mock: pytest.FuncFixture) -> None:
    """The broker emits ISO-8601 with a Z suffix; the client parses it as UTC."""
    # Use a fixed timestamp so the assertion is exact.
    httpx_mock.add_response(
        method="POST",
        url=BROKER_URL,
        status_code=200,
        json={
            "accessKeyId": "ASIA",
            "secretAccessKey": "s",
            "sessionToken": "t",
            "expiration": "2030-01-01T00:00:00Z",
        },
    )
    client = BrokerClient(_config())
    creds = client.credentials()
    assert creds.expiration == datetime(2030, 1, 1, 0, 0, 0, tzinfo=timezone.utc)


def test_concurrent_credentials_calls_share_one_mint(
    httpx_mock: pytest.FuncFixture,
) -> None:
    """Two threads calling credentials() concurrently trigger a single mint."""
    httpx_mock.add_response(
        method="POST",
        url=BROKER_URL,
        status_code=200,
        json=_mint_response(access_key_id="ASIA-1", expires_in=timedelta(hours=1)),
    )
    client = BrokerClient(_config())
    results: list[BrokerCredentials] = []
    barrier = threading.Barrier(2)

    def _call() -> None:
        barrier.wait()
        results.append(client.credentials())

    t1 = threading.Thread(target=_call)
    t2 = threading.Thread(target=_call)
    t1.start()
    t2.start()
    t1.join()
    t2.join()
    # Both threads got the same cached object → exactly one HTTP call happened.
    assert len(results) == 2
    assert results[0] is results[1]
    assert results[0].access_key_id == "ASIA-1"


def test_context_manager_closes_owned_http_client(
    httpx_mock: pytest.FuncFixture,
) -> None:
    """``with BrokerClient(...)`` closes an owned http client on exit."""
    httpx_mock.add_response(
        method="POST",
        url=BROKER_URL,
        status_code=200,
        json=_mint_response(),
    )
    client: BrokerClient
    with BrokerClient(_config()) as inner:
        inner.credentials()
        client = inner
    # After close, a forced refresh hits the closed owned http client.
    with pytest.raises(BrokerUnreachable, match="closed"):
        client.refresh()


def test_context_manager_does_not_close_caller_owned_client() -> None:
    """A caller-passed http_client is left open for the caller to manage."""
    caller_client = httpx.Client()
    try:
        with BrokerClient(_config(), http_client=caller_client):
            pass
        # The caller's client is still usable.
        assert not caller_client.is_closed
    finally:
        caller_client.close()


# --- mint request contract (SRS-CY-416110) -----------------------------------


def test_mint_posts_token_only_to_broker(
    httpx_mock: pytest.FuncFixture,
) -> None:
    """The broker request body is exactly ``{"token": ...}`` — no ``jobId``.

    No request-body field may influence which job a mint is scoped to
    (SRS-CY-416110): the broker resolves the presented token to its job
    server-side, so the job id from the config must not be sent.
    """
    httpx_mock.add_response(
        method="POST",
        url=BROKER_URL,
        status_code=200,
        json=_mint_response(),
    )
    client = BrokerClient(_config())  # config carries a job id
    client.credentials()
    request = httpx_mock.get_requests()[0]
    body = json.loads(request.content)
    assert body == {"token": TOKEN}


def test_mint_omits_job_id_even_when_configured(
    httpx_mock: pytest.FuncFixture,
) -> None:
    """A non-empty job id on the config still never reaches the request body."""
    httpx_mock.add_response(
        method="POST",
        url=BROKER_URL,
        status_code=200,
        json=_mint_response(),
    )
    config = BrokerConfig(endpoint=BROKER_URL, token=TOKEN, job_id="provider-job-xyz")
    client = BrokerClient(config)
    client.credentials()
    request = httpx_mock.get_requests()[0]
    body = json.loads(request.content)
    assert set(body) == {"token"}


def test_opaque_token_is_presented_verbatim(
    httpx_mock: pytest.FuncFixture,
) -> None:
    """A non-JWT (opaque) token with dots and unusual bytes is sent as-is.

    The session token is opaque (SRS-CY-416110): the client must not parse,
    decode or validate it — a value that happens to look like a JWT (or like
    anything else) is presented exactly as read from the environment.
    """
    opaque = "not-a-jwt..not-base64!!\u00e9"
    httpx_mock.add_response(
        method="POST",
        url=BROKER_URL,
        status_code=200,
        json=_mint_response(),
    )
    config = BrokerConfig(endpoint=BROKER_URL, token=opaque, job_id="")
    client = BrokerClient(config)
    client.credentials()
    request = httpx_mock.get_requests()[0]
    body = json.loads(request.content)
    assert body == {"token": opaque}


def test_long_running_job_presents_same_token_on_every_mint(
    httpx_mock: pytest.FuncFixture,
) -> None:
    """Successive mints over a long job present the identical token.

    The per-job session token never rotates client-side (C-604): there is no
    in-memory rotation state, so the Nth mint presents the same token as the
    first — even when a response body carries a ``refreshToken`` field an
    older host might still send.
    """
    httpx_mock.add_response(
        method="POST",
        url=BROKER_URL,
        status_code=200,
        json=_mint_response(access_key_id="ASIA-1", refresh_token="rotated-legacy"),
    )
    for i in range(2, 5):
        httpx_mock.add_response(
            method="POST",
            url=BROKER_URL,
            status_code=200,
            json=_mint_response(access_key_id=f"ASIA-{i}", expires_in=timedelta(minutes=4)),
        )
    client = BrokerClient(_config())
    seen_keys: list[str] = []
    for _ in range(4):
        # Force a mint each time so the token presentation is observable.
        creds = client.refresh()
        seen_keys.append(creds.access_key_id)

    requests = httpx_mock.get_requests()
    assert len(requests) == 4
    assert seen_keys == ["ASIA-1", "ASIA-2", "ASIA-3", "ASIA-4"]
    for request in requests:
        assert json.loads(request.content) == {"token": TOKEN}


def test_refresh_token_field_in_response_is_ignored(
    httpx_mock: pytest.FuncFixture,
) -> None:
    """A ``refreshToken`` field in the body changes nothing (rolling deploy).

    An older host still sending ``refreshToken`` must not break the client
    (forward compatibility, C-604): the mint succeeds and the next mint
    still presents the original per-job session token — the field is
    ignored, there is no rotation state to update.
    """
    httpx_mock.add_response(
        method="POST",
        url=BROKER_URL,
        status_code=200,
        json=_mint_response(access_key_id="ASIA-1", refresh_token="legacy-rotated"),
    )
    httpx_mock.add_response(
        method="POST",
        url=BROKER_URL,
        status_code=200,
        json=_mint_response(access_key_id="ASIA-2", refresh_token="legacy-rotated-2"),
    )
    client = BrokerClient(_config())
    first = client.credentials()
    assert first.access_key_id == "ASIA-1"
    second = client.refresh()
    assert second.access_key_id == "ASIA-2"

    requests = httpx_mock.get_requests()
    assert len(requests) == 2
    first_body = json.loads(requests[0].content)
    second_body = json.loads(requests[1].content)
    assert first_body == {"token": TOKEN}
    assert second_body == {"token": TOKEN}


# --- env-based construction --------------------------------------------------


def test_from_env_builds_client_from_environment() -> None:
    """``from_env`` reads the required variables and constructs a client."""
    environ = {
        "CYTARIO_BROKER_ENDPOINT": BROKER_URL,
        "CYTARIO_BROKER_TOKEN": TOKEN,
        "AWS_BATCH_JOB_ID": JOB_ID,
    }
    client = BrokerClient.from_env(environ=environ)
    assert client.config.endpoint == BROKER_URL
    assert client.config.token == TOKEN
    assert client.config.job_id == JOB_ID


def test_from_env_works_without_job_id() -> None:
    """The job id is optional: the two ``CYTARIO_*`` variables suffice (C-604)."""
    environ = {
        "CYTARIO_BROKER_ENDPOINT": BROKER_URL,
        "CYTARIO_BROKER_TOKEN": TOKEN,
    }
    client = BrokerClient.from_env(environ=environ)
    assert client.config.endpoint == BROKER_URL
    assert client.config.token == TOKEN
    assert client.config.job_id == ""


def test_from_env_raises_on_missing_variable() -> None:
    """A missing required variable is a deployment defect, surfaced as BrokerConfigError."""
    with pytest.raises(BrokerConfigError, match="CYTARIO_BROKER_ENDPOINT"):
        BrokerClient.from_env(environ={})


def test_from_env_raises_on_empty_variable() -> None:
    """A present-but-empty variable is treated as missing."""
    environ = {
        "CYTARIO_BROKER_ENDPOINT": "  ",
        "CYTARIO_BROKER_TOKEN": TOKEN,
    }
    with pytest.raises(BrokerConfigError, match="CYTARIO_BROKER_ENDPOINT"):
        BrokerClient.from_env(environ=environ)


def test_config_from_env_lists_all_missing_variables_in_one_error() -> None:
    """When multiple variables are missing, the error names all of them."""
    with pytest.raises(BrokerConfigError) as exc_info:
        config_from_env(environ={})
    message = str(exc_info.value)
    assert "CYTARIO_BROKER_ENDPOINT" in message
    assert "CYTARIO_BROKER_TOKEN" in message
    assert "AWS_BATCH_JOB_ID" not in message


# --- provider-binding seam ---------------------------------------------------


def test_default_provider_binding_reads_aws_batch_job_id() -> None:
    """The default (AWS) binding correlates on ``AWS_BATCH_JOB_ID``."""
    binding = get_provider_binding()
    assert binding.name == "aws"
    assert binding.job_id_env_var == "AWS_BATCH_JOB_ID"


def test_config_from_env_reads_binding_job_id_var(monkeypatch: pytest.MonkeyPatch) -> None:
    """``config_from_env`` reads the job-id variable named by the binding.

    A future Kubernetes binding plugs in by adding one entry to
    ``PROVIDER_BINDINGS`` — no client change. The test registers a
    hypothetical binding at runtime rather than shipping a speculative
    one in the SDK: the seam is the lookup, not the (unknown) variable
    a real Kubernetes deployment would correlate on.
    """
    monkeypatch.setitem(env_module.PROVIDER_BINDINGS, "kubernetes", "KUBE_JOB_ID")
    config = config_from_env(
        environ={
            "CYTARIO_BROKER_ENDPOINT": BROKER_URL,
            "CYTARIO_BROKER_TOKEN": TOKEN,
            "KUBE_JOB_ID": "pod-job-9",
        },
        provider="kubernetes",
    )
    assert config.job_id == "pod-job-9"
    assert config.provider == "kubernetes"


def test_unknown_provider_binding_is_rejected() -> None:
    """Naming a provider the SDK does not know is a caller error, not a silent default."""
    with pytest.raises(ValueError, match="unknown provider binding"):
        config_from_env(
            environ={
                "CYTARIO_BROKER_ENDPOINT": BROKER_URL,
                "CYTARIO_BROKER_TOKEN": TOKEN,
            },
            provider="gcp",
        )
