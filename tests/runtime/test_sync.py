"""Tests for the S3 sync primitives (wrapper-mode download/upload).

Backed by ``moto``'s in-memory S3 so the tests run without network access. The
``s3_client`` fixture creates a fresh bucket per test; the broker is not
involved here (that's exercised in ``test_aws.py``); these tests target the
sync layer in isolation.
"""

from __future__ import annotations

import boto3
import pytest
from moto import mock_aws

from cytario_app_sdk.runtime import (
    download_inputs,
    parse_s3_uri,
    upload_outputs,
)

BUCKET = "cytario-test-bucket"
REGION = "us-east-1"


@pytest.fixture
def s3_client() -> boto3.client:
    """A moto-backed S3 client with a fresh bucket.

    ``mock_aws()`` is entered here so the mock is scoped to the fixture's
    lifecycle. AWS creds must be set for boto3 to construct the client even
    under moto.
    """
    with mock_aws():
        client = boto3.client("s3", region_name=REGION)
        client.create_bucket(Bucket=BUCKET)
        yield client


def _put(s3_client: boto3.client, key: str, body: bytes = b"hello") -> None:
    """Upload a single object to the test bucket."""
    s3_client.put_object(Bucket=BUCKET, Key=key, Body=body)


# --- parse_s3_uri ------------------------------------------------------------


class TestParseS3Uri:
    def test_parse_bucket_and_key(self) -> None:
        uri = parse_s3_uri("s3://my-bucket/path/to/file.txt")
        assert uri.bucket == "my-bucket"
        assert uri.key == "path/to/file.txt"

    def test_parse_bucket_only(self) -> None:
        """An empty key means a bare bucket reference (lists the whole bucket)."""
        uri = parse_s3_uri("s3://my-bucket/")
        assert uri.bucket == "my-bucket"
        assert uri.key == ""

    def test_parse_rejects_non_s3_scheme(self) -> None:
        with pytest.raises(ValueError, match="not a valid s3:// URI"):
            parse_s3_uri("https://example.com/path")

    def test_parse_rejects_empty(self) -> None:
        with pytest.raises(ValueError, match="not a valid s3:// URI"):
            parse_s3_uri("")

    def test_parse_accepts_uppercase_bucket(self) -> None:
        """We accept uppercase for leniency; the real S3 service rejects it."""
        uri = parse_s3_uri("s3://My-Bucket/key")
        assert uri.bucket == "My-Bucket"
        assert uri.key == "key"


# --- download_inputs ---------------------------------------------------------


class TestDownloadInputs:
    def test_download_single_object(self, s3_client: boto3.client, tmp_path: object) -> None:
        """A URI pointing at a single object downloads just that object."""
        _put(s3_client, "inputs/file1.txt", b"content-1")
        dest = tmp_path  # type: ignore[assignment]
        written = download_inputs(s3_client, [f"s3://{BUCKET}/inputs/file1.txt"], dest)  # type: ignore[arg-type]
        assert len(written) == 1
        assert written[0].read_bytes() == b"content-1"
        assert written[0].name == "file1.txt"

    def test_download_directory_recursive(self, s3_client: boto3.client, tmp_path: object) -> None:
        """A URI whose key is a prefix lists all matching objects recursively."""
        _put(s3_client, "inputs/file1.txt", b"one")
        _put(s3_client, "inputs/sub/file2.txt", b"two")
        _put(s3_client, "inputs/sub/deep/file3.txt", b"three")
        dest = tmp_path  # type: ignore[assignment]
        written = download_inputs(s3_client, [f"s3://{BUCKET}/inputs/"], dest)  # type: ignore[arg-type]
        assert len(written) == 3
        names = {p.relative_to(dest).as_posix() for p in written}  # type: ignore[union-attr]
        assert names == {"file1.txt", "sub/file2.txt", "sub/deep/file3.txt"}
        # Contents preserved.
        by_name = {p.relative_to(dest).as_posix(): p.read_bytes() for p in written}  # type: ignore[union-attr]
        assert by_name["file1.txt"] == b"one"
        assert by_name["sub/file2.txt"] == b"two"
        assert by_name["sub/deep/file3.txt"] == b"three"

    def test_download_multiple_sources(self, s3_client: boto3.client, tmp_path: object) -> None:
        """Multiple source URIs are downloaded into the same dest dir."""
        _put(s3_client, "src-a/a.txt", b"a")
        _put(s3_client, "src-b/b.txt", b"b")
        dest = tmp_path  # type: ignore[assignment]
        written = download_inputs(
            s3_client,
            [f"s3://{BUCKET}/src-a/", f"s3://{BUCKET}/src-b/"],
            dest,  # type: ignore[arg-type]
        )
        assert len(written) == 2
        names = {p.name for p in written}
        assert names == {"a.txt", "b.txt"}

    def test_download_overwrites_existing_local(self, s3_client: boto3.client, tmp_path: object) -> None:
        """A pre-existing local file with the same name is overwritten."""
        _put(s3_client, "file.txt", b"remote")
        dest = tmp_path  # type: ignore[assignment]
        (dest / "file.txt").write_bytes(b"local-stale")  # type: ignore[union-attr]
        download_inputs(s3_client, [f"s3://{BUCKET}/file.txt"], dest)  # type: ignore[arg-type]
        assert (dest / "file.txt").read_bytes() == b"remote"  # type: ignore[union-attr]

    def test_download_creates_dest_dir(self, s3_client: boto3.client, tmp_path: object) -> None:
        """If the dest dir does not exist, it is created."""
        _put(s3_client, "file.txt", b"x")
        dest = tmp_path / "nested" / "deeper"  # type: ignore[union-attr]
        written = download_inputs(s3_client, [f"s3://{BUCKET}/file.txt"], dest)
        assert dest.is_dir()
        assert written[0].read_bytes() == b"x"

    def test_download_empty_prefix_writes_nothing(self, s3_client: boto3.client, tmp_path: object) -> None:
        """A prefix matching no objects writes no files (no error)."""
        dest = tmp_path  # type: ignore[assignment]
        written = download_inputs(s3_client, [f"s3://{BUCKET}/does-not-exist/"], dest)  # type: ignore[arg-type]
        assert written == []


