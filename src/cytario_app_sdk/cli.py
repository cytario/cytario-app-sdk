"""Typer CLI entry point.

Commands:
  register <app.yaml>   Attach an app-definition to a container image as an OCI
                        Image Format annotation on the image manifest
                        (``org.cytario.appdef.v1``). The manifest's new content
                        digest pins both the image and the definition.

  run -- <cmd...>       Wrapper-mode entrypoint for analysis containers: downloads
                        inputs from S3, spawns the algorithm, uploads outputs.
                        Reads CYTARIO_BROKER_*, CYTARIO_INPUT_URIS, CYTARIO_OUTPUT_URI
                        from the environment (injected by the compute plugin).

Connection settings (registry, user, secret) can be passed on the command line
or via a YAML config file consumed by typer-config's ``--config`` option.

Usage:
  cytario-app-sdk --config conn.yaml register app.yaml
  cytario-app-sdk --registry https://harbor.example.com --user robot$cat \
      --secret <token> register app.yaml
  cytario-app-sdk run -- python /app/process.py
"""

from __future__ import annotations

import json
import os
from datetime import timedelta
from pathlib import Path
from typing import Annotated

import typer
import yaml
from typer_config.decorators import use_yaml_config

from cytario_app_sdk import __version__
from cytario_app_sdk.errors import AppDefinitionError, AppSdkError, RegistryError
from cytario_app_sdk.models import AppDefinition
from cytario_app_sdk.oci import APPDEF_ANNOTATION_KEY, RegistryClient, attach_definition_annotation
from cytario_app_sdk.runtime.params import load_parameters_from_env, parameters_to_flags

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

    ``@use_yaml_config()`` injects a ``--config`` option and, when provided,
    populates ``registry``/``user``/``secret`` from the YAML before this runs.
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
        typer.Option("--dry-run", help="Validate and print the planned annotation without pushing."),
    ] = False,
) -> None:
    """Register an app-definition as an OCI Image Format annotation on its image manifest."""
    try:
        definition = _load_app_definition(app_yaml)
        image_ref = definition.image.tag or definition.image.digest
        if not image_ref:
            msg = "app-definition image has neither tag nor digest"
            raise AppDefinitionError(msg)

        typer.echo(
            f"registering app {definition.application_id!r} on {definition.image.repository}@{image_ref}",
        )

        if dry_run:
            typer.echo(
                "DRY RUN: would fetch the image manifest, attach the app-definition "
                "annotation, and PUT it back by the image ref.",
            )
            typer.echo(f"annotation key: {APPDEF_ANNOTATION_KEY}")
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
            manifest, media_type, _old_digest = client.fetch_manifest(definition.image.repository, image_ref)
            definition_doc = definition.definition_document
            definition_json = json.dumps(definition_doc, sort_keys=True, separators=(",", ":"))
            annotated = attach_definition_annotation(
                manifest=manifest,
                definition_json=definition_json,
            )
            new_digest = client.put_manifest(
                definition.image.repository,
                image_ref,
                annotated,
                media_type=media_type,
            )
        except RegistryError as exc:
            detail = f" (HTTP {exc.status_code})" if exc.status_code is not None else ""
            body = f"\n{exc.body}" if exc.body else ""
            typer.echo(f"error: {exc}{detail}{body}", err=True)
            raise typer.Exit(code=2) from exc
        except AppSdkError as exc:
            typer.echo(f"error: {exc}", err=True)
            raise typer.Exit(code=1) from exc

    typer.echo(
        f"registered app {definition.application_id!r}: attached annotation "
        f"{APPDEF_ANNOTATION_KEY} to {definition.image.repository}@{image_ref} "
        f"(new manifest digest {new_digest})",
    )


# ---------------------------------------------------------------------------
# run — wrapper-mode entrypoint for analysis containers
# ---------------------------------------------------------------------------


