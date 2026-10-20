from __future__ import annotations

from pathlib import Path

from codetrace.workspace import create_workspace, ensure_mirror, git

from .conftest import FIX_MARKER


def _ws(tmp_path: Path, upstream: tuple[Path, str]):  # type: ignore[no-untyped-def]
    repo, base = upstream
    mirror = ensure_mirror(str(repo), tmp_path / "mirror")
    return create_workspace(mirror, base, tmp_path / "ws")


def test_snapshot_is_base_commit_without_future_history(
    tmp_path: Path, upstream_repo: tuple[Path, str]
) -> None:
    ws = _ws(tmp_path, upstream_repo)
    assert "return a - b" in (ws.path / "calc" / "core.py").read_text()
    # Exactly one commit, and the future fix is unreachable from any ref or object.
    assert git("rev-list", "--all", cwd=ws.path).decode().split() == [ws.baseline_sha]
    all_objects = git("cat-file", "--batch-all-objects", "--batch", cwd=ws.path)
    assert FIX_MARKER.encode() not in all_objects


def test_diff_is_empty_then_applies_cleanly(
    tmp_path: Path, upstream_repo: tuple[Path, str]
) -> None:
    ws = _ws(tmp_path, upstream_repo)
    assert ws.diff() == ""
    core = ws.path / "calc" / "core.py"
    core.write_text(core.read_text().replace("return a - b", "return a + b"))
    (ws.path / "calc" / "new.py").write_text("X = 1\n")
    patch = ws.diff()
    assert patch.startswith("diff --git a/calc/core.py b/calc/core.py")
    assert "+++ b/calc/new.py" in patch
    # The patch must apply to a pristine checkout of the upstream base commit.
    repo, base = upstream_repo
    fresh = tmp_path / "fresh"
    git("worktree", "add", "--quiet", "--detach", str(fresh), base, cwd=repo)
    git("apply", "--check", "-", cwd=fresh, input_bytes=patch.encode())


def test_mirror_reused(tmp_path: Path, upstream_repo: tuple[Path, str]) -> None:
    repo, _ = upstream_repo
    m1 = ensure_mirror(str(repo), tmp_path / "m")
    marker = m1 / "codetrace-marker"
    marker.write_text("x")
    assert ensure_mirror(str(repo), tmp_path / "m") == m1 and marker.exists()


def test_shallow_commit_mirror_excludes_future(
    tmp_path: Path, upstream_repo: tuple[Path, str]
) -> None:
    repo, base = upstream_repo
    mirror = ensure_mirror(str(repo), tmp_path / "m", commits=[base])
    assert FIX_MARKER.encode() not in git("cat-file", "--batch-all-objects", "--batch", cwd=mirror)
    # Idempotent: a second call with the same commit fetches nothing new.
    assert ensure_mirror(str(repo), tmp_path / "m", commits=[base]) == mirror
    ws = create_workspace(mirror, base, tmp_path / "ws")
    assert "return a - b" in (ws.path / "calc" / "core.py").read_text()


def test_baseline_tracks_files_upstream_tracks_despite_gitignore(tmp_path: Path) -> None:
    """Regression (found 2026-10-20 on pylint-dev__pylint-7080): upstream tracked a file
    under a .gitignore'd directory; the baseline commit omitted it, so agent edits to it
    would have been missing from the patch."""
    up = tmp_path / "up"
    (up / "keep").mkdir(parents=True)
    (up / ".gitignore").write_text("keep/\n")
    (up / "keep" / ".gitkeep").write_text("")
    (up / "keep" / "data.txt").write_text("v1\n")
    (up / "a.py").write_text("x = 1\n")
    git("init", "--quiet", "-b", "main", cwd=up)
    git("add", "-A", "--force", cwd=up)
    git("commit", "--quiet", "-m", "base", cwd=up)
    base = git("rev-parse", "HEAD", cwd=up).decode().strip()
    mirror = ensure_mirror(str(up), tmp_path / "m", commits=[base])
    ws = create_workspace(mirror, base, tmp_path / "ws")
    upstream_tree = git("rev-parse", f"{base}^{{tree}}", cwd=mirror).decode().strip()
    assert git("rev-parse", "HEAD^{tree}", cwd=ws.path).decode().strip() == upstream_tree
    (ws.path / "keep" / "data.txt").write_text("v2\n")
    assert "+v2" in ws.diff()
