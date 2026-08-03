"""Runtime sub-package — S3 sync primitives for wrapper mode.

The runtime is the in-container execution surface: download inputs, spawn the
algorithm, upload outputs. It depends on the optional ``boto3`` extra
(``pip install cytario-app-sdk[runtime]``); library-mode consumers that bring
their own boto3 only need the :mod:`cytario_app_sdk.broker` sub-package.
"""

from __future__ import annotations

from cytario_app_sdk.runtime.spawn import run_job
from cytario_app_sdk.runtime.sync import S3Uri, download_inputs, parse_s3_uri, upload_outputs

__all__ = [
    "S3Uri",
    "download_inputs",
    "parse_s3_uri",
    "run_job",
    "upload_outputs",
]
