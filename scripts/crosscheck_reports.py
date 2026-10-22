"""CLI compatibility wrapper for the independent report cross-check."""

from codetrace.crosscheck import check_run, log_statuses, main

__all__ = ["check_run", "log_statuses", "main"]

if __name__ == "__main__":
    raise SystemExit(main())
