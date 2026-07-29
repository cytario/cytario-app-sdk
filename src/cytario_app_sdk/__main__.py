"""Entry point shim so `python -m cytario_app_sdk` works."""

from __future__ import annotations

from cytario_app_sdk.cli import app

if __name__ == "__main__":
    app()
