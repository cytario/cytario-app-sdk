"""Typer CLI entry point.

Commands:
  register <app.yaml>   Attach an app-definition to a container image as an
                        OCI Referrer (artifactType
                        `application/vnd.cytario.app-definition.v1+json`).

Connection settings (registry, user, secret) can be passed on the command line
or via a YAML config file consumed by typer-config's `--config` option.

Usage:
  cytario-app-sdk --config conn.yaml register app.yaml
  cytario-app-sdk --registry https://harbor.example.com --user robot$cat \
      --secret <token> register app.yaml
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated

import typer
import yaml
from typer_config.decorators import use_yaml_config

from cytario_app_sdk import __version__
from cytario_app_sdk.errors import AppDefinitionError, AppSdkError, RegistryError
from cytario_app_sdk.models import AppDefinition
from cytario_app_sdk.oci import RegistryClient, build_app_definition_manifest
from cytario_app_sdk.oci.manifest import EMPTY_CONFIG_BYTES

app = typer.Typer(
    name="cytario-app-sdk",
    help="Register Cytario analysis applications in an OCI Distribution v1.1 registry.",
    no_args_is_help=True,
    add_completion=False,
)


@app.command()
def version() -> None:
    """Print the cytario-app-sdk version and exit."""
    typer.echo(__version__)


def _load_app_definition(path: Path) -> AppDefinition:
    if not path.is_file():
        msg = f"app-definition file not found: {path}"
        raise AppDefinitionError(msg)
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        msg = f"invalid YAML in {path}: {exc}"
        raise AppDefinitionError(msg) from exc
    if not isinstance(raw, dict):
        msg = f"app-definition {path} must be a mapping, got {type(raw).__name__}"
        raise AppDefinitionError(msg)
    try:
        return AppDefinition.model_validate(raw)
    except Exception as exc:
        msg = f"invalid app-definition in {path}: {exc}"
        raise AppDefinitionError(msg) from exc


def _connection_from(
    *,
    registry: str | None,
    user: str | None,
    secret: str | None,
) -> RegistryClient:
    """Build a RegistryClient from CLI flags (populated by typer-config from YAML).

    `@use_yaml_config()` injects a `--config` option and, when provided,
    populates `registry`/`user`/`secret` from the YAML before this runs.
    """
    if registry and user and secret:
        return RegistryClient(registry=registry, user=user, secret=secret)
    msg = "missing registry connection: provide --registry/--user/--secret or --config"
    raise typer.BadParameter(msg)


@app.command()
@use_yaml_config()
def register(
    app_yaml: Annotated[
        Path,
        typer.Argument(
            exists=True,
            dir_okay=False,
            readable=True,
            help="Path to the app-definition YAML.",
        ),
    ],
    registry: Annotated[
        str | None,
        typer.Option(
            "--registry",
            help="OCI registry base URL (e.g. https://harbor.example.com).",
        ),
    ] = None,
    user: Annotated[
        str | None,
        typer.Option("--user", help="Registry username (often a robot account)."),
    ] = None,
    secret: Annotated[
        str | None,
        typer.Option("--secret", help="Registry password / robot token."),
    ] = None,
    dry_run: Annotated[
        bool,
        typer.Option("--dry-run", help="Validate and print the planned manifest without pushing."),
    ] = False,
) -> None:
    """Register an app-definition as an OCI Referrer on its container image."""
    try:
        definition = _load_app_definition(app_yaml)
        image_ref = definition.image.tag or definition.image.digest
        if not image_ref:
            msg = "app-definition image has neither tag nor digest"
            raise AppDefinitionError(msg)

        typer.echo(
            f"registering app {definition.name!r} on {definition.image.repository}@{image_ref}",
        )

        if dry_run:
            typer.echo(
                "DRY RUN: would resolve subject, push definition blob, and push referrer manifest.",
            )
            typer.echo(f"artifactType: {definition.artifact_type}")
            return

        client = _connection_from(
            registry=registry,
            user=user,
            secret=secret,
        )
    except AppSdkError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(code=1) from exc

    with client:
        try:
            subject = client.resolve_subject(definition.image.repository, image_ref)
            definition_doc = definition.definition_document
            definition_bytes = json.dumps(definition_doc, sort_keys=True, separators=(",", ":")).encode(
                "utf-8"
            )
            definition_descriptor = client.push_blob(definition.image.repository, definition_bytes)
            # The layer's mediaType is the app-definition type, not octet-stream.
            definition_descriptor = {**definition_descriptor, "mediaType": definition.artifact_type}
            # Harbor validates that every blob referenced by the manifest
            # exists in the registry before accepting the manifest push — a
            # missing blob is rejected as MANIFEST_BLOB_UNKNOWN (HTTP 400).
            # The manifest's `config` is the OCI empty-config blob
            # (sha256:e3b0c442..., size 0); Harbor does not treat it as
            # implicitly present the way the reference distribution server
            # does, so push it explicitly. Re-pushes are a no-op (registries
            # deduplicate by digest). EMPTY_CONFIG_BYTES is the same bytes
            # the manifest module hashed to derive EMPTY_CONFIG_DIGEST, so the
            # blob's digest always matches the manifest's `config.digest`.
            client.push_blob(definition.image.repository, EMPTY_CONFIG_BYTES)
            manifest = build_app_definition_manifest(
                subject_descriptor=subject,
                definition_blob_descriptor=definition_descriptor,
                definition_media_type=definition.artifact_type,
            )
            manifest_digest = client.push_manifest(definition.image.repository, manifest)
        except RegistryError as exc:
            detail = f" (HTTP {exc.status_code})" if exc.status_code is not None else ""
            body = f"\n{exc.body}" if exc.body else ""
            typer.echo(f"error: {exc}{detail}{body}", err=True)
            raise typer.Exit(code=2) from exc
        except AppSdkError as exc:
            typer.echo(f"error: {exc}", err=True)
            raise typer.Exit(code=1) from exc

    typer.echo(
        f"registered app {definition.name!r}: referrer manifest "
        f"{manifest_digest} attached to {definition.image.repository}@{subject['digest']}",
    )


if __name__ == "__main__":
    app()
