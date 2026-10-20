"""Execution environment capture for run manifests (no hostnames or user paths)."""

from __future__ import annotations

import platform
import subprocess
import sys
from importlib import metadata
from pathlib import Path
from typing import Any

_PACKAGES = (
    "codetrace",
    "pydantic",
    "pyyaml",
    "huggingface-hub",
    "pyarrow",
    "anthropic",
    "swebench",
)


def _version(pkg: str) -> str | None:
    try:
        return metadata.version(pkg)
    except metadata.PackageNotFoundError:
        return None


def environment_info() -> dict[str, Any]:
    git_v = subprocess.run(["git", "--version"], capture_output=True, text=True, check=False)
    return {
        "python": sys.version.split()[0],
        "implementation": platform.python_implementation(),
        "os": platform.system(),
        "os_release": platform.release(),
        "mac_ver": platform.mac_ver()[0] or None,
        "machine": platform.machine(),
        "git": git_v.stdout.strip() or None,
        "packages": {p: _version(p) for p in _PACKAGES},
    }


def codetrace_git_commit() -> str | None:
    """Commit of this repository (with '+dirty' if there are uncommitted changes)."""
    root = Path(__file__).resolve().parents[2]
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=root, capture_output=True, text=True, check=False
    )
    if head.returncode != 0:
        return None
    dirty = subprocess.run(
        ["git", "status", "--porcelain", "--untracked-files=no"],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
    )
    return head.stdout.strip() + ("+dirty" if dirty.stdout.strip() else "")
