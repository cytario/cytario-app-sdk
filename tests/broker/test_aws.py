"""Tests for the boto3.Session factory backed by the credential broker.

These tests verify the credential wiring — that the session's frozen
credentials match the broker's mint, that the refresh callback calls
``broker.refresh()``, and that the region is propagated. Full S3 I/O with
moto is exercised in step 3 (``test_sync``); here we stay at the credential
layer.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from cytario_app_sdk.broker import BrokerClient, BrokerConfig, broker_boto3_session
from cytario_app_sdk.broker.aws import _broker_refresh_dict, _format_expiry

BROKER_URL = "https://app.example.com/api/plugin/broker"
TOKEN = "job-scoped-grant-token"
JOB_ID = "batch-job-123"


def _config() -> BrokerConfig:
    return BrokerConfig(endpoint=BROKER_URL, token=TOKEN, job_id=JOB_ID)


def _mint_response(
    *,
    access_key_id: str = "ASIA-1",
    expires_in: timedelta = timedelta(hours=1),
) -> dict[str, str]:
    return {
        "accessKeyId": access_key_id,
        "secretAccessKey": "secret-1",
        "sessionToken": "session-1",
        "expiration": (datetime.now(timezone.utc) + expires_in).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }


def _broker_with_responses(
    httpx_mock: pytest.FuncFixture,
    responses: list[dict[str, str]],
) -> BrokerClient:
    """Register broker responses in order and return a client."""
    for resp in responses:
        httpx_mock.add_response(
            method="POST",
            url=BROKER_URL,
            status_code=200,
            json=resp,
        )
    return BrokerClient(_config())


def test_session_frozen_credentials_match_broker_mint(
    httpx_mock: pytest.FuncFixture,
) -> None:
    """The session's frozen credentials must match what the broker minted."""
    httpx_mock.add_response(
        method="POST",
        url=BROKER_URL,
        status_code=200,
        json=_mint_response(access_key_id="ASIA-1"),
    )
    broker = BrokerClient(_config())
    session = broker_boto3_session(broker)
    frozen = session.get_credentials().get_frozen_credentials()
    assert frozen.access_key == "ASIA-1"
    assert frozen.secret_key == "secret-1"
    assert frozen.token == "session-1"


def test_refresh_callback_calls_broker_refresh(
    httpx_mock: pytest.FuncFixture,
) -> None:
    """The botocore refresh callback mints a fresh set from the broker."""
    httpx_mock.add_response(
        method="POST",
        url=BROKER_URL,
        status_code=200,
        json=_mint_response(access_key_id="ASIA-1"),
    )
    httpx_mock.add_response(
        method="POST",
        url=BROKER_URL,
        status_code=200,
        json=_mint_response(access_key_id="ASIA-2"),
    )
    broker = BrokerClient(_config())
    session = broker_boto3_session(broker)
    creds = session.get_credentials()
    # Force a refresh via botocore's internal refresh callback.
    metadata = creds._refresh_using()
    assert metadata["access_key"] == "ASIA-2"
    assert metadata["secret_key"] == "secret-1"
    assert metadata["token"] == "session-1"
    assert "expiry_time" in metadata
    assert metadata["account_id"] is None


def test_region_name_is_propagated(httpx_mock: pytest.FuncFixture) -> None:
    """The region_name kwarg is set on the session."""
    httpx_mock.add_response(
        method="POST",
        url=BROKER_URL,
        status_code=200,
        json=_mint_response(),
    )
    broker = BrokerClient(_config())
    session = broker_boto3_session(broker, region_name="eu-central-1")
    assert session.region_name == "eu-central-1"


def test_region_defaults_to_none(httpx_mock: pytest.FuncFixture) -> None:
    """Without region_name, boto3's default resolution applies (no override)."""
    httpx_mock.add_response(
        method="POST",
        url=BROKER_URL,
        status_code=200,
        json=_mint_response(),
    )
    broker = BrokerClient(_config())
    session = broker_boto3_session(broker)
    # No explicit region set — boto3 falls back to its default chain.
    assert session.region_name is None


