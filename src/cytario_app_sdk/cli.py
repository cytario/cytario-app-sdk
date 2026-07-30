"""Typer CLI entry point.

Commands:
  register <app.yaml>   Attach an app-definition to a container image as an
                        OCI Referrer (artifactType
                        `application/vnd.cytario.app-definition.v1+json`).
  apps                  Scan the registry for registered Cytario apps.

Connection settings (registry, user, secret) can be passed on the command line
or via a YAML config file. When `--config` is omitted, the CLI auto-discovers
`./cytario-app-sdk.yaml` then `~/.config/cytario-app-sdk/config.yaml`.

Usage:
  cytario-app-sdk register app.yaml
  cytario-app-sdk apps
  cytario-app-sdk apps --repo cytario/cellseg
  cytario-app-sdk --registry https://harbor.example.com --user robot$cat \
      --secret <token> register app.yaml
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Annotated

import typer
import yaml
from rich.console import Console
from rich.progress import BarColumn, Progress, SpinnerColumn, TextColumn, TimeRemainingColumn
from rich.table import Table
from typer_config.decorators import use_config

from cytario_app_sdk import __version__
from cytario_app_sdk.config import auto_yaml_conf_callback
from cytario_app_sdk.discovery import (
    APP_DEFINITION_ARTIFACT_TYPE,
    CatalogApp,
    ScanResult,
    scan_namespace,
    scan_repository,
)
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
@use_config(auto_yaml_conf_callback)
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
            definition_descriptor = {
                **definition_descriptor,
                "mediaType": definition.artifact_type,
            }
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


# ---------------------------------------------------------------------------
# apps — catalog discovery
# ---------------------------------------------------------------------------


def _resolve_repo(repo: str, namespace: str | None) -> str:
    """Resolve a bare repo name to `<namespace>/<name>`; leave full paths as-is."""
    if "/" in repo:
        return repo
    if namespace:
        return f"{namespace}/{repo}"
    msg = (
        f"bare repo name {repo!r} needs a namespace: pass --namespace or set `namespace` in your config file"
    )
    raise typer.BadParameter(msg)


def _format_roles(app: CatalogApp) -> str:
    """Compact `input→output` role summary, e.g. `image,mask→seg,report`."""
    inputs = ",".join(r.name for r in app.definition.input_roles) or "-"
    outputs = ",".join(r.name for r in app.definition.output_roles) or "-"
    return f"{inputs}→{outputs}"


def _print_apps_table(apps: list[CatalogApp], console: Console) -> None:
    table = Table(title="Cytario app catalog", show_lines=False)
    table.add_column("Name", style="bold cyan", no_wrap=True)
    table.add_column("Image", style="dim", no_wrap=True)
    table.add_column("Roles", no_wrap=True)
    table.add_column("Tag", no_wrap=True)
    table.add_column("Manifest", style="dim", no_wrap=True)
    for app in apps:
        image_ref = f"{app.repository}@{app.subject_descriptor['digest'][:19]}…"
        table.add_row(
            app.definition.name,
            image_ref,
            _format_roles(app),
            app.tag or "—",
            app.referrer_manifest_digest[:19] + "…",
        )
    console.print(table)


def _print_apps_json(apps: list[CatalogApp]) -> None:
    payload = [
        {
            "name": app.definition.name,
            "display": app.definition.display,
            "description": app.definition.description,
            "image": {
                "repository": app.repository,
                "digest": app.subject_descriptor["digest"],
                "tag": app.tag,
            },
            "parameterSchema": app.definition.parameter_schema,
            "inputRoles": [r.model_dump(by_alias=True) for r in app.definition.input_roles],
            "outputRoles": [r.model_dump(by_alias=True) for r in app.definition.output_roles],
            "groups": app.definition.groups.model_dump(),
            "manifestDigest": app.referrer_manifest_digest,
            "artifactType": APP_DEFINITION_ARTIFACT_TYPE,
        }
        for app in apps
    ]
    typer.echo(json.dumps(payload, indent=2, sort_keys=True))


def _has_fatal_errors(result: ScanResult) -> bool:
    return bool(result.errors)


def _emit_errors(result: ScanResult) -> None:
    for err in result.errors:
        typer.echo(f"error: {err}", err=True)


def _print_scan_summary(result: ScanResult, console: Console, *, verbose: bool) -> None:
    console.print(
        f"\n[bold]{len(result.apps)} app(s)[/bold] found"
        + (f", {len(result.skipped)} skipped" if result.skipped else "")
        + (f", {len(result.errors)} error(s)" if result.errors else "")
        + ".",
    )
    if verbose and result.skipped:
        console.print("\n[dim]Skipped:[/dim]")
        for reason in result.skipped:
            console.print(f"  [dim]• {reason}[/dim]")
    if verbose and result.errors:
        console.print("\n[red]Errors:[/red]")
        for err in result.errors:
            console.print(f"  [red]• {err}[/red]")


apps_app = typer.Typer(
    name="apps",
    help="Scan the registry for registered Cytario apps.",
    no_args_is_help=True,
    invoke_without_command=True,
)
app.add_typer(apps_app)


def _scan(
    *,
    client: RegistryClient,
    namespace: str | None,
    repo: str | None,
    json_output: bool,
) -> ScanResult:
    """Run a scan (single repo or namespace-wide) with optional progress."""
    is_interactive = sys.stdout.isatty() and not json_output
    console = Console()
    resolved_repo = _resolve_repo(repo, namespace) if repo else None
    if resolved_repo:
        if is_interactive:
            with console.status(f"Scanning [bold]{resolved_repo}[/bold]…"):
                return scan_repository(client, resolved_repo)
        return scan_repository(client, resolved_repo)
    return _scan_with_progress(client, namespace, console, is_interactive)


@apps_app.callback()
@use_config(auto_yaml_conf_callback)
def apps(
    ctx: typer.Context,
    registry: Annotated[
        str | None,
        typer.Option("--registry", help="OCI registry base URL."),
    ] = None,
    user: Annotated[
        str | None,
        typer.Option("--user", help="Registry username (often a robot account)."),
    ] = None,
    secret: Annotated[
        str | None,
        typer.Option("--secret", help="Registry password / robot token."),
    ] = None,
    namespace: Annotated[
        str | None,
        typer.Option("--namespace", help="Namespace prefix to scan (e.g. 'cytario')."),
    ] = None,
    repo: Annotated[
        str | None,
        typer.Option("--repo", help="Scan a single repository (e.g. 'cytario/cellseg')."),
    ] = None,
    name: Annotated[
        str | None,
        typer.Option("--name", help="Filter results by app-definition name."),
    ] = None,
    json_output: Annotated[
        bool,
        typer.Option("--json", help="Emit machine-readable JSON (disables progress)."),
    ] = False,
    verbose: Annotated[
        bool,
        typer.Option("-v", "--verbose", help="Show skipped reasons and errors."),
    ] = False,
) -> None:
    """List registered Cytario apps in the registry."""
    if ctx.invoked_subcommand is not None:
        return  # a subcommand (show) is taking over; skip the list.
    try:
        client = _connection_from(registry=registry, user=user, secret=secret)
    except AppSdkError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(code=1) from exc

    console = Console()
    try:
        with client:
            result = _scan(client=client, namespace=namespace, repo=repo, json_output=json_output)
    except RegistryError as exc:
        _emit_registry_error(exc)
        raise typer.Exit(code=2) from exc

    if name:
        result.apps = [a for a in result.apps if a.definition.name == name]

    if _has_fatal_errors(result):
        _emit_errors(result)
        if not result.apps:
            raise typer.Exit(code=2)

    if json_output:
        _print_apps_json(result.apps)
        return
    if not result.apps:
        console.print("[yellow]No apps found.[/yellow]")
        _print_scan_summary(result, console, verbose=verbose)
        return
    _print_apps_table(result.apps, console)
    _print_scan_summary(result, console, verbose=verbose)


def _scan_with_progress(
    client: RegistryClient,
    namespace: str | None,
    console: Console,
    is_interactive: bool,
) -> ScanResult:
    """Scan all repositories (optionally namespace-filtered) with a rich progress bar."""
    if is_interactive:
        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            TextColumn("{task.completed}/{task.total}"),
            TimeRemainingColumn(),
            console=console,
        ) as progress:
            repos = client.list_repositories(namespace=namespace)
            if not repos:
                return ScanResult(apps=[], skipped=[], errors=[])
            task = progress.add_task("Scanning repositories", total=len(repos))
            all_apps: list[CatalogApp] = []
            all_skipped: list[str] = []
            all_errors: list[str] = []
            for r in repos:
                progress.update(task, description=f"Scanning [bold]{r}[/bold]")
                repo_result = scan_repository(client, r)
                all_apps.extend(repo_result.apps)
                all_skipped.extend(repo_result.skipped)
                all_errors.extend(repo_result.errors)
                progress.advance(task)
            return ScanResult(apps=all_apps, skipped=all_skipped, errors=all_errors)
    # Non-interactive: no progress output.
    return scan_namespace(client, namespace=namespace)


def _emit_registry_error(exc: RegistryError) -> None:
    detail = f" (HTTP {exc.status_code})" if exc.status_code is not None else ""
    body = f"\n{exc.body}" if exc.body else ""
    typer.echo(f"error: {exc}{detail}{body}", err=True)


def _print_app_card(app: CatalogApp, console: Console) -> None:
    """Render a single app-definition as a rich card (key/value + roles)."""
    from rich.panel import Panel  # noqa: PLC0415

    def_ = app.definition
    image_ref = (
        f"{app.repository}@{app.subject_descriptor['digest']}"
        if not def_.image.tag
        else f"{app.repository}:{def_.image.tag}"
    )
    lines: list[str] = []
    lines.append(f"[bold cyan]{def_.name}[/bold cyan]")
    if def_.display and def_.display != def_.name:
        lines.append(f"[dim]display:[/dim] {def_.display}")
    if def_.description:
        lines.append(f"[dim]description:[/dim] {def_.description}")
    lines.append(f"[dim]image:[/dim] {image_ref}")
    if app.tag:
        lines.append(f"[dim]tag:[/dim] {app.tag}")
    if def_.input_roles:
        roles = ", ".join(f"{r.name} ({', '.join(r.media_types)})" for r in def_.input_roles)
        lines.append(f"[dim]inputRoles:[/dim] {roles}")
    if def_.output_roles:
        roles = ", ".join(f"{r.name} ({', '.join(r.media_types)})" for r in def_.output_roles)
        lines.append(f"[dim]outputRoles:[/dim] {roles}")
    if def_.parameter_schema:
        lines.append("[dim]parameterSchema:[/dim] (present)")
    else:
        lines.append("[dim]parameterSchema:[/dim] (none)")
    if def_.groups.consumers or def_.groups.maintainers:
        groups_parts: list[str] = []
        if def_.groups.consumers:
            groups_parts.append(f"consumers={','.join(def_.groups.consumers)}")
        if def_.groups.maintainers:
            groups_parts.append(f"maintainers={','.join(def_.groups.maintainers)}")
        lines.append(f"[dim]groups:[/dim] {' '.join(groups_parts)}")
    lines.append(f"[dim]manifestDigest:[/dim] {app.referrer_manifest_digest}")
    lines.append(f"[dim]artifactType:[/dim] {APP_DEFINITION_ARTIFACT_TYPE}")
    console.print(Panel("\n".join(lines), title="App definition", border_style="cyan"))


def _print_app_json(app: CatalogApp) -> None:
    def_ = app.definition
    payload = {
        "name": def_.name,
        "display": def_.display,
        "description": def_.description,
        "image": {
            "repository": app.repository,
            "digest": app.subject_descriptor["digest"],
            "tag": app.tag,
        },
        "parameterSchema": def_.parameter_schema,
        "inputRoles": [r.model_dump(by_alias=True) for r in def_.input_roles],
        "outputRoles": [r.model_dump(by_alias=True) for r in def_.output_roles],
        "groups": def_.groups.model_dump(),
        "manifestDigest": app.referrer_manifest_digest,
        "artifactType": APP_DEFINITION_ARTIFACT_TYPE,
    }
    typer.echo(json.dumps(payload, indent=2, sort_keys=True))


@apps_app.command("show")
@use_config(auto_yaml_conf_callback)
def apps_show(
    app_name: Annotated[
        str,
        typer.Argument(help="App-definition name (e.g. 'cellseg')."),
    ],
    registry: Annotated[
        str | None,
        typer.Option("--registry", help="OCI registry base URL."),
    ] = None,
    user: Annotated[
        str | None,
        typer.Option("--user", help="Registry username (often a robot account)."),
    ] = None,
    secret: Annotated[
        str | None,
        typer.Option("--secret", help="Registry password / robot token."),
    ] = None,
    namespace: Annotated[
        str | None,
        typer.Option("--namespace", help="Namespace prefix to scan (e.g. 'cytario')."),
    ] = None,
    repo: Annotated[
        str | None,
        typer.Option("--repo", help="Scan a single repository (e.g. 'cytario/cellseg')."),
    ] = None,
    json_output: Annotated[
        bool,
        typer.Option("--json", help="Emit machine-readable JSON."),
    ] = False,
) -> None:
    """Show the full app-definition for a named app."""
    try:
        client = _connection_from(registry=registry, user=user, secret=secret)
    except AppSdkError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(code=1) from exc

    console = Console()
    try:
        with client:
            result = _scan(client=client, namespace=namespace, repo=repo, json_output=json_output)
    except RegistryError as exc:
        _emit_registry_error(exc)
        raise typer.Exit(code=2) from exc

    matches = [a for a in result.apps if a.definition.name == app_name]
    if not matches:
        console.print(f"[yellow]No app named {app_name!r} found.[/yellow]")
        raise typer.Exit(code=1)
    if len(matches) > 1:
        console.print(
            f"[yellow]Multiple apps named {app_name!r} found ({len(matches)}); showing the first.[/yellow]"
        )
    if json_output:
        _print_app_json(matches[0])
        return
    _print_app_card(matches[0], console)


if __name__ == "__main__":
    app()
