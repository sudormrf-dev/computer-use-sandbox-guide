"""Secure, isolated computer-use agent runner (end-to-end example).

Combines virtual display + watchdog + recorder + deterministic retry
into a single production-ready runner. Drop in your agent loop and
get isolation, observability, and safety for free.

Usage::

    python examples/secure_agent_runner.py
"""

from __future__ import annotations

import asyncio
import hashlib
import os
import time
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Stub for the agent loop — replace with actual Anthropic SDK calls
# ---------------------------------------------------------------------------

async def _fake_screenshot() -> bytes:
    """Simulate taking a screenshot (returns synthetic bytes)."""
    return hashlib.sha256(str(time.time()).encode()).digest()


async def _fake_action(action: str, **kwargs: Any) -> str:
    """Simulate executing a computer-use action."""
    await asyncio.sleep(0.05)
    return f"ok:{action}"


async def _fake_agent_step(step: int) -> dict[str, Any]:
    """Simulate one agent reasoning + action loop iteration."""
    actions = [
        {"type": "screenshot", "params": {}},
        {"type": "click", "params": {"selector": "#search-input"}},
        {"type": "type", "params": {"text": "hello world"}},
        {"type": "click", "params": {"selector": "#search-btn"}},
        {"type": "screenshot", "params": {}},
    ]
    action = actions[step % len(actions)]
    await asyncio.sleep(0.1)
    return action


# ---------------------------------------------------------------------------
# Secure runner
# ---------------------------------------------------------------------------

async def run_secure_agent(
    task: str,
    max_steps: int = 20,
    session_id: str | None = None,
    output_dir: Path = Path("/tmp/agent_sessions"),
) -> dict[str, Any]:
    """Run a computer-use agent with full isolation and observability.

    Layers applied:
    - Virtual display isolation (Xvfb)
    - AgentWatchdog for stuck / runaway detection
    - AgentRecorder for reproducible post-mortems
    - DeterministicRetry for transient failures
    - ActionNarrator for transparent logging

    Args:
        task: Plain-English task description.
        max_steps: Hard cap on number of agent steps.
        session_id: Unique run identifier (auto-generated if not provided).
        output_dir: Where to write recordings and logs.

    Returns:
        Dict with ``success``, ``steps``, ``session_id``, ``log_path``.
    """
    from patterns.kill_switch import AgentKilledException, AgentWatchdog
    from patterns.recording_observability import AgentRecorder
    from patterns.retry_deterministic import DeterministicRetry
    from patterns.ux_transparency import ActionNarrator
    from patterns.virtual_display import VirtualDisplay, VirtualDisplayConfig

    sid = session_id or f"run-{int(time.time())}"
    output_dir.mkdir(parents=True, exist_ok=True)

    display_cfg = VirtualDisplayConfig(width=1280, height=800)

    # In CI / environments without Xvfb, skip the display start
    _display_available = os.environ.get("DISPLAY") or Path("/tmp/.X99-lock").exists()
    display = VirtualDisplay(display_cfg)
    if not _display_available:
        print(f"[{sid}] No display available — skipping Xvfb start (CI mode)")

    watchdog = AgentWatchdog(
        max_duration_seconds=300,
        stuck_timeout_seconds=30,
        max_actions=max_steps + 10,
        forbidden_patterns=["rm -rf /", "DROP TABLE", "DELETE FROM users"],
        session_id=sid,
    )
    recorder = AgentRecorder(
        session_id=sid,
        output_dir=output_dir,
        record_video=False,  # video disabled in example; enable in production
    )
    narrator = ActionNarrator(min_confidence_to_surface=0.6)
    retry = DeterministicRetry(max_attempts=3)

    steps_completed = 0
    success = False

    try:
        if _display_available:
            display.start()

        async with watchdog, recorder:
            await recorder.log_action("task_start", {"task": task})
            print(f"[{sid}] Starting task: {task!r}")

            for step in range(max_steps):
                # Heartbeat — prevents stuck detection
                screenshot = await _fake_screenshot()
                screenshot_hash = hashlib.sha256(screenshot).hexdigest()[:16]
                watchdog.tick(screenshot_hash=screenshot_hash)

                # Get next action from agent
                result = await retry.run(_fake_agent_step, step)
                if not result.success:
                    print(f"[{sid}] Step {step}: agent step failed after retries")
                    continue

                action = result.result

                # Narrate and log
                with narrator.announce(action["type"], action.get("params", {})) as ctx:
                    print(f"[{sid}] Step {step+1}/{max_steps}: {ctx.intent}")
                    await recorder.log_action(
                        action["type"],
                        action.get("params", {}),
                        screenshot_bytes=screenshot,
                    )

                    if not ctx.cancelled:
                        await _fake_action(action["type"], **action.get("params", {}))

                steps_completed += 1

                # Simulate task completion
                if step >= 4:
                    success = True
                    print(f"[{sid}] Task completed after {steps_completed} steps")
                    break

    except AgentKilledException as exc:
        print(f"[{sid}] Agent killed: {exc.reason.value}")
        print(f"[{sid}] Stats: {watchdog.stats()}")
    finally:
        if _display_available:
            display.stop()

    return {
        "success": success,
        "steps": steps_completed,
        "session_id": sid,
        "log_path": str(output_dir / f"{sid}.jsonl"),
        "narrator_history": narrator.recent_actions(10),
    }


if __name__ == "__main__":
    result = asyncio.run(
        run_secure_agent(
            task="Search for 'anthropic claude' and take a screenshot of the results",
            max_steps=10,
        )
    )
    print("\n=== Run Complete ===")
    for key, value in result.items():
        if key != "narrator_history":
            print(f"  {key}: {value}")
    print("  Recent actions:")
    for action in result.get("narrator_history", []):
        print(f"    - {action}")
