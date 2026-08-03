"""S3 sync primitives for wrapper mode (download inputs / upload outputs).

A thin boto3 layer over ``list_objects_v2`` + ``download_fileobj`` /
``upload_fileobj``. Recursive, overwrite-always — matches the semantics of
``aws s3 cp --recursive`` from the processing PoC. Streaming 8 MiB parts via
boto3's threaded transfer manager; no ``aiobotocore`` dependency.

The functions take a ``boto3.client("s3")`` (or any duck-typed S3 client)
explicitly, so tests inject a moto-backed client and library-mode callers can
pass a session built from :func:`cytario_app_sdk.broker.broker_boto3_session`.

Sources and destinations are full ``s3://bucket/key`` URIs (the plugin resolves
``RunPayload.input``/``output`` to these at submit time and injects
``CYTARIO_INPUT_URIS`` / ``CYTARIO_OUTPUT_URI`` into the container env).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

    import boto3

__all__ = ["S3Uri", "download_inputs", "upload_outputs"]


@dataclass(frozen=True)
class S3Uri:
    """Parsed ``s3://bucket/key`` URI.

    The bucket name is validated loosely (AWS rules are region-specific and
    the broker-minted credentials scope the actual access; here we only guard
    against obviously malformed input). The key is allowed to be empty (a
    bare bucket reference lists the whole bucket).
    """

    bucket: str
    key: str


_S3_URI_RE = re.compile(r"^s3://(?P<bucket>[a-z0-9][a-z0-9.-]*[a-z0-9])/(?P<key>.*)$", re.IGNORECASE)


def parse_s3_uri(uri: str) -> S3Uri:
    """Parse an ``s3://bucket/key`` URI into an :class:`S3Uri`.

    Raises ``ValueError`` on a malformed URI — bucket names must be lowercase
    DNS-hostnames per AWS rules (we accept uppercase for leniency and let the
    real S3 service reject on access).
    """
    match = _S3_URI_RE.match(uri)
    if match is None:
        msg = f"not a valid s3:// URI: {uri!r}"
        raise ValueError(msg)
    return S3Uri(bucket=match["bucket"], key=match["key"])


def _list_objects(s3: boto3.client, uri: S3Uri) -> list[str]:
    """List all object keys under ``uri.key`` (prefix match, recursive)."""
    paginator = s3.get_paginator("list_objects_v2")
    keys: list[str] = []
    for page in paginator.paginate(Bucket=uri.bucket, Prefix=uri.key):
        keys.extend(obj["Key"] for obj in page.get("Contents", []))
    return keys


def download_inputs(
    s3: boto3.client,
    sources: list[str],
    dest_dir: Path,
) -> list[Path]:
    """Download each source URI (recursively) into ``dest_dir``.

    Each entry in ``sources`` is an ``s3://`` URI. A URI whose key points at a
    "directory" (no specific object by that exact key) is listed recursively
    and all matching objects are downloaded, preserving the path relative to
    the URI's key prefix. A URI whose key points at a single object downloads
    just that object.

    Returns the list of local file paths written, in iteration order. Existing
    local files are overwritten (matches PoC semantics).
    """
    dest_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for uri_str in sources:
        uri = parse_s3_uri(uri_str)
        keys = _list_objects(s3, uri)
        for key in keys:
            rel = _relative_key(key, uri.key)
            local_path = dest_dir / rel
            local_path.parent.mkdir(parents=True, exist_ok=True)
            with local_path.open("wb") as f:
                s3.download_fileobj(uri.bucket, key, f)
            written.append(local_path)
    return written


def upload_outputs(
    s3: boto3.client,
    local_dir: Path,
    dest_uri: str,
) -> list[str]:
    """Recursively upload every file under ``local_dir`` to ``dest_uri``.

    ``dest_uri`` is an ``s3://bucket/prefix`` URI. The relative path of each
    file under ``local_dir`` is appended to the prefix, preserving directory
    structure. Returns the list of S3 object keys written, in iteration order.
    Overwrites any existing object at the same key (matches PoC semantics).
    """
    if not local_dir.is_dir():
        msg = f"local_dir is not a directory: {local_dir}"
        raise ValueError(msg)
    dest = parse_s3_uri(dest_uri)
    dest_prefix = dest.key.rstrip("/")
    written: list[str] = []
    for path in sorted(local_dir.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(local_dir).as_posix()
        key = f"{dest_prefix}/{rel}" if dest_prefix else rel
        with path.open("rb") as f:
            s3.upload_fileobj(f, dest.bucket, key)
        written.append(key)
    return written


def _relative_key(key: str, prefix: str) -> str:
    """Strip ``prefix`` from ``key`` to get the path relative to the source.

    ``prefix`` is the directory portion of the source URI; a trailing slash is
    stripped so ``prefix/sub/file`` → ``sub/file``. When the prefix matches the
    key exactly (single-object URI), the result is the basename.
    """
    stripped = prefix.rstrip("/")
    if not stripped:
        return key
    if key == stripped:
        return key.rsplit("/", maxsplit=1)[-1]
    return key[len(stripped) + 1 :] if key.startswith(f"{stripped}/") else key


__all__ = ["S3Uri", "download_inputs", "parse_s3_uri", "upload_outputs"]
