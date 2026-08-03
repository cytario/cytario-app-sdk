"""Wrapper-mode orchestration: download → spawn → upload (SRS-CY-416101).

The wrapper is the in-container entrypoint for broker-unaware algorithms. It
boots a broker-backed boto3 session, downloads the job's declared inputs to a
local directory, spawns the algorithm as a subprocess, and uploads the
algorithm's output directory back to S3 on success (or on failure with
``--upload-on-failure``). The subprocess inherits a cleaned environment — the
broker token and endpoint are stripped by default so a broker-unaware
algorithm cannot accidentally leak them; ``--pass-through-env`` keeps them for
hybrid algorithms that import the SDK and call the broker themselves.

boto3's :class:`~botocore.credentials.RefreshableCredentials` (wired in
:mod:`cytario_app_sdk.broker.aws`) refresh from the broker before every S3
call when the cached credentials are near expiry, so the download and upload
phases are automatically covered without an explicit refresh thread. A
broker revocation (cancel or terminal state) surfaces as
:class:`~cytario_app_sdk.broker.GrantRevoked` on the next S3 call — the
wrapper catches it, logs a clear message, and exits non-zero.
"""

from __future__ import annotations

import logging
import os
import subprocess
from typing import TYPE_CHECKING

from cytario_app_sdk.broker.exceptions import BrokerError
from cytario_app_sdk.runtime.sync import download_inputs, upload_outputs

if TYPE_CHECKING:
    from pathlib import Path

    import boto3

__all__ = ["run_job"]

_logger = logging.getLogger("cytario_app_sdk.runtime.spawn")

#: Environment variables stripped from the subprocess env by default so a
#: broker-unaware algorithm cannot accidentally leak the grant token. Kept
#: only when ``pass_through_env=True`` is set (hybrid algorithms that import
#: the SDK and call the broker themselves).
_STRIPPED_ENV_VARS = frozenset(
    {
        "CYTARIO_BROKER_TOKEN",
        "CYTARIO_BROKER_ENDPOINT",
    }
)


def run_job(
    s3_client: boto3.client,
    *,
    input_dir: Path,
    output_dir: Path,
    sources: list[str],
    output_uri: str | None,
    command: list[str],
    upload_on_failure: bool = False,
    pass_through_env: bool = False,
    env: dict[str, str] | None = None,
) -> int:
    """Download inputs, spawn the algorithm, upload outputs.

    Args:
        s3_client: A boto3 S3 client (typically built from
            :func:`cytario_app_sdk.broker.broker_boto3_session`).
        input_dir: Local directory to download inputs into (created if missing).
        output_dir: Local directory to upload outputs from (created if missing).
        sources: List of ``s3://`` URIs to download. Empty list skips download.
        output_uri: ``s3://`` URI to upload outputs to. ``None`` skips upload.
        command: The algorithm command as an argv list (e.g.
            ``["python", "/app/process.py"]``).
        upload_on_failure: Upload outputs even when the algorithm exits non-zero.
            Default ``False`` — outputs are uploaded only on success.
        pass_through_env: Keep broker env vars in the subprocess environment.
            Default ``False`` — strip ``CYTARIO_BROKER_TOKEN`` and
            ``CYTARIO_BROKER_ENDPOINT``.
        env: The base environment for the subprocess. ``None`` inherits the
            parent process environment (after stripping). Explicit in tests.

    Returns:
        The algorithm's exit code. A broker/infrastructure failure returns 70
        (``EX_SOFTWARE`` from BSD sysexits) to distinguish it from an algorithm
        failure.

    """
    # --- Download phase ----------------------------------------------------
    if sources:
        try:
            written = download_inputs(s3_client, sources, input_dir)
            _logger.info("downloaded %d file(s) to %s", len(written), input_dir)
        except BrokerError as exc:
            _logger.error("broker denied input download: %s", exc)
            return 70
        except Exception as exc:
            _logger.error("input download failed: %s", exc)
            return 70

    # --- Spawn phase -------------------------------------------------------
    output_dir.mkdir(parents=True, exist_ok=True)
    sub_env = _build_subprocess_env(
        env if env is not None else dict(os.environ),
        pass_through_env=pass_through_env,
    )
    _logger.info("spawning algorithm: %s", " ".join(command))
    result = subprocess.run(command, env=sub_env, check=False)  # noqa: S603
    exit_code = result.returncode
    _logger.info("algorithm exited with code %d", exit_code)

    # --- Upload phase ------------------------------------------------------
    should_upload = output_uri is not None and (exit_code == 0 or upload_on_failure)
    if should_upload and output_uri is not None:
        try:
            keys = upload_outputs(s3_client, output_dir, output_uri)
            _logger.info("uploaded %d file(s) to %s", len(keys), output_uri)
        except BrokerError as exc:
            _logger.error("broker denied output upload: %s", exc)
            return 70 if exit_code == 0 else exit_code
        except Exception as exc:
            _logger.error("output upload failed: %s", exc)
            return 70 if exit_code == 0 else exit_code

    return exit_code


def _build_subprocess_env(
    base: dict[str, str],
    *,
    pass_through_env: bool,
) -> dict[str, str]:
    """Return the environment for the subprocess, stripping broker vars.

    When ``pass_through_env`` is True, the environment is passed through
    unchanged (for hybrid algorithms that import the SDK and call the broker
    themselves). Otherwise, the broker token and endpoint are removed so a
    broker-unaware algorithm cannot accidentally leak or log them.
    """
    if pass_through_env:
        return base
    return {k: v for k, v in base.items() if k not in _STRIPPED_ENV_VARS}
