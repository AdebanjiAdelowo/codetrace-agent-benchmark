"""Offline fixtures: a toy upstream repository and a SWE-bench-shaped task record."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from codetrace.workspace import git

BUGGY = '''\
def add(a, b):
    """Return the sum of a and b."""
    return a - b


def greet(name):
    return "hello " + name
'''

FIXED = BUGGY.replace("return a - b", "return a + b")
FIX_MARKER = "return a + b"  # appears only in the later (future) commit

REFERENCE_PATCH = """\
diff --git a/calc/core.py b/calc/core.py
--- a/calc/core.py
+++ b/calc/core.py
@@ -1,6 +1,6 @@
 def add(a, b):
     \"\"\"Return the sum of a and b.\"\"\"
-    return a - b
+    return a + b
"""

TEST_PATCH_SECRET = "def test_add_hidden_secret_9f3a():"


@pytest.fixture
def upstream_repo(tmp_path: Path) -> tuple[Path, str]:
    """Toy upstream with two commits; returns (path, base_commit)."""
    repo = tmp_path / "upstream" / "toy__calc"
    (repo / "calc").mkdir(parents=True)
    (repo / "calc" / "__init__.py").write_text("")
    (repo / "calc" / "core.py").write_text(BUGGY)
    (repo / "README.md").write_text("toy calc\n")
    git("init", "--quiet", "-b", "main", cwd=repo)
    git("add", "-A", cwd=repo)
    git("commit", "--quiet", "-m", "base", cwd=repo)
    base = git("rev-parse", "HEAD", cwd=repo).decode().strip()
    (repo / "calc" / "core.py").write_text(FIXED)
    git("commit", "--quiet", "-am", "fix add", cwd=repo)
    return repo, base


@pytest.fixture
def task_record(upstream_repo: tuple[Path, str]) -> dict[str, Any]:
    _, base = upstream_repo
    return {
        "instance_id": "toy__calc-1",
        "repo": "toy/calc",
        "base_commit": base,
        "problem_statement": "`add` returns the wrong result: add(2, 2) gives 0.",
        "version": "0.1",
        "environment_setup_commit": base,
        "created_at": "2020-01-01T00:00:00Z",
        "patch": REFERENCE_PATCH,
        "test_patch": TEST_PATCH_SECRET + "\n    assert add(2, 2) == 4\n",
        "FAIL_TO_PASS": json.dumps(["tests/test_core.py::test_add"]),
        "PASS_TO_PASS": json.dumps(["tests/test_core.py::test_greet"]),
        "hints_text": "HINT_SECRET look at core.py",
    }


@pytest.fixture
def dataset_jsonl(tmp_path: Path, task_record: dict[str, Any]) -> Path:
    p = tmp_path / "dataset.jsonl"
    p.write_text(json.dumps(task_record) + "\n")
    return p
