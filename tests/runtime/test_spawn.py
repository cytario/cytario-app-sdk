"""Tests for the wrapper-mode orchestration (download → spawn → upload).

Uses ``moto`` for S3 and real subprocesses (``python -c "..."``) to exercise
the full flow. The broker is not mocked directly — instead, a real boto3
client under moto is passed, simulating the end-state of a broker-backed
session (the credential wiring is tested in ``test_aws.py``).
"""

from __future__ import annotations

import json
import os
import sys

import boto3
import pytest
from moto import mock_aws

from cytario_app_sdk.runtime import run_job

BUCKET = "cytario-test-bucket"
REGION = "us-east-1"


@pytest.fixture
def s3_client() -> boto3.client:
    """A moto-backed S3 client with a fresh bucket."""
    with mock_aws():
        client = boto3.client("s3", region_name=REGION)
        client.create_bucket(Bucket=BUCKET)
        yield client


def _put(s3_client: boto3.client, key: str, body: bytes = b"hello") -> None:
    s3_client.put_object(Bucket=BUCKET, Key=key, Body=body)


def _s3_uri(key: str) -> str:
    return f"s3://{BUCKET}/{key}"


# --- exit-code propagation --------------------------------------------------


class TestExitCodePropagation:
    def test_algorithm_exit_zero_uploads_outputs(self, s3_client: boto3.client, tmp_path: object) -> None:
        """Exit 0 → outputs uploaded."""
        input_dir = tmp_path / "in"  # type: ignore[union-attr]
        output_dir = tmp_path / "out"
        output_dir.mkdir()  # type: ignore[union-attr]
        (output_dir / "result.txt").write_text("ok")  # type: ignore[union-attr]
        # Write the output file before running so it exists for upload.
        # Use a command that exits 0 without touching the output dir.
        exit_code = run_job(
            s3_client,
            input_dir=input_dir,
            output_dir=output_dir,
            sources=[],
            output_uri=_s3_uri("out/"),
            command=[sys.executable, "-c", "import sys; sys.exit(0)"],
        )
        assert exit_code == 0
        assert s3_client.get_object(Bucket=BUCKET, Key="out/result.txt")["Body"].read() == b"ok"

    def test_algorithm_exit_nonzero_skips_upload(self, s3_client: boto3.client, tmp_path: object) -> None:
        """Exit non-zero → outputs NOT uploaded (default)."""
        output_dir = tmp_path / "out"
        output_dir.mkdir()  # type: ignore[union-attr]
        (output_dir / "result.txt").write_text("fail")  # type: ignore[union-attr]
        exit_code = run_job(
            s3_client,
            input_dir=tmp_path / "in",  # type: ignore[union-attr]
            output_dir=output_dir,
            sources=[],
            output_uri=_s3_uri("out/"),
            command=[sys.executable, "-c", "import sys; sys.exit(1)"],
        )
        assert exit_code == 1
        # No objects uploaded.
        objs = s3_client.list_objects_v2(Bucket=BUCKET, Prefix="out/")
        assert objs.get("KeyCount", 0) == 0

    def test_upload_on_failure_uploads_even_on_nonzero(
        self, s3_client: boto3.client, tmp_path: object
    ) -> None:
        """``upload_on_failure=True`` uploads outputs even when the algorithm fails."""
        output_dir = tmp_path / "out"
        output_dir.mkdir()  # type: ignore[union-attr]
        (output_dir / "crash.log").write_text("crashed")  # type: ignore[union-attr]
        exit_code = run_job(
            s3_client,
            input_dir=tmp_path / "in",  # type: ignore[union-attr]
            output_dir=output_dir,
            sources=[],
            output_uri=_s3_uri("out/"),
            command=[sys.executable, "-c", "import sys; sys.exit(2)"],
            upload_on_failure=True,
        )
        assert exit_code == 2
        assert s3_client.get_object(Bucket=BUCKET, Key="out/crash.log")["Body"].read() == b"crashed"

    def test_no_output_uri_skips_upload(self, s3_client: boto3.client, tmp_path: object) -> None:
        """``output_uri=None`` skips the upload phase entirely."""
        output_dir = tmp_path / "out"
        output_dir.mkdir()  # type: ignore[union-attr]
        (output_dir / "result.txt").write_text("x")  # type: ignore[union-attr]
        exit_code = run_job(
            s3_client,
            input_dir=tmp_path / "in",  # type: ignore[union-attr]
            output_dir=output_dir,
            sources=[],
            output_uri=None,
            command=[sys.executable, "-c", "import sys; sys.exit(0)"],
        )
        assert exit_code == 0
        objs = s3_client.list_objects_v2(Bucket=BUCKET, Prefix="out/")
        assert objs.get("KeyCount", 0) == 0


