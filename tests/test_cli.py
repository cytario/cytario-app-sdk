"""End-to-end CLI tests using typer.testing.CliRunner + respx mocks."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import TYPE_CHECKING

import httpx
from typer.testing import CliRunner

from cytario_app_sdk.cli import app
from cytario_app_sdk.oci.manifest import EMPTY_CONFIG_DIGEST, OCI_MANIFEST_MEDIA_TYPE

if TYPE_CHECKING:
    import pytest

RUNNER = CliRunner()


def _digest(payload: bytes) -> str:
    return f"sha256:{hashlib.sha256(payload).hexdigest()}"


def test_register_dry_run_validates_yaml(
    example_app_yaml: Path,
    example_connection_yaml: Path,
) -> None:
    result = RUNNER.invoke(
        app,
        [
            "register",
            str(example_app_yaml),
            "--config",
            str(example_connection_yaml),
            "--dry-run",
        ],
    )
    assert result.exit_code == 0, result.stdout
    assert "registering app 'cellseg'" in result.stdout
    assert "DRY RUN" in result.stdout
    assert "application/vnd.cytario.app-definition.v1+json" in result.stdout


def test_register_dry_run_with_inline_credentials(
    example_app_yaml: Path,
    registry_url: str,
) -> None:
    result = RUNNER.invoke(
        app,
        [
            "register",
            str(example_app_yaml),
            "--registry",
            registry_url,
            "--user",
            "robot$cat",
            "--secret",
            "tok",
            "--dry-run",
        ],
    )
    assert result.exit_code == 0, result.stdout
    assert "registering app 'cellseg'" in result.stdout


def test_register_end_to_end(
    example_app_yaml: Path,
    example_connection_yaml: Path,
    full_registry_mock: None,
) -> None:
    result = RUNNER.invoke(
        app,
        [
            "register",
            str(example_app_yaml),
            "--config",
            str(example_connection_yaml),
        ],
    )
    assert result.exit_code == 0, result.stdout
    assert "registered app 'cellseg'" in result.stdout
    assert "referrer manifest sha256:" in result.stdout


def test_register_pushes_empty_config_blob_before_manifest(
    httpx_mock: pytest.FuncFixture,
    example_app_yaml: Path,
    example_connection_yaml: Path,
    registry_url: str,
    repository: str,
    image_tag: str,
    image_manifest_digest: str,
    image_manifest_size: int,
) -> None:
    """Harbor rejects a manifest whose referenced blobs don't exist
    (MANIFEST_BLOB_UNKNOWN, HTTP 400). The referrer manifest's `config` is the
    OCI empty-config blob (sha256:e3b0c443..., size 0), which Harbor does not
    treat as implicitly present — the SDK must push it before the manifest.
    """
    httpx_mock.add_response(
        method="HEAD",
        url=f"{registry_url}/v2/{repository}/manifests/{image_tag}",
        status_code=200,
        headers={
            "Docker-Content-Digest": image_manifest_digest,
            "Content-Length": str(image_manifest_size),
            "Content-Type": OCI_MANIFEST_MEDIA_TYPE,
        },
    )

    pushed_digests: list[str] = []
    push_order: list[str] = []

    def _blob_cb(request: httpx.Request) -> httpx.Response:
        digest = request.url.params.get("digest")
        assert digest is not None, "blob upload POST missing digest query param"
        pushed_digests.append(digest)
        push_order.append(f"blob:{digest}")
        return httpx.Response(
            201,
            headers={
                "Location": f"{registry_url}/v2/{repository}/blobs/{digest}",
                "Docker-Content-Digest": digest,
            },
        )

    httpx_mock.add_callback(
        _blob_cb,
        method="POST",
        url=re.compile(re.escape(f"{registry_url}/v2/{repository}/blobs/uploads/") + r"(\?.*)?$"),
        is_reusable=True,
    )

    def _manifest_cb(request: httpx.Request) -> httpx.Response:
        push_order.append("manifest")
        digest = _digest(request.content)
        return httpx.Response(
            201,
            headers={
                "Location": f"{registry_url}/v2/{repository}/manifests/{digest}",
                "Docker-Content-Digest": digest,
            },
        )

    httpx_mock.add_callback(
        _manifest_cb,
        method="PUT",
        url=re.compile(rf"{registry_url}/v2/{repository}/manifests/sha256:[a-f0-9]+"),
    )

    result = RUNNER.invoke(
        app,
        [
            "register",
            str(example_app_yaml),
            "--config",
            str(example_connection_yaml),
        ],
    )
    assert result.exit_code == 0, result.stdout

    # The empty config blob must be pushed.
    assert EMPTY_CONFIG_DIGEST in pushed_digests, (
        f"empty config blob {EMPTY_CONFIG_DIGEST} was not pushed; pushed digests: {pushed_digests}"
    )
    # And it must be pushed BEFORE the manifest (Harbor checks blobs at
    # manifest-push time, so pushing it after would still 400).
    empty_blob_idx = push_order.index(f"blob:{EMPTY_CONFIG_DIGEST}")
    manifest_idx = push_order.index("manifest")
    assert empty_blob_idx < manifest_idx, f"empty config blob pushed after manifest: order={push_order}"


def test_register_missing_app_file(
    example_connection_yaml: Path,
    tmp_path: Path,
) -> None:
    result = RUNNER.invoke(
        app,
        [
            "register",
            str(tmp_path / "nope.yaml"),
            "--config",
            str(example_connection_yaml),
        ],
    )
    # typer.Argument(exists=True) rejects the path before our handler runs.
    assert result.exit_code != 0


def test_register_invalid_app_definition(
    example_connection_yaml: Path,
    tmp_path: Path,
) -> None:
    bad = tmp_path / "bad.yaml"
    bad.write_text(
        "name: cellseg\nimage: {repository: cytario/x}\n",  # missing tag AND digest
        encoding="utf-8",
    )
    result = RUNNER.invoke(
        app,
        [
            "register",
            str(bad),
            "--config",
            str(example_connection_yaml),
            "--dry-run",
        ],
    )
    assert result.exit_code == 1, result.output
    assert "error:" in result.output


def test_register_no_connection(
    example_app_yaml: Path,
) -> None:
    # Without --dry-run, the command needs a connection and must fail.
    result = RUNNER.invoke(app, ["register", str(example_app_yaml)])
    assert result.exit_code != 0


def test_help_lists_register() -> None:
    result = RUNNER.invoke(app, ["--help"])
    assert result.exit_code == 0, result.stdout
    assert "register" in result.stdout


def test_register_auto_discovers_local_config(
    example_app_yaml: Path,
    example_connection_yaml: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A ./cytario-app-sdk.yaml in cwd auto-loads, no --config needed."""
    # Place the auto-discovered config in an isolated cwd.
    monkeypatch.chdir(tmp_path)
    cfg = tmp_path / "cytario-app-sdk.yaml"
    cfg.write_text(example_connection_yaml.read_text(encoding="utf-8"), encoding="utf-8")

    result = RUNNER.invoke(
        app,
        ["register", str(example_app_yaml), "--dry-run"],
    )
    assert result.exit_code == 0, result.stdout
    assert "registering app 'cellseg'" in result.stdout
    assert "DRY RUN" in result.stdout


