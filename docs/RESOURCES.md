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

**Current status: insufficient.** The host volume has about 7.5 GiB free (99% used). Docker
holds 15.9 GB of images and 23.9 GB of build cache (16.4 GB reported reclaimable), possibly
used by other projects on this machine. Nothing has been deleted. Cleanup needs explicit
approval after confirming no other project depends on it.

### Options

1. **Remote x86_64 Linux host** (recommended): any VM with 8 or more cores, 16 GB or more RAM,
   and 120 GB disk. Copy `runs/<experiment>/` and run `evaluate.sh` there.
2. **Modal** (supported by the harness): add `--modal true` (`evaluator_command(..., modal=True)`).
   Needs a Modal account and token configured outside this repository.
3. **Local**, after freeing disk with approval:
   `bash runs/<experiment>/evaluate.sh` from the repository root with the `eval` extra installed.

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
