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


def git_state() -> dict[str, Any]:
    """Commit of this repository and whether the working tree differs from it.

    ``dirty`` counts modified, staged, and untracked (non-ignored) files. Returns
    ``{"commit": None, "dirty": None}`` outside a git checkout or before any commit.
    """
    root = Path(__file__).resolve().parents[2]
    head = subprocess.run(
        ["git", "rev-parse", "--verify", "HEAD"],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
    )
    if head.returncode != 0:
        return {"commit": None, "dirty": None}
    status = subprocess.run(
        ["git", "status", "--porcelain"], cwd=root, capture_output=True, text=True, check=False
    )
    return {"commit": head.stdout.strip(), "dirty": bool(status.stdout.strip())}


def codetrace_git_commit() -> str | None:
    """Commit SHA for manifests, suffixed with '+dirty' if the tree has changes."""
    st = git_state()
    if st["commit"] is None:
        return None
    return str(st["commit"]) + ("+dirty" if st["dirty"] else "")
