"""Tests for kill_switch.py."""

from __future__ import annotations

import asyncio
from unittest.mock import patch

import pytest

from patterns.kill_switch import (
    AgentKilledException,
    AgentWatchdog,
    KillReason,
)


class TestAgentWatchdog:
    def test_tick_increments_action_count(self):
        watchdog = AgentWatchdog()
        watchdog._start_time = 1.0
        watchdog._last_tick = 1.0
        watchdog.tick()
        assert watchdog.stats()["action_count"] == 1

    def test_tick_raises_if_already_killed(self):
        watchdog = AgentWatchdog()
        watchdog._killed = True
        watchdog._kill_reason = KillReason.MANUAL_KILL
        with pytest.raises(AgentKilledException):
            watchdog.tick()

    def test_tick_exceeds_max_actions(self):
        watchdog = AgentWatchdog(max_actions=3)
        watchdog._start_time = 1.0
        watchdog._last_tick = 1.0
        watchdog._kill_event = asyncio.Event()
        with pytest.raises(AgentKilledException) as exc_info:
            for _ in range(10):
                watchdog.tick()
        assert exc_info.value.reason == KillReason.MAX_ACTIONS_EXCEEDED

    def test_check_output_forbidden_pattern(self):
        watchdog = AgentWatchdog(
            forbidden_patterns=["rm -rf"],
        )
        watchdog._start_time = 1.0
        watchdog._kill_event = asyncio.Event()
        with pytest.raises(AgentKilledException) as exc_info:
            watchdog.check_output("I will run rm -rf /tmp/foo")
        assert exc_info.value.reason == KillReason.FORBIDDEN_ACTION

    def test_check_output_safe_text(self):
        watchdog = AgentWatchdog(forbidden_patterns=["DROP TABLE"])
        watchdog._start_time = 1.0
        watchdog.check_output("SELECT * FROM users WHERE id = 1")

    def test_is_killed_false_initially(self):
        watchdog = AgentWatchdog()
        assert not watchdog.is_killed

    def test_stats_returns_dict(self):
        watchdog = AgentWatchdog(session_id="test-session")
        watchdog._start_time = 1.0
        watchdog._last_tick = 1.0
        stats = watchdog.stats()
        assert stats["session_id"] == "test-session"
        assert "elapsed_s" in stats
        assert "action_count" in stats
        assert "killed" in stats

    @pytest.mark.asyncio
    async def test_context_manager_starts_watchdog(self):
        watchdog = AgentWatchdog(max_duration_seconds=60, stuck_timeout_seconds=30)
        async with watchdog:
            watchdog.tick()
            assert watchdog.stats()["action_count"] == 1
        assert not watchdog.is_killed

    @pytest.mark.asyncio
    async def test_max_duration_triggers_kill(self):
        with patch("patterns.kill_switch._DEFAULT_POLL_INTERVAL", 0.01):
            watchdog = AgentWatchdog(
                max_duration_seconds=0.01,
                stuck_timeout_seconds=999,
            )
            with pytest.raises(AgentKilledException) as exc_info:
                async with watchdog:
                    await asyncio.sleep(0.15)
                    watchdog.tick()  # _killed=True by now → raises
            assert exc_info.value.reason in {
                KillReason.MAX_DURATION_EXCEEDED,
                KillReason.STUCK_DETECTED,
            }

    @pytest.mark.asyncio
    async def test_stuck_detection(self):
        with patch("patterns.kill_switch._DEFAULT_POLL_INTERVAL", 0.01):
            watchdog = AgentWatchdog(
                max_duration_seconds=999,
                stuck_timeout_seconds=0.02,
            )
            with pytest.raises(AgentKilledException):
                async with watchdog:
                    # Don't call tick — simulate stuck agent
                    await asyncio.sleep(0.15)
                    watchdog.tick()


class TestAgentKilledException:
    def test_message_includes_reason(self):
        exc = AgentKilledException(KillReason.MANUAL_KILL)
        assert "manual_kill" in str(exc)

    def test_details_stored(self):
        exc = AgentKilledException(KillReason.FORBIDDEN_ACTION, {"pattern": "rm -rf"})
        assert exc.details["pattern"] == "rm -rf"
