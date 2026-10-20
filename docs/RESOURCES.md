# Resource Requirements

Unless marked *estimate*, numbers were measured on the development machine on 2026-10-20:
Apple Silicon (arm64), macOS 26.6.2, 11 CPU cores, 18 GB RAM, Docker 29.8.2 with an 8.3 GB VM.

## Local footprint (measured)

| item | size |
|---|---|
| `.venv`, core and dev deps | 242 MB |
| `.venv` with `anthropic` and `eval` extras | 405 MB |
| Depth-1 repository mirrors for the 3 pilot tasks | 9 MB total (pytest 1.5, pylint 1.8, sympy 5.8) |
| Sanitized workspaces | about the size of each checkout |
| Smoke-run traces and outputs (3 runs) | about 160 KB |
| Pinned SWE-bench Lite test split (parquet) | about 1 to 2 MB |

The test suite and smoke run need no GPU, no Docker, and no API key. Test runtime is about 6 s.

## Official evaluation (SWE-bench harness)

- Upstream guidance: an x86_64 host with about 120 GB free disk, 16 GB RAM, and 8 cores for
  full-benchmark evaluation. Apple Silicon support is marked experimental.
- Pilot images (Docker Hub, `latest`, **amd64 only**, measured *compressed* sizes):

  | image | compressed |
  |---|---|
  | `swebench/sweb.eval.x86_64.pytest-dev_1776_pytest-11143` | 1.03 GB |
  | `swebench/sweb.eval.x86_64.sympy_1776_sympy-13480` | 1.07 GB |
  | `swebench/sweb.eval.x86_64.pylint-dev_1776_pylint-7080` | 1.08 GB |

- *Estimate*: the uncompressed footprint inside Docker's VM is several times the compressed
  size, so plan for **at least 10 to 15 GB of free space** for the three pilot images, and
  more for 10 to 20 tasks (images can be removed between instances).
- On this Mac the images would run under x86_64 emulation, which is slower and has a higher
  risk of timeouts.

**Measured local run (2026-10-20, after disk was freed outside this project).** On the
Apple Silicon host with about 32 GiB free, the three pilot tasks were evaluated
sequentially with one image present at a time: pull, gold, control, then remove the image.

| per task | measured |
|---|---|
| Image size (uncompressed) | 3.84 to 4.04 GB |
| Host disk while the image is present | about 5 GiB (compressed and unpacked layers) |
| Harness wall time, one run | 28 to 39 s (test runtime 10 to 21 s), under Rosetta |
| Peak free disk floor observed | 26 GiB |
| Docker VM memory | 7.75 GiB total, about 3.5 GiB used by another project's containers; one evaluation container at a time fit |

Rule used: keep at least 15 GiB free, pull with `--platform linux/amd64`, `--max_workers 1`,
and remove only images pulled for this project. Earlier in the day the host had 4.7 to
7.5 GiB free, which is insufficient. Docker
holds 15.9 GB of images and 23.9 GB of build cache (16.4 GB reported reclaimable), possibly
used by other projects on this machine. Nothing has been deleted. Cleanup needs explicit
approval after confirming no other project depends on it.

### What needs Docker and what does not

Predictions with empty patches are skipped by the harness before any container starts, so
they need no images (verified locally). Only non-empty patches need the images above.

### Options

1. **GitHub-hosted runner** (prepared: `.github/workflows/swebench-harness-validation.yml`):
   native x86_64 `ubuntu-24.04`. The workflow frees preinstalled SDKs on the throwaway VM
   before pulling images and logs disk usage before and after.
   - Trigger: manual (`workflow_dispatch`) only. Needs a GitHub remote and a push, which
     need approval.
   - Cost bound: `timeout-minutes: 120` per run, one run at a time (`concurrency`). Public
     repositories use standard runners free of charge. Private repositories consume the
     account's included Actions minutes. Check the account's spending limit before
     triggering (not verified here).
   - Risk: unauthenticated Docker Hub pulls are rate-limited. A failed pull appears as a
     harness error, never as an outcome.
2. **Other x86_64 Linux host**: any VM with 8 or more cores, 16 GB or more RAM, and enough disk
   for the images. Run `uv sync --locked --extra eval && uv run codetrace validate-harness
   --bundle eval_bundles/harness_validation_v1 --out runs/harness_validation_v1`, or an
   experiment's `evaluate.sh`. Paid provisioning needs approval.
3. **Modal** (supported by the harness, paid, needs approval): add `--modal true` (`evaluator_command(..., modal=True)`).
   Needs a Modal account and token configured outside this repository.
4. **Local**, after freeing disk with approval:
   `bash runs/<experiment>/evaluate.sh` with the `eval` extra installed (the script runs the
   harness inside the experiment's `evaluation/` directory).

## Model API cost (*estimates*; nothing has been spent)

Prices for `claude-opus-5-5` as listed on 2026-10-06: input $4 / MTok, output $20 / MTok, cache
reads $0.20 / MTok, cache writes 1.25 times input. Prompt caching is on, and history is
append-only within a segment.

- Each run is hard-capped at 1.5 M processed tokens (all input components plus output) and
  40 steps.
- **Upper bound per run**: if every processed token were billed as uncached input, about $6,
  plus output at $20 / MTok. With caching, most re-sent history is billed at the cache-read
  price, so typical runs should cost a small fraction of the bound. This must be measured in
  the pilot, not assumed.
- **Pilot (3 tasks, configuration A, 1 seed)**: 3 runs. Upper bound of roughly $20 to $40.
- **Full protocol (15 tasks, 3 configurations, 3 seeds = 135 runs, plus ablation replays)**:
  extrapolate from measured pilot cost before approval. The worst case under current caps is
  on the order of $1,000.
