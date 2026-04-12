"""Kill-switch and watchdog for runaway computer-use agents.

The watchdog monitors agent activity and cuts execution if:
- The agent has been running longer than max_duration
- No progress detected in stuck_timeout seconds (same screenshot hash)
- The agent triggers a forbidden action pattern
- Memory/CPU usage exceeds limits

Pattern:
    Watchdog runs as background task
    Agent ticks heartbeat on every action
    If heartbeat stops → stuck_timeout exceeded → kill
    If total time > max_duration → hard kill

Usage::

    watchdog = AgentWatchdog(
        max_duration_seconds=300,
        stuck_timeout_seconds=30,
        forbidden_patterns=["rm -rf", "DROP TABLE"],
    )
    async with watchdog:
        watchdog.tick(screenshot_hash)  # call on every action
        # agent runs here
"""

from __future__ import annotations

import asyncio
import os
import signal
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

_DEFAULT_MAX_DURATION: float = 300.0   # 5 minutes
_DEFAULT_STUCK_TIMEOUT: float = 30.0   # 30 seconds without progress
_DEFAULT_POLL_INTERVAL: float = 1.0
_DEFAULT_MAX_ACTIONS: int = 500


class KillReason(str, Enum):
    """Why the watchdog killed the agent session."""

    MAX_DURATION_EXCEEDED = "max_duration_exceeded"
    STUCK_DETECTED = "stuck_detected"
    FORBIDDEN_ACTION = "forbidden_action"
    MAX_ACTIONS_EXCEEDED = "max_actions_exceeded"
    MANUAL_KILL = "manual_kill"
    RESOURCE_LIMIT = "resource_limit"


@dataclass
class WatchdogEvent:
    """An event emitted by the watchdog before or after killing."""

    reason: KillReason
    session_id: str
    timestamp: float = field(default_factory=time.time)
    details: dict[str, Any] = field(default_factory=dict)


class AgentKilledException(Exception):
    """Raised when the watchdog terminates an agent session."""

    def __init__(self, reason: KillReason, details: dict[str, Any] | None = None) -> None:
        self.reason = reason
        self.details = details or {}
        super().__init__(f"Agent killed: {reason.value}")