def test_initial_mint_failure_propagates(httpx_mock: pytest.FuncFixture) -> None:
    """If the initial mint fails, the error propagates (no silent fallback)."""
    httpx_mock.add_response(
        method="POST",
        url=BROKER_URL,
        status_code=403,
        text="No active job binding",
    )
    from cytario_app_sdk.broker import GrantRevoked

    broker = BrokerClient(_config())
    with pytest.raises(GrantRevoked, match="revoked"):
        broker_boto3_session(broker)


def test_format_expiry_handles_naive_datetime() -> None:
    """A naive datetime is treated as UTC and formatted with a Z suffix."""
    naive = datetime(2030, 1, 1, 0, 0, 0)
    formatted = _format_expiry(naive)
    assert formatted == "2030-01-01T00:00:00Z"


def test_format_expiry_handles_aware_datetime() -> None:
    """A timezone-aware datetime is converted to UTC and formatted with Z."""
    aware = datetime(2030, 1, 1, 2, 0, 0, tzinfo=timezone.utc)
    formatted = _format_expiry(aware)
    assert formatted == "2030-01-01T02:00:00Z"


def test_broker_refresh_dict_shape() -> None:
    """The refresh dict has the exact keys botocore's _set_from_data expects."""
    from cytario_app_sdk.broker import BrokerCredentials

    creds = BrokerCredentials(
        access_key_id="AKIA",
        secret_access_key="secret",
        session_token="session",
        expiration=datetime(2030, 1, 1, 0, 0, 0, tzinfo=timezone.utc),
    )
    d = _broker_refresh_dict(creds)
    assert set(d.keys()) == {"access_key", "secret_key", "token", "expiry_time", "account_id"}
    assert d["access_key"] == "AKIA"
    assert d["secret_key"] == "secret"
    assert d["token"] == "session"
    assert d["expiry_time"] == "2030-01-01T00:00:00Z"
    assert d["account_id"] is None


def test_session_can_create_s3_client(httpx_mock: pytest.FuncFixture) -> None:
    """The returned session can create a standard boto3 S3 client."""
    httpx_mock.add_response(
        method="POST",
        url=BROKER_URL,
        status_code=200,
        json=_mint_response(),
    )
    broker = BrokerClient(_config())
    session = broker_boto3_session(broker, region_name="us-east-1")
    s3 = session.client("s3")
    # The client exists and carries the right region.
    assert s3.meta.region_name == "us-east-1"


def test_boto3_refresh_picks_up_new_credentials(
    httpx_mock: pytest.FuncFixture,
) -> None:
    """After a botocore refresh, the frozen credentials reflect the new mint."""
    httpx_mock.add_response(
        method="POST",
        url=BROKER_URL,
        status_code=200,
        json=_mint_response(access_key_id="ASIA-1"),
    )
    httpx_mock.add_response(
        method="POST",
        url=BROKER_URL,
        status_code=200,
        json=_mint_response(access_key_id="ASIA-2"),
    )
    broker = BrokerClient(_config())
    session = broker_boto3_session(broker)
    creds = session.get_credentials()
    # Simulate botocore's mandatory refresh path (near-expiry).
    creds._protected_refresh(is_mandatory=True)
    frozen = creds.get_frozen_credentials()
    assert frozen.access_key == "ASIA-2"


def test_import_error_without_boto3(monkeypatch: pytest.MonkeyPatch) -> None:
    """A clear ImportError is raised when boto3 is not installed."""
    # Simulate boto3 not being installed by blocking the import.
    import builtins

    real_import = builtins.__import__

    def _block_boto3(name: str, *args: object, **kwargs: object) -> object:
        if name in ("boto3", "botocore", "botocore.session"):
            msg = "simulated: boto3 not installed"
            raise ImportError(msg)
        return real_import(name, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(builtins, "__import__", _block_boto3)
    broker = BrokerClient(_config())
    with pytest.raises(ImportError, match="cytario-app-sdk\\[runtime\\]"):
        broker_boto3_session(broker)
