# AGENTS.md

Small Typer CLI that registers Cytario analysis apps in an OCI Distribution v1.1
registry (Harbor 2.13+) by attaching an *app-definition* as an OCI Referrer to a
container image. Python 3.10, `uv`-managed, single package in `src/cytario_app_sdk`.

## Commands

```bash
uv sync                                  # install deps into .venv
uv run cytario-app-sdk --help            # run the CLI
uv run ruff check --fix && uv run ruff format   # lint+format (run before commit)
uv run pytest                            # full test suite
uv run pytest tests/test_client.py::test_push_blob_single_post   # one test
uv run pytest tests/test_cli.py         # one file
```

No CI, no pre-commit hooks, no codegen, no migrations. `ruff check` then `ruff
format` then `pytest` is the full verification loop.

## Repo layout

- `src/cytario_app_sdk/cli.py` — Typer entrypoint; `register` command + `--dry-run`.
- `src/cytario_app_sdk/models.py` — Pydantic models for the app-definition YAML.
- `src/cytario_app_sdk/oci/client.py` — `RegistryClient` (httpx) over OCI Distribution v1.1.
- `src/cytario_app_sdk/oci/manifest.py` — builds the referrer image manifest.
- `src/cytario_app_sdk/config.py` — validates the connection YAML (separate from typer-config injection).
- `examples/` — `cellseg.yaml` (app-definition) and `connection.yaml` (registry creds). Tests load these directly via `conftest.py` fixtures, so changing them changes tests.

## Conventions that differ from defaults

- **Ruff `select = ["ALL"]`** with per-file ignores in `pyproject.toml`. The test ignores (S101 asserts, SLF001 private access, no docstrings/annotations, etc.) are intentional — do not "clean them up." Same for `PLR0913`/`TC003` ignores on `cli.py` and `client.py`.
- **Pydantic models use `extra="forbid"` and camelCase aliases** (`mediaTypes`, `schemaVersion`, `parameterSchema`, `inputRoles`, `outputRoles`). The app-definition YAML is camelCase. Add new fields with `alias=` or validation will reject them.
- **`ImageRef.tag` and `ImageRef.digest` are mutually exclusive** (exactly one required), enforced by a model validator.
- **Canonical JSON matters.** Both `oci/client.py:_canonical_json` and `cli.py` serialize the definition/manifest with `sort_keys=True, separators=(",", ":")` so the manifest digest is stable across runs. Change one, change the other.
- **The app-definition layer mediaType is overwritten** to `artifact_type` (`application/vnd.cytario.app-definition.v1+json`) after `push_blob` returns — see `cli.py` ~line 151. The blob is pushed as `application/octet-stream` and the descriptor is patched before going into the manifest.
- Python 3.10 target: `from __future__ import annotations` is used everywhere; `typing_extensions.Self` for context managers.

## `register` flow (do not reorder)

1. Load + validate app-definition YAML → `AppDefinition`.
2. `--dry-run` prints the planned `artifactType` and exits without any HTTP.
3. Build `RegistryClient` from `--registry/--user/--secret` or `--config <yaml>` (typer-config injects the latter).
4. `resolve_subject` — `HEAD /v2/<repo>/manifests/<tag|digest>` → subject descriptor.
5. `push_blob` — single-POST upload (`POST .../blobs/uploads/?digest=` → 201); falls back to POST+PUT when the registry returns 202 with a `Location`.
6. `push_manifest` — `PUT /v2/<repo>/manifests/<sha256:…>` (referrer manifests are pushed by digest; the registry indexes them under the subject's referrers list).

No Harbor-specific APIs. The catalog adapter discovers the result via
`GET /v2/<name>/referrers/<digest>`; the app-definition shape it validates is
referenced internally as SDS-CY-080200.

## Tests

- HTTP is mocked with **`pytest-httpx`** (`httpx_mock` fixture, typed `pytest.FuncFixture`). `respx` is in dev deps but not currently imported — prefer `httpx_mock` to match existing tests.
- `tests/conftest.py` owns the reusable registry mocks: `mock_resolve_subject`, `mock_push_blob_single_post`, `mock_push_manifest`, and the composed `full_registry_mock`. The blob/manifest callbacks assert digest equality, so a broken canonical-JSON change fails fast.
- Fixtures (`example_app_yaml`, `example_connection_yaml`, `registry_url`, `repository`, `image_tag`, `image_manifest_digest`, `image_manifest_size`) are shared — reuse them instead of hardcoding `cytario/cellseg` / `sha256:aaa…` in new tests.
- `tests/__init__.py` exists intentionally (so `INP001` is ignored there); don't remove it.

## Gotchas

- **`cytario-app-sdk.yaml` at the repo root is the local connection config and currently contains a live robot token.** It is untracked but **not** in `.gitignore` — do not `git add` it, and prefer editing `examples/connection.yaml` for documented examples. If you add it to `.gitignore`, that's a worthwhile commit.
- The CLI's `--config` is provided by `typer-config`'s `@use_yaml_config()` and populates `registry`/`user`/`secret` *before* the command body runs; `--dry-run` still requires a valid app-definition but skips connection setup entirely.
- `typer.Argument(exists=True, dir_okay=False)` rejects missing app-definition files before our error handler, so such tests assert `exit_code != 0` rather than a specific message.
