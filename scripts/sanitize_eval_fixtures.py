"""Copy official evaluator reports into sanitized regression-test fixtures.

From each harness run directory, only the run summary and the per-instance
``report.json`` files are copied (patches, eval scripts with hidden tests, and test logs
are not). Every test identifier is replaced by ``test_<sha256[:12]>`` so no benchmark test
names enter the repository, while structure, keys, booleans, list lengths, and the
instance ids are kept. Absolute paths are rejected.

Before writing, the script checks that CodeTrace parses each sanitized report and the
directory as a whole exactly as it parses the original.

Usage: python scripts/sanitize_eval_fixtures.py <validation_out> <fixture_dir>
"""

from __future__ import annotations

import hashlib
import json
import shutil
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Any

from codetrace.evaluation import collect_evaluation, parse_instance_report


def _anon(name: str) -> str:
    return "test_" + hashlib.sha256(name.encode()).hexdigest()[:12]


def sanitize_report(report: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = json.loads(json.dumps(report))
    for rec in out.values():
        for group in (rec.get("tests_status") or {}).values():
            for kind in ("success", "failure"):
                group[kind] = [_anon(t) for t in group[kind]]
    return out


def main() -> int:
    src, dst = Path(sys.argv[1]), Path(sys.argv[2])
    if dst.exists():
        shutil.rmtree(dst)
    for run_dir in sorted(p for p in src.iterdir() if (p / "logs").is_dir()):
        reports = sorted(run_dir.glob("logs/run_evaluation/*/*/*/report.json"))
        if not reports:
            continue
        run_id = reports[0].parents[2].name
        ids = [p.parent.name for p in reports]
        summary = next(run_dir.glob(f"*.{run_id}.json"))
        out_run = dst / run_dir.name
        for rp in reports:
            original = json.loads(rp.read_text())
            clean = sanitize_report(original)
            iid = rp.parent.name
            a, b = (
                asdict(parse_instance_report(original, iid)),
                asdict(parse_instance_report(clean, iid)),
            )
            if a != b:
                raise SystemExit(f"{rp}: sanitized parse differs: {a} != {b}")
            target = out_run / rp.relative_to(run_dir)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(json.dumps(clean, indent=4) + "\n")
        (out_run / summary.name).write_text(summary.read_text())
        before = collect_evaluation(run_dir, run_id, ids)
        after = collect_evaluation(out_run, run_id, ids)
        for r in before + after:
            r.pop("report_path", None)
        if before != after:
            raise SystemExit(f"{run_dir}: collected rows differ after sanitization")
        print(f"{run_dir.name}: {len(reports)} report(s) sanitized, parse-equivalent")
    for f in dst.rglob("*.json"):
        if "/Users/" in f.read_text() or "/home/" in f.read_text():
            raise SystemExit(f"{f}: contains an absolute path")
    return 0


if __name__ == "__main__":
    sys.exit(main())
