"""Sanitized repository workspaces.

The upstream clone contains every later commit, including the reference fix.
Agents therefore never see it. Instead we export the tree at ``base_commit``
into a fresh directory and create a new single-commit git repository there,
used only to compute the agent's diff.
"""

from __future__ import annotations

import io
import os
import subprocess
import tarfile
from dataclasses import dataclass
from pathlib import Path

_GIT_ENV = {
    "GIT_AUTHOR_NAME": "codetrace",
    "GIT_AUTHOR_EMAIL": "codetrace@localhost",
    "GIT_COMMITTER_NAME": "codetrace",
    "GIT_COMMITTER_EMAIL": "codetrace@localhost",
    "GIT_AUTHOR_DATE": "2000-01-01T00:00:00+0000",
    "GIT_COMMITTER_DATE": "2000-01-01T00:00:00+0000",
    "GIT_CONFIG_NOSYSTEM": "1",
    "GIT_TERMINAL_PROMPT": "0",
}


def git(*args: str, cwd: Path | None = None, input_bytes: bytes | None = None) -> bytes:
    env = {**os.environ, **_GIT_ENV, "HOME": os.environ.get("HOME", "/tmp")}
    proc = subprocess.run(
        ["git", "-c", "core.quotepath=false", "-c", "commit.gpgsign=false", *args],
        cwd=cwd,
        input=input_bytes,
        capture_output=True,
        env=env,
        check=False,
    )
    if proc.returncode != 0:
        stderr = proc.stderr.decode(errors="replace")
        raise RuntimeError(f"git {' '.join(args)} failed ({proc.returncode}): {stderr}")
    return proc.stdout


def repo_url(repo: str) -> str:
    return f"https://github.com/{repo}.git"


def ensure_mirror(source: str, mirror_dir: Path, commits: list[str] | None = None) -> Path:
    """Bare cache repository for ``source`` (URL or local path); reused if present.

    With ``commits``, only those commits are fetched at depth 1 (one tree snapshot
    each, no history), which is far smaller for large repositories and means the
    cache never holds later commits. Without ``commits``, a blobless clone is made.
    """
    mirror_dir.parent.mkdir(parents=True, exist_ok=True)
    if not (mirror_dir / "HEAD").exists():
        if commits is None:
            git("clone", "--quiet", "--bare", "--filter=blob:none", source, str(mirror_dir))
            return mirror_dir
        git("init", "--quiet", "--bare", str(mirror_dir))
    for sha in commits or []:
        have = subprocess.run(
            ["git", "cat-file", "-e", f"{sha}^{{commit}}"], cwd=mirror_dir, capture_output=True
        )
        if have.returncode != 0:
            ipv4 = ["--ipv4"] if os.environ.get("CODETRACE_FORCE_IPV4") == "1" else []
            git("fetch", "--quiet", *ipv4, "--depth", "1", "--no-tags", source, sha, cwd=mirror_dir)
    return mirror_dir


@dataclass(frozen=True)
class Workspace:
    path: Path
    base_commit: str  # upstream commit the snapshot was exported from
    baseline_sha: str  # commit id in the sanitized repo

    def diff(self) -> str:
        """Unified diff of all agent changes against the baseline (SWE-bench format)."""
        git("add", "-A", cwd=self.path)
        out = git(
            "diff", "--cached", "--no-color", "--no-ext-diff", self.baseline_sha, cwd=self.path
        )
        return out.decode("utf-8", errors="surrogateescape")


def create_workspace(mirror_dir: Path, base_commit: str, dest: Path) -> Workspace:
    if dest.exists() and any(dest.iterdir()):
        raise FileExistsError(f"workspace {dest} already exists and is not empty")
    dest.mkdir(parents=True, exist_ok=True)
    archive = git("archive", "--format=tar", base_commit, cwd=mirror_dir)
    with tarfile.open(fileobj=io.BytesIO(archive)) as tar:
        tar.extractall(dest, filter="data")
    git("init", "--quiet", "-b", "baseline", cwd=dest)
    # --force: the baseline must equal the upstream tree exactly, including files that
    # upstream tracks under .gitignore'd paths (e.g. pylint's tests/.pylint_primer_tests/
    # .gitkeep). Otherwise edits to such files would be missing from the agent's patch.
    git("add", "-A", "--force", cwd=dest)
    git("commit", "--quiet", "--no-verify", "-m", f"baseline {base_commit}", cwd=dest)
    sha = git("rev-parse", "HEAD", cwd=dest).decode().strip()
    return Workspace(path=dest, base_commit=base_commit, baseline_sha=sha)
