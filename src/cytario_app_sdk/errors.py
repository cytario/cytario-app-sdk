"""Domain exceptions for the cytario-app-sdk."""

from __future__ import annotations


class AppSdkError(Exception):
    """Base class for all SDK errors."""


class ConfigError(AppSdkError):
    """Raised when the connection config is missing or invalid."""


class AppDefinitionError(AppSdkError):
    """Raised when an app-definition YAML fails validation."""


class RegistryError(AppSdkError):
    """Raised when the OCI registry returns an unexpected response."""

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


__all__ = ["AppDefinitionError", "AppSdkError", "ConfigError", "RegistryError"]
