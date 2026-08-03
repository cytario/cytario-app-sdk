# cytario-app-sdk

[![PyPI version](https://img.shields.io/pypi/v/cytario-app-sdk.svg)](https://pypi.org/project/cytario-app-sdk/)
[![Python](https://img.shields.io/pypi/pyversions/cytario-app-sdk.svg)](https://pypi.org/project/cytario-app-sdk/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![CI](https://github.com/cytario/cytario-app-sdk/actions/workflows/ci.yml/badge.svg)](https://github.com/cytario/cytario-app-sdk/actions/workflows/ci.yml)
[![Release](https://github.com/cytario/cytario-app-sdk/actions/workflows/release.yml/badge.svg)](https://github.com/cytario/cytario-app-sdk/actions/workflows/release.yml)

A CLI and Python library for Cytario analysis applications — register apps in an
OCI Distribution v1.1 registry and run them inside Cytario compute containers.

The SDK has two responsibilities:

1. **Registration** (`register` command) — attaches an *app-definition* (parameter
   schema, input/output data roles) to a container image as an **OCI Image Format
   annotation** (`org.cytario.appdef.v1`) on the image manifest, then PUTs the
   manifest back under its original tag. Because the annotation is part of the
   manifest content, the manifest's immutable content digest binds the definition
   to the exact image — pinning the image by digest also pins the definition.

2. **In-container runtime** (`run` command + `broker` / `runtime` libraries) —
   when Cytario's compute plugin submits an analysis job, the running container
   receives a broker endpoint, a job-scoped token, and resolved S3 URIs for its
   inputs and outputs. The SDK obtains short-lived storage credentials from the
   broker, downloads the inputs, spawns the algorithm, and uploads the results.

Application access (who may see/run an app, who may edit its saved configs) is
not part of the app-definition. Entitlement is governed by the catalog
connection's access scope on the Cytario side, not by anything carried in the
registry image.

## Install

```bash
pip install cytario-app-sdk            # registration only
pip install "cytario-app-sdk[runtime]" # + boto3 for in-container use
```

Or with [`uv`](https://docs.astral.sh/uv/):

```bash
uv tool install cytario-app-sdk
```

## Quick start — registering an app

```bash
cytario-app-sdk --registry https://harbor.example.com \
  --user 'robot$cytario-catalog' \
  --secret <robot-token> \
  register examples/cellseg.yaml
```

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
cytario-app-sdk --config cytario-app-sdk.yaml register examples/cellseg.yaml
```

Validate an app-definition without pushing anything:

```bash
cytario-app-sdk --config cytario-app-sdk.yaml register --dry-run examples/cellseg.yaml
```

### App-definition YAML

```yaml
# examples/cellseg.yaml
schemaVersion: 1
applicationId: cellseg
name: Cell Segmentation
description: Runs Cellpose on a 16-bit OME-TIFF.
image:
  repository: cytario/cellseg
  tag: "1.0.0"
parameterSchema:
  - name: diameter
    label: Diameter
    type: number
    required: false
    default: 30
    description: Cellpose diameter estimate in pixels.
dataRoles:
  - name: image
    kind: input
  - name: segmentation
    kind: output
```

`register` fetches the container image manifest (by tag or digest), builds the
app-definition document, attaches it as the `org.cytario.appdef.v1` annotation
on the manifest, and PUTs the manifest back under the original tag. The new
manifest digest (returned by the registry) pins both the image and the
definition. The Cytario runtime reads the annotation back via
`GET /v2/<name>/manifests/<ref>`; no Harbor-specific calls are made.

## Running inside a Cytario compute container

When the Cytario compute plugin submits an analysis job, the running container
receives these environment variables:

| Variable | Description |
|---|---|
| `CYTARIO_BROKER_ENDPOINT` | Full URL of the credential-broker endpoint (e.g. `https://app.cytario.com/api/plugin/broker`). |
| `CYTARIO_BROKER_TOKEN` | Job-scoped offline-capable grant token the broker validates against the running-jobs ledger. |
| `CYTARIO_INPUT_URIS` | JSON array of `s3://bucket/key` URIs for the job's input files. |
| `CYTARIO_OUTPUT_URI` | `s3://bucket/key` URI (prefix) for the job's output. |
| `AWS_BATCH_JOB_ID` | The provider job identifier (injected by AWS Batch). |

The container calls the broker with its token + job id to obtain short-lived STS
credentials (≤ 1 hour, scoped to the submitting user's organization and the
job's validated output prefix), then uses those credentials to read inputs and
write outputs. The SDK provides two ways to handle this:

### Wrapper mode (broker-unaware algorithms)

The SDK is the container entrypoint — it downloads inputs, spawns the algorithm
as a subprocess, and uploads outputs on success. The algorithm only reads/writes
local files; it never touches S3 or the broker.

```dockerfile
# Dockerfile
FROM python:3.12-slim
RUN pip install "cytario-app-sdk[runtime]"
COPY process.py /app/process.py
ENTRYPOINT ["cytario-app-sdk", "run", "--"]
CMD ["python", "/app/process.py"]
```

```bash
# Equivalent to the Dockerfile above, invoked manually:
cytario-app-sdk run --input-dir /data/in --output-dir /data/out -- python /app/process.py
```

Options:

| Option | Default | Description |
|---|---|---|
| `--input-dir` | `/data/in` | Local directory to download inputs into. |
| `--output-dir` | `/data/out` | Local directory to upload outputs from. |
| `--upload-on-failure` | off | Upload outputs even when the algorithm exits non-zero. |
| `--pass-through-env` | off | Keep `CYTARIO_BROKER_*` env vars in the subprocess (for hybrid algorithms). |
| `--refresh-margin` | `300` | Broker credential refresh margin in seconds. |

The wrapper strips `CYTARIO_BROKER_TOKEN` and `CYTARIO_BROKER_ENDPOINT` from the
subprocess environment by default so a broker-unaware algorithm cannot
accidentally leak them. Pass `--pass-through-env` to keep them (for hybrid
algorithms that import the SDK and call the broker themselves).

### Library mode (broker-aware algorithms)

The algorithm's Python code imports the SDK and calls the broker directly. This
is the path for algorithms that stream from S3 (cellpose, OME-TIFF readers,
zarr stores) and want full control over their boto3 usage.

```python
from cytario_app_sdk.broker import BrokerClient

broker = BrokerClient.from_env()
creds = broker.credentials()

import boto3

s3 = boto3.client(
    "s3",
    aws_access_key_id=creds.access_key_id,
    aws_secret_access_key=creds.secret_access_key,
    aws_session_token=creds.session_token,
)
# stream from s3://...
```

Or use the convenience that returns a `boto3.Session` whose credentials refresh
from the broker transparently on every API call:

```python
from cytario_app_sdk.broker import BrokerClient, broker_boto3_session

broker = BrokerClient.from_env()
session = broker_boto3_session(broker)
s3 = session.client("s3")
s3.download_file("my-bucket", "key", "/local/path")
```

The `broker_boto3_session` factory wires a `RefreshableCredentials` whose refresh
callback calls `broker.refresh()`, so boto3's own refresh-before-call logic is
the driver — the algorithm never needs to manage credential lifetimes manually.

For manual S3 sync (download/upload recursive), use the runtime module:

```python
from cytario_app_sdk.broker import BrokerClient, broker_boto3_session
from cytario_app_sdk.runtime import download_inputs, upload_outputs
from pathlib import Path

broker = BrokerClient.from_env()
session = broker_boto3_session(broker)
s3 = session.client("s3")

# Download inputs declared in CYTARIO_INPUT_URIS
download_inputs(s3, ["s3://bucket/inputs/"], Path("/data/in"))

# Run your algorithm here...

# Upload outputs to CYTARIO_OUTPUT_URI
upload_outputs(s3, Path("/data/out"), "s3://bucket/outputs/")
```

### Credential lifecycle

The broker mints STS credentials with a ≤ 1 hour lifetime. The grant token
(`CYTARIO_BROKER_TOKEN`) has a longer lifetime — the realm's maximum
offline-session validity (typically hours). The SDK refreshes credentials on
demand: whenever the cached credentials expire within the refresh margin (5
minutes by default), a fresh mint is requested from the broker. A job running
longer than the realm max cannot refresh anymore; this is the spec's accepted,
risk-assessed limitation.

If the job is cancelled or reaches a terminal state, the Cytario reconciler
removes the job's ledger row, and the broker rejects subsequent calls with a
`GrantRevoked` error — storage access ceases within one credential lifetime of
revocation.

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