def test_register_no_config_when_nothing_to_auto_discover(
    example_app_yaml: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Without --config and no auto-discoverable file, --dry-run still works
    (it validates the app-definition only) but a non-dry-run needs flags."""
    import cytario_app_sdk.config as cfg  # noqa: PLC0415

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(cfg, "USER_CONFIG_DIR", tmp_path / "nonexistent")

    # --dry-run does not need a connection.
    dry = RUNNER.invoke(app, ["register", str(example_app_yaml), "--dry-run"])
    assert dry.exit_code == 0, dry.stdout

    # Non-dry-run without any connection source fails.
    real = RUNNER.invoke(app, ["register", str(example_app_yaml)])
    assert real.exit_code != 0


# ---------------------------------------------------------------------------
# apps — catalog discovery
# ---------------------------------------------------------------------------


def test_apps_single_repo_finds_app(
    example_connection_yaml: Path,
    full_discovery_mock: None,
    repository: str,
) -> None:
    """`apps --repo` scans one repo and finds the registered app."""
    result = RUNNER.invoke(
        app,
        [
            "apps",
            "--config",
            str(example_connection_yaml),
            "--repo",
            repository,
        ],
    )
    assert result.exit_code == 0, result.stdout
    assert "cellseg" in result.stdout
    assert "image" in result.stdout
    assert "1 app(s)" in result.stdout


def test_apps_namespace_scan_finds_app(
    example_connection_yaml: Path,
    full_catalog_discovery_mock: None,
) -> None:
    """`apps --namespace` scans the catalog and finds the registered app."""
    result = RUNNER.invoke(
        app,
        [
            "apps",
            "--config",
            str(example_connection_yaml),
            "--namespace",
            "cytario",
        ],
    )
    assert result.exit_code == 0, result.stdout
    assert "cellseg" in result.stdout


def test_apps_json_output(
    example_connection_yaml: Path,
    full_discovery_mock: None,
    repository: str,
) -> None:
    """`apps --json` emits machine-readable JSON with the full app-definition."""
    result = RUNNER.invoke(
        app,
        [
            "apps",
            "--config",
            str(example_connection_yaml),
            "--repo",
            repository,
            "--json",
        ],
    )
    assert result.exit_code == 0, result.stdout
    payload = json.loads(result.stdout)
    assert isinstance(payload, list)
    assert len(payload) == 1
    assert payload[0]["name"] == "cellseg"
    assert payload[0]["image"]["repository"] == repository
    assert payload[0]["image"]["tag"] == "1.0.0"
    assert "parameterSchema" in payload[0]
    assert payload[0]["artifactType"] == "application/vnd.cytario.app-definition.v1+json"


def test_apps_name_filter(
    example_connection_yaml: Path,
    full_discovery_mock: None,
    repository: str,
) -> None:
    """`apps --name` filters by the app-definition name."""
    result = RUNNER.invoke(
        app,
        [
            "apps",
            "--config",
            str(example_connection_yaml),
            "--repo",
            repository,
            "--name",
            "cellseg",
        ],
    )
    assert result.exit_code == 0, result.stdout
    assert "cellseg" in result.stdout

    # Non-matching name → no apps.
    no_match = RUNNER.invoke(
        app,
        [
            "apps",
            "--config",
            str(example_connection_yaml),
            "--repo",
            repository,
            "--name",
            "nonexistent",
        ],
    )
    assert no_match.exit_code == 0, no_match.stdout
    assert "No apps found" in no_match.stdout


def test_apps_bare_repo_without_namespace_errors(
    example_connection_yaml: Path,
) -> None:
    """A bare repo name without --namespace fails with a helpful error."""
    result = RUNNER.invoke(
        app,
        [
            "apps",
            "--config",
            str(example_connection_yaml),
            "--repo",
            "cellseg",
        ],
    )
    assert result.exit_code != 0
    assert "namespace" in result.output.lower()


def test_apps_bare_repo_with_namespace_resolves(
    example_connection_yaml: Path,
    full_discovery_mock: None,
    repository: str,
) -> None:
    """A bare repo name is resolved to <namespace>/<name>."""
    # full_discovery_mock mocks `cytario/cellseg`; `cellseg` + --namespace cytario
    # should resolve to the same repository.
    result = RUNNER.invoke(
        app,
        [
            "apps",
            "--config",
            str(example_connection_yaml),
            "--repo",
            "cellseg",
            "--namespace",
            "cytario",
        ],
    )
    assert result.exit_code == 0, result.stdout
    assert "cellseg" in result.stdout


def test_apps_no_connection_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Without any connection source, `apps` fails (it always needs the registry)."""
    import cytario_app_sdk.config as cfg  # noqa: PLC0415

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(cfg, "USER_CONFIG_DIR", tmp_path / "nonexistent")
    result = RUNNER.invoke(app, ["apps"])
    assert result.exit_code != 0


def test_apps_verbose_shows_skipped(
    example_connection_yaml: Path,
    mock_resolve_subject: None,
    mock_list_tags: None,
    registry_url: str,
    repository: str,
    image_manifest_digest: str,
    httpx_mock: pytest.FuncFixture,
) -> None:
    """`apps -v` shows skip reasons when a repo has no app-definition referrer."""
    # No referrer mock → list_referrers returns empty.
    httpx_mock.add_response(
        method="GET",
        url=f"{registry_url}/v2/{repository}/referrers/{image_manifest_digest}",
        status_code=200,
        json={
            "schemaVersion": 2,
            "mediaType": "application/vnd.oci.image.index.v1+json",
            "manifests": [],
        },
    )
    result = RUNNER.invoke(
        app,
        [
            "apps",
            "--config",
            str(example_connection_yaml),
            "--repo",
            repository,
            "--verbose",
        ],
    )
    assert result.exit_code == 0, result.stdout
    assert "No apps found" in result.stdout
    assert "no app-definition referrer" in result.stdout


def test_apps_registry_error_exits_nonzero(
    example_connection_yaml: Path,
    httpx_mock: pytest.FuncFixture,
    registry_url: str,
    repository: str,
) -> None:
    """A registry error during scan exits with code 2 (like register)."""
    httpx_mock.add_response(
        method="GET",
        url=f"{registry_url}/v2/{repository}/tags/list",
        status_code=401,
        text="unauthorized",
    )
    result = RUNNER.invoke(
        app,
        [
            "apps",
            "--config",
            str(example_connection_yaml),
            "--repo",
            repository,
        ],
    )
    assert result.exit_code == 2, result.stdout
    assert "error:" in result.output.lower() or "unauthorized" in result.output.lower()


# ---------------------------------------------------------------------------
# apps show — single-app card
# ---------------------------------------------------------------------------


def test_apps_show_card(
    example_connection_yaml: Path,
    full_discovery_mock: None,
    repository: str,
) -> None:
    """`apps show <name>` prints a rich card with the full app-definition."""
    result = RUNNER.invoke(
        app,
        [
            "apps",
            "show",
            "cellseg",
            "--config",
            str(example_connection_yaml),
            "--repo",
            repository,
        ],
    )
    assert result.exit_code == 0, result.stdout
    assert "cellseg" in result.stdout
    assert "Cell Segmentation" in result.stdout
    assert "Runs Cellpose" in result.stdout
    assert "image" in result.stdout
    assert "segmentation" in result.stdout
    assert "manifestDigest" in result.stdout


def test_apps_show_json(
    example_connection_yaml: Path,
    full_discovery_mock: None,
    repository: str,
) -> None:
    """`apps show <name> --json` emits the full app-definition as JSON."""
    result = RUNNER.invoke(
        app,
        [
            "apps",
            "show",
            "cellseg",
            "--config",
            str(example_connection_yaml),
            "--repo",
            repository,
            "--json",
        ],
    )
    assert result.exit_code == 0, result.stdout
    payload = json.loads(result.stdout)
    assert payload["name"] == "cellseg"
    assert payload["display"] == "Cell Segmentation"
    assert payload["image"]["repository"] == repository
    assert payload["image"]["tag"] == "1.0.0"
    assert "parameterSchema" in payload
    assert payload["artifactType"] == "application/vnd.cytario.app-definition.v1+json"


def test_apps_show_not_found(
    example_connection_yaml: Path,
    full_discovery_mock: None,
    repository: str,
) -> None:
    """`apps show <missing>` exits 1 with a 'No app named' message."""
    result = RUNNER.invoke(
        app,
        [
            "apps",
            "show",
            "nonexistent",
            "--config",
            str(example_connection_yaml),
            "--repo",
            repository,
        ],
    )
    assert result.exit_code == 1, result.stdout
    assert "No app named" in result.stdout
    assert "nonexistent" in result.stdout


def test_apps_show_bare_repo_resolves_with_namespace(
    example_connection_yaml: Path,
    full_discovery_mock: None,
) -> None:
    """`apps show` resolves a bare repo name via --namespace."""
    result = RUNNER.invoke(
        app,
        [
            "apps",
            "show",
            "cellseg",
            "--config",
            str(example_connection_yaml),
            "--repo",
            "cellseg",
            "--namespace",
            "cytario",
        ],
    )
    assert result.exit_code == 0, result.stdout
    assert "cellseg" in result.stdout


def test_apps_show_namespace_scan(
    example_connection_yaml: Path,
    full_catalog_discovery_mock: None,
) -> None:
    """`apps show` works without --repo (scans the namespace catalog)."""
    result = RUNNER.invoke(
        app,
        [
            "apps",
            "show",
            "cellseg",
            "--config",
            str(example_connection_yaml),
            "--namespace",
            "cytario",
        ],
    )
    assert result.exit_code == 0, result.stdout
    assert "cellseg" in result.stdout
