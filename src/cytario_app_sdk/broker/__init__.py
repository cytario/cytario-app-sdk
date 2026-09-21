"""Credential-broker sub-package (SRS-CY-416102).

Exposes :class:`BrokerClient` for obtaining short-lived STS storage
credentials from the Cytario broker endpoint, and :func:`config_from_env`
for reading the standard container environment.
"""

from __future__ import annotations

from cytario_app_sdk.broker.aws import broker_boto3_session
from cytario_app_sdk.broker.client import BrokerClient, BrokerCredentials
from cytario_app_sdk.broker.env import (
    DEFAULT_PROVIDER,
    PROVIDER_BINDINGS,
    BrokerConfig,
    ProviderBinding,
    config_from_env,
    get_provider_binding,
)
from cytario_app_sdk.broker.exceptions import (
    BrokerConfigError,
    BrokerError,
    BrokerProtocolError,
    BrokerUnreachable,
    GrantExpired,
    GrantRevoked,
)

__all__ = [
    "DEFAULT_PROVIDER",
    "PROVIDER_BINDINGS",
    "BrokerClient",
    "BrokerConfig",
    "BrokerConfigError",
    "BrokerCredentials",
    "BrokerError",
    "BrokerProtocolError",
    "BrokerUnreachable",
    "GrantExpired",
    "GrantRevoked",
    "ProviderBinding",
    "broker_boto3_session",
    "config_from_env",
    "get_provider_binding",
]
