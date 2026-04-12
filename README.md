# computer-use-sandbox-guide

Production sandbox patterns for Claude computer-use agents. Five battle-tested modules covering the full safety stack — from virtual display isolation to UX transparency.

## Why this exists

Computer-use agents run autonomously on real desktops. Without proper sandboxing they can:
- Loop forever on a stuck UI (no watchdog)
- Destroy evidence when something goes wrong (no recording)
- Retry blind without verifying state (no deterministic retry)
- Silently delete files without user awareness (no UX transparency)
- Affect the host display instead of an isolated virtual one (no Xvfb)

This guide gives you copy-paste patterns to fix all five.

## Patterns

### `virtual_display.py` — Headless isolation

Run agents on an Xvfb virtual framebuffer. No host display needed. Includes VNC for live debugging and Docker configuration with minimal capabilities.

```python
from patterns.virtual_display import VirtualDisplay, VirtualDisplayConfig

with VirtualDisplay(VirtualDisplayConfig(width=1280, height=800)):
    env = display.get_env()  # {"DISPLAY": ":99", ...}
    # run your agent here
```

### `kill_switch.py` — Watchdog & hard kill

Bounded execution with stuck detection, forbidden pattern scanning, and SIGTERM→SIGKILL escalation.

```python
from patterns.kill_switch import AgentWatchdog

watchdog = AgentWatchdog(max_duration_seconds=300, stuck_timeout_seconds=30)
async with watchdog:
    for action in agent_loop():
        watchdog.tick(screenshot_hash=hash_of_screenshot)
        await execute(action)
```

### `recording_observability.py` — Session recording

Synchronized MP4 video + JSONL action log. Essential for post-mortem debugging.

```python
from patterns.recording_observability import AgentRecorder

async with AgentRecorder("run-001", Path("/recordings")) as rec:
    await rec.log_action("click", {"x": 100, "y": 200}, screenshot_bytes=img)
# → /recordings/run-001.mp4 + /recordings/run-001.jsonl
```

### `retry_deterministic.py` — Bounded retry

Pre-action state verification before each retry. Exponential backoff without jitter for reproducibility.

```python
from patterns.retry_deterministic import DeterministicRetry

retry = DeterministicRetry(max_attempts=3, verify_fn=verify_ui_state)
result = await retry.run(click_submit, selector="#submit")
```

### `ux_transparency.py` — Transparent actions

Narrate actions in plain English, gate destructive ones for user approval, track confidence.

```python
from patterns.ux_transparency import ActionNarrator

narrator = ActionNarrator()
with narrator.announce("click", {"selector": "#delete-account"}) as ctx:
    if ctx.needs_approval:
        if not await confirm(ctx.intent):
            ctx.cancel()
            return
    await driver.click("#delete-account")
```

## Quick start

```bash
pip install -e ".[dev]"
pytest
```

## Docker

```bash
docker compose up agent
# For VNC debugging:
docker compose --profile debug up agent-vnc
# Connect: vnc://localhost:5999
```

## Full example

See [`examples/secure_agent_runner.py`](examples/secure_agent_runner.py) for an end-to-end runner combining all five layers.

## License

MIT