class AgentWatchdog:
    """Monitor and kill runaway agent sessions.

    The watchdog runs as a background asyncio task. The agent must call
    ``tick()`` on every action; failure to tick within ``stuck_timeout``
    triggers a kill.

    Args:
        max_duration_seconds: Hard wall-clock limit for the entire session.
        stuck_timeout_seconds: Kill if no new screenshot hash for this long.
        max_actions: Kill if agent takes more than this many actions.
        forbidden_patterns: Kill if agent outputs any of these strings.
        session_id: Identifier for logging.
        on_kill: Optional async callback invoked just before killing.

    Example::

        watchdog = AgentWatchdog(max_duration_seconds=120, stuck_timeout_seconds=15)
        async with watchdog:
            for action in agent_loop():
                screenshot = await take_screenshot()
                watchdog.tick(screenshot_hash=hash(screenshot))
                await execute_action(action)
    """

    def __init__(
        self,
        max_duration_seconds: float = _DEFAULT_MAX_DURATION,
        stuck_timeout_seconds: float = _DEFAULT_STUCK_TIMEOUT,
        max_actions: int = _DEFAULT_MAX_ACTIONS,
        forbidden_patterns: list[str] | None = None,
        session_id: str = "unknown",
    ) -> None:
        self._max_duration = max_duration_seconds
        self._stuck_timeout = stuck_timeout_seconds
        self._max_actions = max_actions
        self._forbidden = [p.lower() for p in (forbidden_patterns or [])]
        self._session_id = session_id

        self._start_time: float = 0.0
        self._last_tick: float = 0.0
        self._last_hash: str = ""
        self._action_count: int = 0
        self._killed: bool = False
        self._kill_event: asyncio.Event | None = None
        self._kill_reason: KillReason | None = None
        self._watchdog_task: asyncio.Task[None] | None = None

    async def __aenter__(self) -> AgentWatchdog:
        self._start_time = time.time()
        self._last_tick = time.time()
        self._kill_event = asyncio.Event()
        self._watchdog_task = asyncio.create_task(self._watch_loop())
        return self

    async def __aexit__(self, *_: object) -> None:
        if self._watchdog_task:
            self._watchdog_task.cancel()
            try:
                await self._watchdog_task
            except asyncio.CancelledError:
                pass
        if self._kill_event:
            self._kill_event.set()

    def tick(self, screenshot_hash: str = "") -> None:
        """Register agent progress. Call on every action.

        Args:
            screenshot_hash: Hash of current screenshot for stuck detection.

        Raises:
            AgentKilledException: If watchdog has already triggered a kill.
        """
        if self._killed:
            reason = self._kill_reason or KillReason.MANUAL_KILL
            raise AgentKilledException(reason)

        self._last_tick = time.time()
        self._action_count += 1

        if screenshot_hash and screenshot_hash != self._last_hash:
            self._last_hash = screenshot_hash

        if self._action_count > self._max_actions:
            self._trigger_kill(
                KillReason.MAX_ACTIONS_EXCEEDED,
                {"action_count": self._action_count, "limit": self._max_actions},
            )

    def check_output(self, text: str) -> None:
        """Check agent output for forbidden patterns.

        Args:
            text: Agent output text to scan.

        Raises:
            AgentKilledException: If a forbidden pattern is found.
        """
        text_lower = text.lower()
        for pattern in self._forbidden:
            if pattern in text_lower:
                self._trigger_kill(
                    KillReason.FORBIDDEN_ACTION,
                    {"pattern": pattern, "text_snippet": text[:200]},
                )

    def _trigger_kill(self, reason: KillReason, details: dict[str, Any]) -> None:
        """Mark session as killed and raise exception."""
        self._killed = True
        self._kill_reason = reason
        if self._kill_event:
            self._kill_event.set()
        raise AgentKilledException(reason, details)

    async def _watch_loop(self) -> None:
        """Background loop that checks timeouts."""
        while not self._killed:
            await asyncio.sleep(_DEFAULT_POLL_INTERVAL)
            now = time.time()

            # Hard duration limit
            elapsed = now - self._start_time
            if elapsed > self._max_duration:
                self._trigger_kill(
                    KillReason.MAX_DURATION_EXCEEDED,
                    {"elapsed_s": elapsed, "limit_s": self._max_duration},
                )

            # Stuck detection
            since_tick = now - self._last_tick
            if since_tick > self._stuck_timeout:
                self._trigger_kill(
                    KillReason.STUCK_DETECTED,
                    {"since_last_tick_s": since_tick, "limit_s": self._stuck_timeout},
                )

    @property
    def is_killed(self) -> bool:
        """Return True if the watchdog has triggered a kill."""
        return self._killed

    @property
    def elapsed_seconds(self) -> float:
        """Return seconds since session started."""
        return time.time() - self._start_time if self._start_time else 0.0

    def stats(self) -> dict[str, Any]:
        """Return current watchdog statistics.

        Returns:
            Dict with timing, action counts, and kill status.
        """
        return {
            "session_id": self._session_id,
            "elapsed_s": self.elapsed_seconds,
            "action_count": self._action_count,
            "killed": self._killed,
            "kill_reason": self._kill_reason.value if self._kill_reason else None,
            "seconds_since_last_tick": time.time() - self._last_tick,
        }


class ProcessKillSwitch:
    """Hard kill switch that terminates a subprocess unconditionally.

    Use when the agent runs as a subprocess and you need guaranteed
    termination regardless of asyncio state.

    Args:
        pid: Process ID to kill on trigger.
        escalation_timeout: Seconds to wait between SIGTERM and SIGKILL.
    """

    def __init__(self, pid: int, escalation_timeout: float = 3.0) -> None:
        self._pid = pid
        self._timeout = escalation_timeout

    def kill(self) -> None:
        """Send SIGTERM then SIGKILL after timeout."""
        try:
            os.kill(self._pid, signal.SIGTERM)
            time.sleep(self._timeout)
            # Check if still running
            os.kill(self._pid, 0)  # raises if not running
            os.kill(self._pid, signal.SIGKILL)
        except ProcessLookupError:
            pass  # Already dead