# --- download + upload integration ------------------------------------------


class TestDownloadUploadIntegration:
    def test_download_then_run_then_upload(self, s3_client: boto3.client, tmp_path: object) -> None:
        """Full round-trip: download inputs, algorithm processes, upload outputs."""
        _put(s3_client, "src/image.tif", b"image-bytes")
        input_dir = tmp_path / "in"  # type: ignore[union-attr]
        output_dir = tmp_path / "out"
        output_dir.mkdir()  # type: ignore[union-attr]

        # Algorithm: copy input to output with a transformation.
        algo = (
            f"import shutil; shutil.copy2(r'{input_dir}/image.tif', "  # type: ignore[union-attr]
            f"r'{output_dir}/processed.tif')"  # type: ignore[union-attr]
        )
        exit_code = run_job(
            s3_client,
            input_dir=input_dir,
            output_dir=output_dir,
            sources=[_s3_uri("src/")],
            output_uri=_s3_uri("dst/"),
            command=[sys.executable, "-c", algo],
        )
        assert exit_code == 0
        assert s3_client.get_object(Bucket=BUCKET, Key="dst/processed.tif")["Body"].read() == b"image-bytes"

    def test_empty_sources_skips_download(self, s3_client: boto3.client, tmp_path: object) -> None:
        """No sources → no download, algorithm still runs."""
        exit_code = run_job(
            s3_client,
            input_dir=tmp_path / "in",  # type: ignore[union-attr]
            output_dir=tmp_path / "out",  # type: ignore[union-attr]
            sources=[],
            output_uri=None,
            command=[sys.executable, "-c", "import sys; sys.exit(0)"],
        )
        assert exit_code == 0


# --- env stripping ----------------------------------------------------------


