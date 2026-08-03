"""boto3.Session factory backed by the credential broker.

Provides :func:`broker_boto3_session`, a convenience that returns a
``boto3.Session`` whose :class:`~botocore.credentials.RefreshableCredentials`
refresh from the broker on every boto3 call — so the SDK's refresh-margin logic
and the broker's ledger-gated revocation (SRS-CY-416102(c)) flow transparently
into boto3 without the algorithm calling ``broker.credentials()`` manually.

boto3 is an optional dependency (``pip install cytario-app-sdk[runtime]``).
Library-mode consumers that bring their own boto3 can use
``broker.credentials()`` directly; this module is the zero-boilerplate path for
algorithms that just want a ready-to-use ``boto3.Session``.

Usage::

    from cytario_app_sdk.broker import BrokerClient, broker_boto3_session

    broker = BrokerClient.from_env()
    session = broker_boto3_session(broker)
    s3 = session.client("s3")
    s3.download_file("my-bucket", "key", "/local/path")

boto3's :class:`~botocore.credentials.RefreshableCredentials` calls the refresh
callback before every API request when the cached credentials are within the
advisory refresh window (default 15 min before expiry). The callback mints a
fresh set from the broker; if the broker returns 403 (ledger row removed) the
mint raises :class:`GrantRevoked`, which boto3 surfaces as a
:class:`~botocore.exceptions.ClientError` on the next call. This is the
expected behaviour: a cancelled or finished job cannot read/write storage.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import TYPE_CHECKING

from cytario_app_sdk.broker.client import BrokerClient, BrokerCredentials

if TYPE_CHECKING:
    import boto3
    from botocore.credentials import RefreshableCredentials

__all__ = ["broker_boto3_session"]


def _format_expiry(dt: datetime) -> str:
    """Format an expiration as ISO-8601 with a Z suffix (botocore convention)."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _broker_refresh_dict(creds: BrokerCredentials) -> dict[str, str | None]:
    """Translate BrokerCredentials into the dict shape botocore expects."""
    return {
        "access_key": creds.access_key_id,
        "secret_key": creds.secret_access_key,
        "token": creds.session_token,
        "expiry_time": _format_expiry(creds.expiration),
        "account_id": None,
    }


def broker_boto3_session(
    broker: BrokerClient,
    *,
    region_name: str | None = None,
) -> boto3.Session:
    """Build a ``boto3.Session`` whose credentials refresh from the broker.

    The session mints an initial set immediately (failing fast if the broker is
    unreachable or the grant has been revoked) and registers a refresh callback
    that :class:`~botocore.credentials.RefreshableCredentials` invokes before
    every boto3 API request when the cached credentials are near expiry. The
    callback calls :meth:`BrokerClient.refresh`, which forces a fresh mint from
    the broker — so boto3's own refresh logic is the driver, and the
    ``refresh_margin`` on the ``BrokerClient`` is the safety net.

    Args:
        broker: A :class:`BrokerClient` (typically built via ``from_env()``).
        region_name: Optional AWS region for the session. If ``None``, boto3
            falls back to its default resolution chain
            (``AWS_DEFAULT_REGION`` / config file).

    Returns:
        A :class:`boto3.Session` ready for ``session.client("s3")`` etc.

    Raises:
        ImportError: If ``boto3`` is not installed. Install the optional
            dependency with ``pip install cytario-app-sdk[runtime]``.
        GrantRevoked: The broker returned 403 on the initial mint — the job's
            ledger row was removed (cancel or terminal state).
        GrantExpired: The broker returned 401 — the grant is past the realm
            max offline-session validity.
        BrokerUnreachable: A network error prevented the initial mint.
        BrokerProtocolError: The broker returned an unexpected response.

    """
    try:
        import boto3
        import botocore.session
        from botocore.credentials import RefreshableCredentials
    except ImportError as exc:
        msg = (
            "boto3 is not installed; install the optional runtime dependency "
            "with `pip install cytario-app-sdk[runtime]`"
        )
        raise ImportError(msg) from exc

    initial = broker.credentials()

    def _refresh_using() -> dict[str, str | None]:
        return _broker_refresh_dict(broker.refresh())

    refreshable: RefreshableCredentials = RefreshableCredentials(
        access_key=initial.access_key_id,
        secret_key=initial.secret_access_key,
        token=initial.session_token,
        expiry_time=initial.expiration,
        refresh_using=_refresh_using,
        method="cytario-broker",
    )

    botocore_session = botocore.session.Session()
    botocore_session._credentials = refreshable
    if region_name:
        botocore_session.set_config_variable("region", region_name)

    return boto3.Session(botocore_session=botocore_session)
