"""Tests for ux_transparency.py."""

from __future__ import annotations

import pytest

from patterns.ux_transparency import (
    ActionCategory,
    ActionNarrator,
    StepAnnouncement,
    _build_intent_string,
    _classify_action,
)


class TestClassifyAction:
    def test_screenshot_is_read(self):
        assert _classify_action("screenshot", {}) == ActionCategory.READ

    def test_navigate_is_navigation(self):
        assert _classify_action("navigate", {"url": "https://example.com"}) == ActionCategory.NAVIGATION

    def test_click_delete_is_destructive(self):
        cat = _classify_action("click", {"selector": "#delete-account-btn"})
        assert cat == ActionCategory.DESTRUCTIVE

    def test_click_safe_is_click(self):
        cat = _classify_action("click", {"selector": "#search"})
        assert cat == ActionCategory.CLICK

    def test_type_is_write(self):
        assert _classify_action("type", {"text": "hello"}) == ActionCategory.WRITE

    def test_bash_is_system(self):
        assert _classify_action("bash", {"command": "ls"}) == ActionCategory.SYSTEM

    def test_unknown_is_unknown(self):
        assert _classify_action("xyzzy_custom", {}) == ActionCategory.UNKNOWN


class TestBuildIntentString:
    def test_screenshot_describes_observation(self):
        s = _build_intent_string("screenshot", {})
        assert "screenshot" in s.lower()

    def test_navigate_includes_url(self):
        s = _build_intent_string("navigate", {"url": "https://example.com"})
        assert "https://example.com" in s

    def test_click_includes_selector(self):
        s = _build_intent_string("click", {"selector": "#submit"})
        assert "#submit" in s

    def test_type_truncates_long_text(self):
        long_text = "a" * 100
        s = _build_intent_string("type", {"text": long_text})
        assert len(s) < 200
        assert "…" in s

    def test_bash_includes_command(self):
        s = _build_intent_string("bash", {"command": "ls -la"})
        assert "ls -la" in s

    def test_unknown_action_fallback(self):
        s = _build_intent_string("custom_action", {"key": "val"})
        assert "custom_action" in s


class TestActionNarrator:
    def test_announce_yields_context(self):
        narrator = ActionNarrator()
        with narrator.announce("screenshot", {}) as ctx:
            assert ctx.intent != ""

    def test_destructive_action_needs_approval(self):
        narrator = ActionNarrator()
        with narrator.announce("click", {"selector": "#delete-all"}) as ctx:
            assert ctx.needs_approval
            assert ctx.is_destructive

    def test_safe_high_confidence_no_approval(self):
        narrator = ActionNarrator(min_confidence_to_surface=0.7)
        with narrator.announce("screenshot", {}, confidence=0.9) as ctx:
            assert not ctx.needs_approval

    def test_low_confidence_needs_approval(self):
        narrator = ActionNarrator(min_confidence_to_surface=0.7)
        with narrator.announce("click", {"selector": "#btn"}, confidence=0.5) as ctx:
            assert ctx.needs_approval

    def test_cancel_marks_intent_cancelled(self):
        narrator = ActionNarrator()
        with narrator.announce("click", {"selector": "#x"}) as ctx:
            ctx.cancel()
        assert narrator.history()[0].cancelled

    def test_history_accumulates(self):
        narrator = ActionNarrator()
        for i in range(3):
            with narrator.announce("click", {"selector": f"#btn-{i}"}):
                pass
        assert len(narrator.history()) == 3

    def test_recent_actions_capped(self):
        narrator = ActionNarrator()
        for i in range(10):
            with narrator.announce("click", {"selector": f"#x{i}"}):
                pass
        recent = narrator.recent_actions(5)
        assert len(recent) == 5

    def test_extra_destructive_keywords(self):
        narrator = ActionNarrator(extra_destructive_keywords=["nuke"])
        with narrator.announce("click", {"selector": "#nuke-database"}) as ctx:
            assert ctx.is_destructive


class TestStepAnnouncement:
    def test_advance_increments_step(self):
        ann = StepAnnouncement(
            task_description="Test task",
            current_step="Step 1",
            step_number=1,
            total_steps=5,
        )
        ann.advance("Step 2")
        assert ann.step_number == 2
        assert ann.current_step == "Step 2"

    def test_advance_updates_percent(self):
        ann = StepAnnouncement(
            task_description="Test",
            current_step="Step 1",
            step_number=1,
            total_steps=4,
        )
        ann.advance("Step 2")  # now at step 2/4 → was (2-1)/4 * 100 = 25%
        assert ann.percent_complete == pytest.approx(25.0)

    def test_complete_sets_100_percent(self):
        ann = StepAnnouncement("Task", "Step 1", total_steps=3)
        ann.complete()
        assert ann.percent_complete == 100.0
        assert ann.current_step == "Done"

    def test_advance_does_not_exceed_total(self):
        ann = StepAnnouncement("Task", "Step 1", step_number=5, total_steps=5)
        ann.advance("Overflow step")
        assert ann.step_number == 5  # capped at total
