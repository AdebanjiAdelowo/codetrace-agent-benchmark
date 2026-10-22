"""Content-addressed pilot protocol. Freezes are never silently overwritten."""

from __future__ import annotations

import hashlib
import json
from importlib.metadata import version
from pathlib import Path
from typing import Any

from codetrace.agent import SYSTEM_PROMPT, TASK_TEMPLATE
from codetrace.config import load_config
from codetrace.dataset import DATASET_NAME, DATASET_REVISION, download_split, load_records
from codetrace.pricing import price_table
from codetrace.tools import TOOL_SPECS

ROOT = Path(__file__).resolve().parents[2]


def digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode()).hexdigest()


def freeze_payload(config_path: Path) -> dict[str, Any]:
    cfg = load_config(config_path)
    if (len(cfg.instance_ids), len(cfg.agent_configs), len(cfg.seeds)) != (1, 1, 1):
        raise ValueError("pilot requires exactly one task, configuration, and run")
    if cfg.model.provider != "anthropic" or cfg.budget.max_cost_usd is None:
        raise ValueError("pilot requires Anthropic and a positive per-run dollar cap")
    prices = price_table(cfg.model.name or "")
    if prices is None:
        raise ValueError("pilot model has no versioned price table")
    dataset = cfg.dataset_path or download_split(cfg.split)
    records = [r for r in load_records(dataset) if r["instance_id"] in cfg.instance_ids]
    if len(records) != 1:
        raise ValueError("pilot task missing or duplicated in dataset")
    files = sorted((ROOT / "src/codetrace").rglob("*.py"))
    files += [ROOT / "uv.lock", ROOT / "pyproject.toml", ROOT / "docs/PROTOCOL.md"]
    sources = {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest() for p in files}
    return {
        "format": 1,
        "config": cfg.model_dump(mode="json"),
        "sources": sources,
        "dataset": DATASET_NAME,
        "dataset_revision": DATASET_REVISION,
        "task_record_sha256": digest(records[0]),
        "system_prompt_sha256": hashlib.sha256(SYSTEM_PROMPT.encode()).hexdigest(),
        "task_template_sha256": hashlib.sha256(TASK_TEMPLATE.encode()).hexdigest(),
        "tool_specs_sha256": digest(TOOL_SPECS),
        "pricing": prices.as_dict(),
        "evaluator": "swebench==5.0.2",
        "anthropic_sdk_version": version("anthropic"),
        "image": cfg.eval_image,
        "image_id": cfg.eval_image_id,
        "total_cost_cap_usd": cfg.budget.max_cost_usd,
    }


def write_freeze(config_path: Path) -> dict[str, Any]:
    payload = freeze_payload(config_path)
    frozen = {"sha256": digest(payload), "payload": payload}
    with (config_path.parent / "FROZEN.json").open("x") as fh:
        fh.write(json.dumps(frozen, indent=2, sort_keys=True) + "\n")
    return frozen


def check_freeze(config_path: Path) -> dict[str, Any]:
    frozen: dict[str, Any] = json.loads((config_path.parent / "FROZEN.json").read_text())
    if frozen["sha256"] != digest(frozen["payload"]):
        raise ValueError("frozen manifest digest is invalid")
    if freeze_payload(config_path) != frozen["payload"]:
        raise ValueError("frozen protocol drift; use a new versioned experiment directory")
    return frozen
