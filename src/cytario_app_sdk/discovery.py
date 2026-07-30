"""Catalog discovery: scan an OCI registry for registered Cytario apps.

The discovery flow mirrors the `register` push in reverse:

  1. `list_repositories` (optionally filtered by namespace) or a single `--repo`.
  2. For each repository: `list_tags` → `resolve_subject` per tag →
     `list_referrers(subject_digest, artifactType=app-definition)`.
  3. For each app-definition referrer: `pull_manifest` to read `layers[0].digest`,
     then `pull_blob` to fetch the app-definition JSON.
  4. `AppDefinition.model_validate_json(blob)` → the same model `register`
     accepts, so the catalog output is guaranteed to round-trip.

A scan produces a list of `CatalogApp` records: the `AppDefinition` plus
provenance (which repository, which subject digest, which referrer manifest
digest) so the caller can trace what it found.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

from cytario_app_sdk.models import AppDefinition

if TYPE_CHECKING:
    from cytario_app_sdk.oci.client import RegistryClient
    from cytario_app_sdk.oci.manifest import Descriptor

# The artifactType the register flow stamps onto app-definition manifests.
APP_DEFINITION_ARTIFACT_TYPE = "application/vnd.cytario.app-definition.v1+json"


class ProgressCallback(Protocol):
    """Progress callback invoked during scan: `fn(repo, tag=None)`."""

    def __call__(self, repository: str, tag: str | None = None) -> None: ...


@dataclass
class CatalogApp:
    """A discovered app-definition with its registry provenance."""

    definition: AppDefinition
    repository: str
    subject_descriptor: Descriptor
    referrer_manifest_digest: str
    tag: str | None = None


@dataclass
class ScanResult:
    """Aggregate result of a catalog scan."""

    apps: list[CatalogApp]
    skipped: list[str]
    errors: list[str]


def _scan_tag(
    client: RegistryClient,
    repository: str,
    tag: str,
) -> tuple[list[CatalogApp], str | None]:
    """Scan one tag for app-definition referrers.

    Returns `(apps, skip_reason)` where `skip_reason` is a human-readable
    string when the tag produced no apps (or None when apps were found).
    """
    try:
        subject = client.resolve_subject(repository, tag)
    except Exception as exc:  # noqa: BLE001
        return [], f"{repository}@{tag}: could not resolve subject: {exc}"
    try:
        referrers = client.list_referrers(
            repository,
            subject["digest"],
            artifact_type=APP_DEFINITION_ARTIFACT_TYPE,
        )
    except Exception as exc:  # noqa: BLE001
        return [], f"{repository}@{tag}: could not list referrers: {exc}"
    if not referrers:
        return [], f"{repository}@{tag}: no app-definition referrer"

    apps: list[CatalogApp] = []
    for referrer in referrers:
        try:
            manifest = client.pull_manifest(repository, referrer["digest"])
            layers = manifest.get("layers", [])
            if not layers:
                return [], f"{repository}@{tag}: referrer {referrer['digest'][:12]} has no layers"
            blob = client.pull_blob(repository, layers[0]["digest"])
            definition = AppDefinition.model_validate_json(blob)
        except Exception as exc:  # noqa: BLE001
            return [], f"{repository}@{tag}: could not parse app-definition: {exc}"
        apps.append(
            CatalogApp(
                definition=definition,
                repository=repository,
                subject_descriptor=subject,
                referrer_manifest_digest=referrer["digest"],
                tag=tag,
            )
        )
    return apps, None if apps else f"{repository}@{tag}: no app-definition referrer"


def scan_repository(
    client: RegistryClient,
    repository: str,
    *,
    progress: ProgressCallback | None = None,
) -> ScanResult:
    """Scan a single repository for app-definition referrers.

    Returns a `ScanResult`. Fatal registry errors (e.g. `list_tags` 401) land
    in `errors`; per-tag misses (no referrer, parse failure) land in `skipped`.
    """
    apps: list[CatalogApp] = []
    skipped: list[str] = []
    errors: list[str] = []

    try:
        tags = client.list_tags(repository)
    except Exception as exc:  # noqa: BLE001
        return ScanResult(apps=[], skipped=[], errors=[f"{repository}: could not list tags: {exc}"])

    if not tags:
        skipped.append(f"{repository}: no tags")
        return ScanResult(apps=apps, skipped=skipped, errors=errors)

    for tag in tags:
        if progress is not None:
            progress(repository, tag)
        tag_apps, skip_reason = _scan_tag(client, repository, tag)
        apps.extend(tag_apps)
        if skip_reason:
            skipped.append(skip_reason)
    return ScanResult(apps=apps, skipped=skipped, errors=errors)


def scan_namespace(
    client: RegistryClient,
    namespace: str | None = None,
    *,
    repositories: list[str] | None = None,
    progress: ProgressCallback | None = None,
) -> ScanResult:
    """Scan a namespace (or an explicit list of repositories) for apps.

    When `repositories` is given, scans exactly those (ignores `namespace`).
    Otherwise lists repositories from `_catalog`, optionally filtered by
    `namespace` prefix.
    """
    all_apps: list[CatalogApp] = []
    all_skipped: list[str] = []
    all_errors: list[str] = []

    if repositories is None:
        try:
            repositories = client.list_repositories(namespace=namespace)
        except Exception as exc:  # noqa: BLE001
            return ScanResult(apps=[], skipped=[], errors=[f"could not list repositories: {exc}"])

    for repo in repositories:
        if progress is not None:
            progress(repo)
        result = scan_repository(client, repo, progress=progress)
        all_apps.extend(result.apps)
        all_skipped.extend(result.skipped)
        all_errors.extend(result.errors)
    return ScanResult(apps=all_apps, skipped=all_skipped, errors=all_errors)


__all__ = [
    "APP_DEFINITION_ARTIFACT_TYPE",
    "CatalogApp",
    "ScanResult",
    "scan_namespace",
    "scan_repository",
]
