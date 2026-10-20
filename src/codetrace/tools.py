"""Sandboxed repository tools exposed to the agent.

All paths are resolved inside the workspace root; ``.git`` and anything that
resolves outside the root are rejected. Read and search tools report the exact
code spans they made visible, which is the raw signal for discovery metrics.
There is intentionally no shell tool: agent-generated commands are not executed
on the host, and test outcomes come only from the official evaluator.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from codetrace.trace import CodeSpan

MAX_READ_LINES = 300
MAX_SEARCH_RESULTS = 50
MAX_LIST_ENTRIES = 300
MAX_FILE_BYTES = 2_000_000
SKIP_DIRS = {".git", "__pycache__", ".tox", ".mypy_cache", ".pytest_cache", "node_modules"}


class ToolError(Exception):
    pass


@dataclass
class ToolOutcome:
    text: str
    is_error: bool = False
    spans_viewed: list[CodeSpan] = field(default_factory=list)
    files_modified: list[str] = field(default_factory=list)
    submitted: bool = False


TOOL_SPECS: list[dict[str, Any]] = [
    {
        "name": "list_dir",
        "description": "List files and directories at a repository-relative path.",
        "input_schema": {
            "type": "object",
            "properties": {"path": {"type": "string", "description": "Directory, default '.'"}},
            "required": [],
            "additionalProperties": False,
        },
    },
    {
        "name": "read_file",
        "description": (
            f"Read a file with line numbers. At most {MAX_READ_LINES} lines per call; "
            "use start_line/end_line (1-indexed, inclusive) to page."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "start_line": {"type": "integer", "minimum": 1},
                "end_line": {"type": "integer", "minimum": 1},
            },
            "required": ["path"],
            "additionalProperties": False,
        },
    },
    {
        "name": "search_code",
        "description": (
            "Search file contents for a literal string (or a Python regex if regex=true). "
            f"Returns up to {MAX_SEARCH_RESULTS} matches as path:line: text."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "pattern": {"type": "string"},
                "path": {"type": "string", "description": "Directory to search, default '.'"},
                "regex": {"type": "boolean"},
            },
            "required": ["pattern"],
            "additionalProperties": False,
        },
    },
    {
        "name": "replace_in_file",
        "description": (
            "Replace exactly one occurrence of old_text with new_text in a file. "
            "Fails if old_text occurs zero or multiple times."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "old_text": {"type": "string"},
                "new_text": {"type": "string"},
            },
            "required": ["path", "old_text", "new_text"],
            "additionalProperties": False,
        },
    },
    {
        "name": "create_file",
        "description": "Create a new file. Fails if the file already exists.",
        "input_schema": {
            "type": "object",
            "properties": {"path": {"type": "string"}, "content": {"type": "string"}},
            "required": ["path", "content"],
            "additionalProperties": False,
        },
    },
    {
        "name": "submit",
        "description": "Finish the task. Your current file edits become the submitted patch.",
        "input_schema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
]

TOOL_NAMES = [t["name"] for t in TOOL_SPECS]


class RepoTools:
    def __init__(self, root: Path) -> None:
        self.root = root.resolve()

    # ------------------------------------------------------------------ helpers
    def _resolve(self, rel: str) -> Path:
        if not isinstance(rel, str) or rel.strip() == "":
            rel = "."
        candidate = (self.root / rel).resolve()
        if candidate != self.root and self.root not in candidate.parents:
            raise ToolError(f"path escapes repository: {rel}")
        parts = candidate.relative_to(self.root).parts
        if ".git" in parts:
            raise ToolError("access to .git is not permitted")
        return candidate

    def _rel(self, p: Path) -> str:
        return p.relative_to(self.root).as_posix() or "."

    @staticmethod
    def _read_text(p: Path) -> str:
        if p.stat().st_size > MAX_FILE_BYTES:
            raise ToolError(f"file too large (> {MAX_FILE_BYTES} bytes)")
        data = p.read_bytes()
        if b"\x00" in data[:8192]:
            raise ToolError("binary file")
        return data.decode("utf-8", errors="replace")

    # ------------------------------------------------------------------ dispatch
    def call(self, name: str, args: dict[str, Any]) -> ToolOutcome:
        handler = getattr(self, f"_tool_{name}", None)
        if handler is None or name not in TOOL_NAMES:
            return ToolOutcome(f"unknown tool: {name}", is_error=True)
        try:
            outcome: ToolOutcome = handler(**args)
            return outcome
        except ToolError as exc:
            return ToolOutcome(str(exc), is_error=True)
        except TypeError as exc:  # wrong/missing arguments from the model
            return ToolOutcome(f"invalid arguments for {name}: {exc}", is_error=True)
        except (OSError, re.error) as exc:
            return ToolOutcome(f"{type(exc).__name__}: {exc}", is_error=True)

    # ------------------------------------------------------------------ tools
    def _tool_list_dir(self, path: str = ".") -> ToolOutcome:
        d = self._resolve(path)
        if not d.is_dir():
            raise ToolError(f"not a directory: {path}")
        entries = sorted(
            (e.name + ("/" if e.is_dir() else "")) for e in d.iterdir() if e.name not in SKIP_DIRS
        )
        more = len(entries) - MAX_LIST_ENTRIES
        text = "\n".join(entries[:MAX_LIST_ENTRIES])
        if more > 0:
            text += f"\n... ({more} more entries)"
        return ToolOutcome(text or "(empty directory)")

    def _tool_read_file(
        self, path: str, start_line: int = 1, end_line: int | None = None
    ) -> ToolOutcome:
        p = self._resolve(path)
        if not p.is_file():
            raise ToolError(f"not a file: {path}")
        lines = self._read_text(p).splitlines()
        if not lines:
            return ToolOutcome("(empty file)")
        start = max(1, int(start_line))
        if start > len(lines):
            raise ToolError(f"start_line {start} beyond end of file ({len(lines)} lines)")
        end = len(lines) if end_line is None else min(int(end_line), len(lines))
        end = min(end, start + MAX_READ_LINES - 1)
        if end < start:
            raise ToolError("end_line before start_line")
        body = "\n".join(f"{i:>6}\t{lines[i - 1]}" for i in range(start, end + 1))
        footer = f"\n[lines {start}-{end} of {len(lines)}]"
        rel = self._rel(p)
        return ToolOutcome(
            body + footer, spans_viewed=[CodeSpan(path=rel, start_line=start, end_line=end)]
        )

    def _tool_search_code(self, pattern: str, path: str = ".", regex: bool = False) -> ToolOutcome:
        if not pattern:
            raise ToolError("empty pattern")
        base = self._resolve(path)
        matcher = re.compile(pattern) if regex else None
        hits: list[tuple[str, int, str]] = []
        files = [base] if base.is_file() else self._walk(base)
        for f in files:
            try:
                text = self._read_text(f)
            except ToolError:
                continue
            for i, line in enumerate(text.splitlines(), 1):
                found = matcher.search(line) if matcher else (pattern in line)
                if found:
                    hits.append((self._rel(f), i, line.strip()[:200]))
                    if len(hits) > MAX_SEARCH_RESULTS:
                        break
            if len(hits) > MAX_SEARCH_RESULTS:
                break
        truncated = len(hits) > MAX_SEARCH_RESULTS
        hits = hits[:MAX_SEARCH_RESULTS]
        if not hits:
            return ToolOutcome("no matches")
        text = "\n".join(f"{p}:{n}: {t}" for p, n, t in hits)
        if truncated:
            text += f"\n... (truncated at {MAX_SEARCH_RESULTS} matches)"
        spans = [CodeSpan(path=p, start_line=n, end_line=n) for p, n, _ in hits]
        return ToolOutcome(text, spans_viewed=spans)

    def _walk(self, base: Path) -> list[Path]:
        out: list[Path] = []
        for dirpath, dirnames, filenames in os.walk(base):
            dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS)
            for fn in sorted(filenames):
                p = Path(dirpath) / fn
                if not p.is_symlink():
                    out.append(p)
        return out

    def _tool_replace_in_file(self, path: str, old_text: str, new_text: str) -> ToolOutcome:
        p = self._resolve(path)
        if not p.is_file():
            raise ToolError(f"not a file: {path}")
        if old_text == "":
            raise ToolError("old_text must be non-empty")
        text = self._read_text(p)
        count = text.count(old_text)
        if count != 1:
            raise ToolError(f"old_text occurs {count} times in {path}; it must occur exactly once")
        p.write_text(text.replace(old_text, new_text, 1), encoding="utf-8")
        return ToolOutcome(f"edited {self._rel(p)}", files_modified=[self._rel(p)])

    def _tool_create_file(self, path: str, content: str) -> ToolOutcome:
        p = self._resolve(path)
        if p.exists():
            raise ToolError(f"file already exists: {path}")
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
        return ToolOutcome(f"created {self._rel(p)}", files_modified=[self._rel(p)])

    def _tool_submit(self) -> ToolOutcome:
        return ToolOutcome("submitted", submitted=True)
