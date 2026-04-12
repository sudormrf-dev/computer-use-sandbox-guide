# computer-use-sandbox-guide — CLAUDE.md

## What this project is

Production sandbox patterns for Claude computer-use agents. Five pattern modules
covering the full safety stack: virtual display isolation, watchdog kill-switch,
session recording, deterministic retry, and UX transparency.

## Quality gates (must pass before commit)

```bash
cd /media/user/SSD/github-waves/computer-use-sandbox-guide
ruff check .
mypy patterns/
bandit -r patterns/ -ll
pytest --cov=patterns --cov-fail-under=60
```

## Project layout

```
patterns/          # Core sandbox patterns (importable library)
  virtual_display.py      — Xvfb + VNC + Docker config
  recording_observability.py — ffmpeg video + JSONL action log
  kill_switch.py          — AgentWatchdog + ProcessKillSwitch
  retry_deterministic.py  — Bounded retry with state verification
  ux_transparency.py      — ActionNarrator + StepAnnouncement
examples/          # End-to-end usage examples
benchmarks/        # Overhead measurement scripts
tests/             # pytest test suite
docker/            # Dockerfile + entrypoint.sh
```

## Key design decisions

- No runtime dependencies beyond stdlib — patterns are pure Python
- Virtual display (Xvfb) is always in a subprocess, never the host display
- Watchdog runs as background asyncio task; agent must call tick() on every action
- Recorder writes JSONL atomically; video is optional (disable in CI with record_video=False)
- DeterministicRetry uses exponential backoff without jitter for reproducibility

## Ruff ignores per-file

- `patterns/*`: S603, S404 (subprocess), S108 (hardcoded /tmp), PLR0913 (many args)
- `tests/*`: S101 (assert), PLR2004 (magic values), SLF001 (private access)
- `benchmarks/*`: T201 (print), S108 (/tmp paths)
- `examples/*`: T201 (print), S108 (/tmp paths)