# --- upload_outputs ----------------------------------------------------------


class TestUploadOutputs:
    def test_upload_flat_directory(self, s3_client: boto3.client, tmp_path: object) -> None:
        """All files in a flat local dir are uploaded under the dest prefix."""
        local = tmp_path  # type: ignore[assignment]
        (local / "a.txt").write_bytes(b"alpha")  # type: ignore[union-attr]
        (local / "b.txt").write_bytes(b"beta")  # type: ignore[union-attr]
        keys = upload_outputs(s3_client, local, f"s3://{BUCKET}/out/")  # type: ignore[arg-type]
        assert set(keys) == {"out/a.txt", "out/b.txt"}
        assert s3_client.get_object(Bucket=BUCKET, Key="out/a.txt")["Body"].read() == b"alpha"

    def test_upload_nested_directories(self, s3_client: boto3.client, tmp_path: object) -> None:
        """Subdirectory structure is preserved in the S3 keys."""
        local = tmp_path  # type: ignore[assignment]
        (local / "top.txt").write_bytes(b"top")  # type: ignore[union-attr]
        (local / "sub").mkdir()  # type: ignore[union-attr]
        (local / "sub" / "nested.txt").write_bytes(b"nested")  # type: ignore[union-attr]
        (local / "sub" / "deep").mkdir()  # type: ignore[union-attr]
        (local / "sub" / "deep" / "x.bin").write_bytes(b"x")  # type: ignore[union-attr]
        keys = upload_outputs(s3_client, local, f"s3://{BUCKET}/results")  # type: ignore[arg-type]
        assert set(keys) == {
            "results/top.txt",
            "results/sub/nested.txt",
            "results/sub/deep/x.bin",
        }

    def test_upload_overwrites_existing_object(self, s3_client: boto3.client, tmp_path: object) -> None:
        """An existing S3 object at the same key is overwritten."""
        _put(s3_client, "out/file.txt", b"stale-remote")
        local = tmp_path  # type: ignore[assignment]
        (local / "file.txt").write_bytes(b"fresh-local")  # type: ignore[union-attr]
        upload_outputs(s3_client, local, f"s3://{BUCKET}/out/")  # type: ignore[arg-type]
        assert s3_client.get_object(Bucket=BUCKET, Key="out/file.txt")["Body"].read() == b"fresh-local"

    def test_upload_empty_directory_writes_nothing(self, s3_client: boto3.client, tmp_path: object) -> None:
        """An empty local dir uploads no objects."""
        local = tmp_path  # type: ignore[assignment]
        keys = upload_outputs(s3_client, local, f"s3://{BUCKET}/out/")  # type: ignore[arg-type]
        assert keys == []

    def test_upload_rejects_nonexistent_dir(self, s3_client: boto3.client, tmp_path: object) -> None:
        """A non-directory local_dir raises ValueError."""
        local = tmp_path / "missing"  # type: ignore[union-attr]
        with pytest.raises(ValueError, match="not a directory"):
            upload_outputs(s3_client, local, f"s3://{BUCKET}/out/")  # type: ignore[arg-type]

    def test_upload_to_bucket_root_prefix(self, s3_client: boto3.client, tmp_path: object) -> None:
        """A dest URI with no prefix writes keys at the bucket root."""
        local = tmp_path  # type: ignore[assignment]
        (local / "file.txt").write_bytes(b"x")  # type: ignore[union-attr]
        keys = upload_outputs(s3_client, local, f"s3://{BUCKET}/")  # type: ignore[arg-type]
        assert keys == ["file.txt"]


# --- round-trip -------------------------------------------------------------


def test_download_then_upload_round_trips(s3_client: boto3.client, tmp_path: object) -> None:
    """A download → local mutation → upload cycle preserves structure."""
    _put(s3_client, "src/a.txt", b"alpha")
    _put(s3_client, "src/sub/b.txt", b"beta")
    work = tmp_path  # type: ignore[assignment]
    downloaded = download_inputs(s3_client, [f"s3://{BUCKET}/src/"], work)  # type: ignore[arg-type]
    assert len(downloaded) == 2
    # Mutate: add a result file alongside the inputs.
    (work / "result.txt").write_bytes(b"combined")  # type: ignore[union-attr]
    keys = upload_outputs(s3_client, work, f"s3://{BUCKET}/dst/")  # type: ignore[arg-type]
    assert set(keys) == {"dst/a.txt", "dst/sub/b.txt", "dst/result.txt"}
    assert s3_client.get_object(Bucket=BUCKET, Key="dst/result.txt")["Body"].read() == b"combined"
