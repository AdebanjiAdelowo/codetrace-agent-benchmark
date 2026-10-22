"""Typed experiment configuration loaded from YAML."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field

from codetrace.agent import Budget


class ModelConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # "heuristic" is the offline scripted explorer (no API calls, no research value).
    provider: Literal["heuristic", "anthropic"]
    name: str | None = None
    effort: str = "high"
    max_tokens: int = 16000


class BudgetConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    max_steps: int = 40
    max_total_tokens: int = 1_500_000
    max_wall_s: float = 1800.0
    context_token_limit: int = 120_000
    max_no_tool_nudges: int = 2
    max_cost_usd: float | None = Field(default=None, gt=0)

    def to_budget(self) -> Budget:
        return Budget(**self.model_dump())


class ExperimentConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    experiment_id: str
    description: str = ""
    split: Literal["dev", "test"] = "test"
    instance_ids: list[str] = Field(min_length=1)
    agent_configs: list[Literal["A_baseline"]] = ["A_baseline"]
    seeds: list[int] = [0]
    model: ModelConfig
    budget: BudgetConfig = BudgetConfig()
    # Optional local dataset file (parquet/jsonl); otherwise downloaded at the pinned revision.
    dataset_path: Path | None = None
    eval_image: str | None = None
    eval_image_id: str | None = None
    # Optional directory of local repo sources named <owner>__<name> (offline runs, tests);
    # otherwise repositories are cloned from GitHub.
    repo_source_dir: Path | None = None


def load_config(path: Path) -> ExperimentConfig:
    with path.open(encoding="utf-8") as fh:
        return ExperimentConfig.model_validate(yaml.safe_load(fh))


def data_dir() -> Path:
    """Mirrors and workspaces (large, never committed). Override with CODETRACE_DATA_DIR."""
    return Path(os.environ.get("CODETRACE_DATA_DIR", ".codetrace_data")).resolve()
