# cytario-app-sdk

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

## Install (dev)

```bash
uv sync
uv run cytario-app-sdk --help
```

## Configuration

Connection settings (registry endpoint, user, secret) can be supplied on the
command line or via a YAML config file consumed by [typer-config][typer-config]:

```yaml
# cytario-app-sdk.yaml
registry: https://harbor.example.com
user: robot$cytario-catalog
secret: <robot-token>
```

[typer-config]: https://github.com/maxb2/typer-config

```bash
uv run cytario-app-sdk --config cytario-app-sdk.yaml register examples/cellseg.yaml
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

```bash
uv run ruff check --fix
uv run ruff format
uv run pytest
```
