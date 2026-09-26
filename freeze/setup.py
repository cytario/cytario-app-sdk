"""cx_Freeze setup for the self-contained cytario-app-sdk runtime binary.

Builds the ``cytario-app-sdk`` console entry point into a standalone Linux
executable that apps can ``COPY --from=`` the published runtime base image,
so a non-Python algorithm container gets wrapper mode (broker-backed S3
sync) without installing a Python toolchain. Run from the repo root:

    uv sync --extra runtime
    uv run --with cx_Freeze python freeze/setup.py build

The output lands in ``build/exe.linux-x86_64-3.XX/``. CI stages it into the
runtime image (freeze/Dockerfile.runtime) at /opt/sdk/bin/cytario-app-sdk.
"""

from __future__ import annotations

import sysconfig
from pathlib import Path

from cx_Freeze import Executable, setup


def _dist_info(name: str) -> Path:
    """Locate a package's dist-info directory in the build venv.

    typer-config reads its own version via importlib.metadata at import
    time, so the frozen app needs the dist-info present. cx_Freeze copies
    package code for ``includes`` but not distribution metadata; include it
    explicitly.
    """
    site = Path(sysconfig.get_paths()["purelib"])
    for pattern in (f"{name}*.dist-info", f"{name.replace('-', '_')}*.dist-info"):
        if found := sorted(site.glob(pattern)):
            return found[0]
    msg = f"dist-info for {name} not found under {site}"
    raise SystemExit(msg)


setup(
    name="cytario-app-sdk-runtime",
    version="0.0.0",  # stamped by CI with the SDK version
    description="Frozen cytario-app-sdk wrapper for non-Python app containers",
    options={
        "build_exe": {
            "includes": [
                "cytario_app_sdk",
                "typer",
                "click",
                "boto3",
                "botocore",
                "s3transfer",
                "urllib3",
                "certifi",
                "pydantic",
                "httpx",
                "yaml",
                # rich lazy-imports the per-Unicode-version width table on
                # first wide-glyph render (typer help/panels); without the
                # table the excepthook itself crashes when printing errors.
                "rich._unicode_data.unicode17-0-0",
            ],
            # typer-config reads its version via importlib.metadata at
            # import time; cx_Freeze copies package code (lib/) but not
            # dist-info. importlib.metadata in the frozen app only scans
            # lib/, so the dist-info must land there, next to the package.
            "include_files": [
                (_dist_info("typer-config"), f"lib/{_dist_info('typer-config').name}"),
            ],
            "silent_level": 1,
        },
    },
    executables=[
        Executable(
            "src/cytario_app_sdk/cli.py",
            target_name="cytario-app-sdk",
        ),
    ],
)
