"""Minimal helper package installed at the workspace root.

This module exists only so that Poetry can produce console scripts
from the workspace without touching the real packages under packages/.
The `main` function simply proxies to the real `llmos_bridge` CLI.
"""

from __future__ import annotations


def main() -> None:
    """Entry point for the root-installed `llmos-bridge` script.

    All the real logic lives under ``llmos_bridge.cli.main``; we import it
    lazily to avoid pulling heavy dependencies when the root package is
    installed for tooling only.
    """

    # import inside the function to keep startup cheap when the script is
    # not invoked (e.g. during CI dependency resolution).
    from llmos_bridge.cli.main import app

    app()
