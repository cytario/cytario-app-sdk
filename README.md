# cytario-app-sdk

[![PyPI version](https://img.shields.io/pypi/v/cytario-app-sdk.svg)](https://pypi.org/project/cytario-app-sdk/)
[![Python](https://img.shields.io/pypi/pyversions/cytario-app-sdk.svg)](https://pypi.org/project/cytario-app-sdk/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![CI](https://github.com/cytario/cytario-app-sdk/actions/workflows/ci.yml/badge.svg)](https://github.com/cytario/cytario-app-sdk/actions/workflows/ci.yml)
[![Release](https://github.com/cytario/cytario-app-sdk/actions/workflows/release.yml/badge.svg)](https://github.com/cytario/cytario-app-sdk/actions/workflows/release.yml)

A small CLI for registering Cytario analysis applications in an OCI Distribution
v1.1 registry (e.g. Harbor 2.13+).

Cytario's "app catalog" is our concept, not the registry's: an analysis
application is a container image plus a metadata document (the *app-definition*)
that declares the app's parameter schema, input/output roles, and consumer /
maintainer groups. The SDK attaches the app-definition to the container image
as an [OCI Referrer][oci-referrers] — a manifest with `artifactType:
application/vnd.cytario.app-definition.v1+json` whose `subject` points at the
container image's manifest digest. The Cytario catalog adapter discovers
app-definitions via `GET /v2/<name>/referrers/<image-digest>` and never needs a
Harbor-specific API.

[oci-referrers]: https://github.com/opencontainers/distribution-spec/blob/main/spec.md#listing-referrers

## Install

```bash
pip install cytario-app-sdk
cytario-app-sdk --help
```

Or with [`uv`](https://docs.astral.sh/uv/):

```bash
uv tool install cytario-app-sdk
```

## Quick start

```bash
cytario-app-sdk --registry https://harbor.example.com \
  --user 'robot$cytario-catalog' \
  --secret <robot-token> \
  register examples/cellseg.yaml
```

Connection settings (registry endpoint, user, secret) can be supplied on the
command line or via a YAML config file. Pass it explicitly with `--config`:

```bash
cytario-app-sdk --config cytario-app-sdk.yaml register examples/cellseg.yaml
```

…or skip `--config` entirely and let the CLI auto-discover one. When no
`--config` is given, the CLI probes, in order:

1. `./cytario-app-sdk.yaml` — project-local (committed for a team, or a personal
   untracked file at the repo root).
2. `~/.config/cytario-app-sdk/config.yaml` — user-global default.

The first existing file wins. If neither is found, the CLI falls back to
`--registry/--user/--secret` flags (or `--dry-run`, which needs no connection).

```bash
cd ~/work/cytario/cytario-app-sdk
cytario-app-sdk register examples/cellseg.yaml          # picks up ./cytario-app-sdk.yaml
cytario-app-sdk register --dry-run examples/cellseg.yaml   # no connection needed
```

Validate an app-definition without pushing anything:

```bash
cytario-app-sdk --config cytario-app-sdk.yaml register --dry-run examples/cellseg.yaml
```

## Registering an app

```yaml
# examples/cellseg.yaml
schemaVersion: 1
name: cellseg
display: Cell Segmentation
description: Runs Cellpose on a 16-bit OME-TIFF.
image:
  repository: cytario/cellseg
  tag: "1.0.0"
parameterSchema:
  type: object
  properties:
    diameter:
      type: number
      default: 30
  required: []
inputRoles:
  - name: image
    mediaTypes:
      - image/tiff
outputRoles:
  - name: segmentation
    mediaTypes:
      - application/vnd.cytario.label-mask
groups:
  consumers:
    - cellbio-team
  maintainers:
    - imaging-platform
```

`register` resolves the container image manifest (by tag or digest), builds the
app-definition artifact, pushes it as a blob, and pushes an OCI image manifest
with `subject` set to the container image descriptor. The catalog adapter picks
it up via the Referrers API; no Harbor-specific calls are made.

## Development

This project uses [`uv`](https://docs.astral.sh/uv/) and targets Python 3.10+.

```bash
uv sync                          # install deps into .venv
uv run cytario-app-sdk --help    # run the CLI
uv run ruff check --fix && uv run ruff format
uv run pytest
```

See [CONTRIBUTING.md](CONTRIBUTING.md) for the full contributor guide and the
[Releases page][releases] for release history. Versions are cut automatically
with [python-semantic-release][psr] — in no-commit mode, so no release commits
land on `main`.

[releases]: https://github.com/cytario/cytario-app-sdk/releases
[psr]: https://github.com/python-semantic-release/python-semantic-release

## License

Released under the [MIT License](LICENSE). By contributing, you agree that
your contributions will be licensed under the MIT License.
