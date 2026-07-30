# AGENTS.md

Small Typer CLI that registers Cytario analysis apps in an OCI Distribution v1.1
registry (Harbor 2.13+) by attaching an *app-definition* as an **OCI Image Format
annotation** (`org.cytario.appdef.v1`) on the container image's manifest. Python
3.10, `uv`-managed, single package in `src/cytario_app_sdk`.

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
- `src/cytario_app_sdk/oci/manifest.py` — attaches the appdef annotation to a fetched manifest.
- `src/cytario_app_sdk/config.py` — validates the connection YAML (separate from typer-config injection).
- `examples/` — `cellseg.yaml` (app-definition) and `connection.yaml` (registry creds). Tests load these directly via `conftest.py` fixtures, so changing them changes tests.

## Conventions that differ from defaults

- **Ruff `select = ["ALL"]`** with per-file ignores in `pyproject.toml`. The test ignores (S101 asserts, SLF001 private access, no docstrings/annotations, etc.) are intentional — do not "clean them up." Same for `PLR0913`/`TC003` ignores on `cli.py` and `client.py`.
- **Pydantic models use `extra="forbid"` and camelCase aliases** (`schemaVersion`, `applicationId`, `parameterSchema`, `dataRoles`, `consumerGroups`, `maintainerGroups`). The app-definition YAML is camelCase. Add new fields with `alias=` or validation will reject them.
- **`ImageRef.tag` and `ImageRef.digest` are mutually exclusive** (exactly one required), enforced by a model validator.
- **Canonical JSON matters.** `oci/client.py:_canonical_json` and `cli.py` both serialize the definition document with `sort_keys=True, separators=(",", ":")` so the manifest digest is stable across runs. Change one, change the other.
- **The appdef annotation key is the contract surface** with the Cytario runtime's `extractDefinition` (`org.cytario.appdef.v1`). Both sides must read/write the same key; changing it breaks discovery.
- Python 3.10 target: `from __future__ import annotations` is used everywhere; `typing_extensions.Self` for context managers.

## `register` flow (do not reorder)

1. Load + validate app-definition YAML → `AppDefinition`.
2. `--dry-run` prints the planned annotation key and exits without any HTTP.
3. Build `RegistryClient` from `--registry/--user/--secret` or `--config <yaml>` (typer-config injects the latter).
4. `fetch_manifest` — `GET /v2/<repo>/manifests/<tag|digest>` (Accept covers Docker v2 schema 2, OCI manifest, and OCI index) → `(manifest, media_type, digest)`.
5. Build the canonical JSON definition document, `attach_definition_annotation` to add `annotations[org.cytario.appdef.v1]` to a deep copy of the manifest.
6. `put_manifest` — `PUT /v2/<repo>/manifests/<original-tag-or-digest>` with the fetched `media_type` as Content-Type. The new content digest (returned by the registry) pins both the image and the definition.

No Harbor-specific APIs. The Cytario runtime discovers the result via
`GET /v2/<name>/manifests/<ref>` and reads the `org.cytario.appdef.v1`
annotation; the app-definition shape it validates is the one modeled by
this SDK.

## Tests

- HTTP is mocked with **`pytest-httpx`** (`httpx_mock` fixture, typed `pytest.FuncFixture`). `respx` is in dev deps but not currently imported — prefer `httpx_mock` to match existing tests.
- `tests/conftest.py` owns the reusable registry mocks: `mock_fetch_manifest`, `mock_put_manifest`, and the composed `full_registry_mock`. The PUT callback asserts the pushed manifest carries the `org.cytario.appdef.v1` annotation, so a broken annotation attach fails fast.
- Fixtures (`example_app_yaml`, `example_connection_yaml`, `registry_url`, `repository`, `image_tag`, `image_manifest`, `image_manifest_digest`, `image_manifest_media_type`) are shared — reuse them instead of hardcoding `cytario/cellseg` / `sha256:aaa…` in new tests.
- `tests/__init__.py` exists intentionally (so `INP001` is ignored there); don't remove it.

## Gotchas

- **`cytario-app-sdk.yaml` at the repo root is the local connection config and currently contains a live robot token.** It is untracked but **not** in `.gitignore` — do not `git add` it, and prefer editing `examples/connection.yaml` for documented examples. If you add it to `.gitignore`, that's a worthwhile commit.
- The CLI's `--config` is provided by `typer-config`'s `@use_yaml_config()` and populates `registry`/`user`/`secret` *before* the command body runs; `--dry-run` still requires a valid app-definition but skips connection setup entirely.
- `typer.Argument(exists=True, dir_okay=False)` rejects missing app-definition files before our error handler, so such tests assert `exit_code != 0` rather than a specific message.
- **`register` mutates the image manifest.** It fetches the manifest, adds the appdef annotation, and PUTs it back under the original tag. The manifest's content digest changes (annotations are content), so the tag now points at a new digest — any prior digest-pinned references to the old manifest still resolve by digest, but the tag has moved. This is the intended behaviour: the new digest is what the Cytario runtime pins at run time.
- **Docker v2 schema-2 manifests have no `annotations` field** in their schema. OCI image manifests and image indexes do. The SDK adds `annotations` regardless and PUTs back with the original media type; OCI-compliant registries (Harbor included) tolerate the extra field on a Docker manifest. If your registry rejects it, re-push the image as an OCI image (`docker build --output type=oci`).