@app.command(
    context_settings={"allow_extra_args": True, "ignore_unknown_options": True},
    add_help_option=True,
)
def run(
    ctx: typer.Context,
    input_dir: Annotated[
        Path,
        typer.Option("--input-dir", help="Local directory to download inputs into."),
    ] = Path("/data/in"),
    output_dir: Annotated[
        Path,
        typer.Option("--output-dir", help="Local directory to upload outputs from."),
    ] = Path("/data/out"),
    upload_on_failure: Annotated[
        bool,
        typer.Option("--upload-on-failure", help="Upload outputs even when the algorithm fails."),
    ] = False,
    pass_through_env: Annotated[
        bool,
        typer.Option(
            "--pass-through-env",
            help="Keep broker env vars in the subprocess (for hybrid algorithms).",
        ),
    ] = False,
    refresh_margin: Annotated[
        int,
        typer.Option("--refresh-margin", help="Broker credential refresh margin in seconds."),
    ] = 300,
) -> None:
    """Wrapper-mode entrypoint: download inputs, run <command>, upload outputs.

    Reads CYTARIO_BROKER_ENDPOINT, CYTARIO_BROKER_TOKEN, AWS_BATCH_JOB_ID,
    CYTARIO_INPUT_URIS (JSON array of s3:// URIs), and CYTARIO_OUTPUT_URI
    (s3:// URI) from the environment, and CYTARIO_PARAMETERS (JSON object of
    user-validated application parameters) which it appends to the algorithm
    command as ``--<name> <value>`` flags (SDS-CY-080302). The algorithm
    command follows ``--``::

        cytario-app-sdk run -- python /app/process.py
    """
    command = ctx.args
    if not command:
        typer.echo("error: no command specified after '--'", err=True)
        raise typer.Exit(code=1)

    # Append user-validated application parameters (SDS-CY-080302) as --<name>
    # <value> flags so a wrapper-mode algorithm exposes a plain CLI whose flag
    # names match its app-definition. An empty/absent CYTARIO_PARAMETERS leaves
    # the command unchanged (backward compatible with images predating it).
    parameters = load_parameters_from_env()
    command = [*command, *parameters_to_flags(parameters)]

    # Lazy imports — boto3 is an optional dependency.
    try:
        from cytario_app_sdk.broker import BrokerClient, broker_boto3_session
        from cytario_app_sdk.broker.exceptions import BrokerError
        from cytario_app_sdk.runtime import run_job
    except ImportError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(code=1) from exc

    # Read broker config from env.
    try:
        broker = BrokerClient.from_env(refresh_margin=timedelta(seconds=refresh_margin))
    except BrokerError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(code=1) from exc

    # Read input/output URIs from env.
    sources: list[str] = []
    raw_inputs = os.environ.get("CYTARIO_INPUT_URIS", "").strip()
    if raw_inputs:
        try:
            parsed = json.loads(raw_inputs)
        except json.JSONDecodeError as exc:
            typer.echo(f"error: CYTARIO_INPUT_URIS is not valid JSON: {exc}", err=True)
            raise typer.Exit(code=1) from exc
        if not isinstance(parsed, list):
            typer.echo("error: CYTARIO_INPUT_URIS must be a JSON array of s3:// URIs", err=True)
            raise typer.Exit(code=1)
        sources = [str(s) for s in parsed]

    output_uri = os.environ.get("CYTARIO_OUTPUT_URI", "").strip() or None

    # Build broker-backed boto3 session.
    region = os.environ.get("AWS_DEFAULT_REGION") or os.environ.get("AWS_REGION")
    try:
        session = broker_boto3_session(broker, region_name=region)
    except BrokerError as exc:
        typer.echo(f"error: broker denied initial credential mint: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    except ImportError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(code=1) from exc

    s3 = session.client("s3")
    exit_code = run_job(
        s3,
        input_dir=input_dir,
        output_dir=output_dir,
        sources=sources,
        output_uri=output_uri,
        command=command,
        upload_on_failure=upload_on_failure,
        pass_through_env=pass_through_env,
    )
    raise typer.Exit(code=exit_code)


if __name__ == "__main__":
    app()
