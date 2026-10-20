from __future__ import annotations

from pathlib import Path

import pytest

from codetrace.tools import TOOL_NAMES, TOOL_SPECS, RepoTools
from codetrace.trace import CodeSpan


@pytest.fixture
def tools(tmp_path: Path) -> RepoTools:
    root = tmp_path / "repo"
    (root / "pkg").mkdir(parents=True)
    (root / "pkg" / "a.py").write_text("".join(f"line{i}\n" for i in range(1, 401)))
    (root / "pkg" / "b.py").write_text("def foo():\n    return 1\n\nfoo()\n")
    (root / ".git").mkdir()
    (root / ".git" / "secret").write_text("future fix")
    (tmp_path / "outside.txt").write_text("outside")
    return RepoTools(root)


def test_specs_are_strict_and_named() -> None:
    assert [t["name"] for t in TOOL_SPECS] == TOOL_NAMES
    assert "bash" not in TOOL_NAMES and "submit" in TOOL_NAMES
    for t in TOOL_SPECS:
        assert t["input_schema"]["additionalProperties"] is False


@pytest.mark.parametrize(
    "path", ["../outside.txt", "/etc/passwd", ".git/secret", "pkg/../.git/secret"]
)
def test_path_sandbox(tools: RepoTools, path: str) -> None:
    out = tools.call("read_file", {"path": path})
    assert out.is_error and not out.spans_viewed


def test_symlink_escape_blocked(tools: RepoTools, tmp_path: Path) -> None:
    (tools.root / "link.txt").symlink_to(tmp_path / "outside.txt")
    assert tools.call("read_file", {"path": "link.txt"}).is_error


def test_read_paging_and_spans(tools: RepoTools) -> None:
    out = tools.call("read_file", {"path": "pkg/a.py"})
    assert out.spans_viewed == [CodeSpan(path="pkg/a.py", start_line=1, end_line=300)]
    assert "[lines 1-300 of 400]" in out.text
    out = tools.call("read_file", {"path": "pkg/a.py", "start_line": 390, "end_line": 999})
    assert out.spans_viewed == [CodeSpan(path="pkg/a.py", start_line=390, end_line=400)]
    assert tools.call("read_file", {"path": "pkg/a.py", "start_line": 500}).is_error


def test_search_literal_regex_and_spans(tools: RepoTools) -> None:
    out = tools.call("search_code", {"pattern": "foo()"})
    assert out.text.splitlines() == ["pkg/b.py:1: def foo():", "pkg/b.py:4: foo()"]
    assert [s.start_line for s in out.spans_viewed] == [1, 4]
    out = tools.call("search_code", {"pattern": r"^line39\d$", "regex": True, "path": "pkg"})
    assert len(out.spans_viewed) == 10
    assert tools.call("search_code", {"pattern": "future fix"}).text == "no matches"
    assert tools.call("search_code", {"pattern": "(", "regex": True}).is_error


def test_search_truncates(tools: RepoTools) -> None:
    out = tools.call("search_code", {"pattern": "line"})
    assert len(out.spans_viewed) == 50 and "truncated" in out.text


def test_replace_requires_unique_match(tools: RepoTools) -> None:
    assert tools.call(
        "replace_in_file", {"path": "pkg/b.py", "old_text": "foo", "new_text": "x"}
    ).is_error
    out = tools.call(
        "replace_in_file", {"path": "pkg/b.py", "old_text": "return 1", "new_text": "return 2"}
    )
    assert not out.is_error and out.files_modified == ["pkg/b.py"]
    assert "return 2" in (tools.root / "pkg" / "b.py").read_text()


def test_create_and_errors(tools: RepoTools) -> None:
    assert not tools.call("create_file", {"path": "pkg/c.py", "content": "x"}).is_error
    assert tools.call("create_file", {"path": "pkg/c.py", "content": "y"}).is_error
    assert tools.call("nope", {}).is_error
    assert tools.call("read_file", {"wrong": 1}).is_error
    assert tools.call("submit", {}).submitted