class TestEnvStripping:
    def test_broker_vars_stripped_by_default(self, s3_client: boto3.client, tmp_path: object) -> None:
        """CYTARIO_BROKER_TOKEN and CYTARIO_BROKER_ENDPOINT are removed from the subprocess env."""
        env = {
            "CYTARIO_BROKER_TOKEN": "secret-token",
            "CYTARIO_BROKER_ENDPOINT": "https://broker.example.com",
            "AWS_BATCH_JOB_ID": "job-1",
            "PATH": os.environ.get("PATH", ""),
        }
        # Algorithm: dump env to a file we can inspect.
        marker = tmp_path / "env_dump.json"  # type: ignore[union-attr]
        algo = f"import json,os; json.dump(dict(os.environ), open(r'{marker}','w'))"
        run_job(
            s3_client,
            input_dir=tmp_path / "in",  # type: ignore[union-attr]
            output_dir=tmp_path / "out",  # type: ignore[union-attr]
            sources=[],
            output_uri=None,
            command=[sys.executable, "-c", algo],
            env=env,
        )
        dumped = json.loads(marker.read_text())  # type: ignore[union-attr]
        assert "CYTARIO_BROKER_TOKEN" not in dumped
        assert "CYTARIO_BROKER_ENDPOINT" not in dumped
        # Non-broker vars are preserved.
        assert dumped["AWS_BATCH_JOB_ID"] == "job-1"

    def test_pass_through_env_keeps_broker_vars(self, s3_client: boto3.client, tmp_path: object) -> None:
        """``pass_through_env=True`` keeps broker vars in the subprocess env."""
        env = {
            "CYTARIO_BROKER_TOKEN": "secret-token",
            "CYTARIO_BROKER_ENDPOINT": "https://broker.example.com",
            "PATH": os.environ.get("PATH", ""),
        }
        marker = tmp_path / "env_dump.json"  # type: ignore[union-attr]
        algo = f"import json,os; json.dump(dict(os.environ), open(r'{marker}','w'))"
        run_job(
            s3_client,
            input_dir=tmp_path / "in",  # type: ignore[union-attr]
            output_dir=tmp_path / "out",  # type: ignore[union-attr]
            sources=[],
            output_uri=None,
            command=[sys.executable, "-c", algo],
            env=env,
            pass_through_env=True,
        )
        dumped = json.loads(marker.read_text())  # type: ignore[union-attr]
        assert dumped["CYTARIO_BROKER_TOKEN"] == "secret-token"
        assert dumped["CYTARIO_BROKER_ENDPOINT"] == "https://broker.example.com"

    def test_subprocess_inherits_path(self, s3_client: boto3.client, tmp_path: object) -> None:
        """The subprocess gets a usable PATH so `python` etc. resolve."""
        env = {"PATH": os.environ.get("PATH", ""), "CYTARIO_BROKER_TOKEN": "x"}
        exit_code = run_job(
            s3_client,
            input_dir=tmp_path / "in",  # type: ignore[union-attr]
            output_dir=tmp_path / "out",  # type: ignore[union-attr]
            sources=[],
            output_uri=None,
            command=[sys.executable, "-c", "import sys; sys.exit(0)"],
            env=env,
        )
        assert exit_code == 0


# --- infrastructure failures ------------------------------------------------


class TestInfrastructureFailure:
    def test_download_failure_returns_70(self, s3_client: boto3.client, tmp_path: object) -> None:
        """A malformed S3 URI during download → exit 70 (infrastructure failure)."""
        exit_code = run_job(
            s3_client,
            input_dir=tmp_path / "in",  # type: ignore[union-attr]
            output_dir=tmp_path / "out",  # type: ignore[union-attr]
            sources=["not-an-s3-uri"],
            output_uri=None,
            command=[sys.executable, "-c", "import sys; sys.exit(0)"],
        )
        assert exit_code == 70

    def test_upload_failure_returns_70_on_success(self, s3_client: boto3.client, tmp_path: object) -> None:
        """A malformed output URI during upload → exit 70 (when algorithm succeeded)."""
        output_dir = tmp_path / "out"
        output_dir.mkdir()  # type: ignore[union-attr]
        (output_dir / "result.txt").write_text("ok")  # type: ignore[union-attr]
        exit_code = run_job(
            s3_client,
            input_dir=tmp_path / "in",  # type: ignore[union-attr]
            output_dir=output_dir,
            sources=[],
            output_uri="not-an-s3-uri",
            command=[sys.executable, "-c", "import sys; sys.exit(0)"],
        )
        assert exit_code == 70

    def test_upload_failure_keeps_algorithm_exit_on_failure(
        self, s3_client: boto3.client, tmp_path: object
    ) -> None:
        """When the algorithm already failed AND upload fails, keep the algorithm's code."""
        output_dir = tmp_path / "out"
        output_dir.mkdir()  # type: ignore[union-attr]
        (output_dir / "result.txt").write_text("fail")  # type: ignore[union-attr]
        exit_code = run_job(
            s3_client,
            input_dir=tmp_path / "in",  # type: ignore[union-attr]
            output_dir=output_dir,
            sources=[],
            output_uri="not-an-s3-uri",
            command=[sys.executable, "-c", "import sys; sys.exit(42)"],
            upload_on_failure=True,
        )
        # Algorithm's exit code (42) is preserved — the upload failure doesn't mask it.
        assert exit_code == 42
