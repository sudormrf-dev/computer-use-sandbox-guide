"""UX transparency patterns for autonomous computer-use agents.

Users distrust agents that act silently. This module provides lightweight
hooks for narrating actions in plain English before they execute, surfacing
confidence levels, and letting users approve irreversible steps without
blocking reversible ones.

Pattern:
    Agent prepares action → Narrator generates intent string
    → Low confidence or destructive action? → surface to user
    → User sees what happened and why, not just the outcome

Usage::

    narrator = ActionNarrator(min_confidence_to_surface=0.7)
    with narrator.announce("click", selector="#delete-account") as ctx:
        if ctx.needs_approval:
            user_input = await ask_user(ctx.intent)
            if not user_input:
                ctx.cancel()
                return
        await click(selector="#delete-account")
"""

from __future__ import annotations

import contextlib
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Generator

_DESTRUCTIVE_KEYWORDS: frozenset[str] = frozenset(
    {
        "delete", "remove", "drop", "truncate", "destroy", "wipe", "purge",
        "reset", "clear", "format", "uninstall", "revoke", "cancel", "terminate",
    }
)
_DEFAULT_CONFIDENCE: float = 1.0


class ActionCategory(str, Enum):
    """Broad category of a computer-use action."""

    NAVIGATION = "navigation"
    READ = "read"
    WRITE = "write"
    CLICK = "click"
    FORM = "form"
    DESTRUCTIVE = "destructive"
    SYSTEM = "system"
    UNKNOWN = "unknown"


@dataclass
class ActionIntent:
    """Human-readable description of a planned agent action.

    Attributes:
        action_type: Raw tool name (``click``, ``type``, ``screenshot``, …).
        params: Parameters passed to the tool.
        category: Semantic category for routing / display.
        intent: One-sentence plain-English description.
        confidence: 0.0-1.0; below threshold - surface to user.
        is_destructive: True if action cannot be undone.
        timestamp: When this intent was created.
    """

    action_type: str
    params: dict[str, Any]
    category: ActionCategory
    intent: str
    confidence: float = _DEFAULT_CONFIDENCE
    is_destructive: bool = False
    timestamp: float = field(default_factory=time.time)
    cancelled: bool = False

    def cancel(self) -> None:
        """Mark this action as cancelled (user vetoed it)."""
        self.cancelled = True

    @property
    def needs_approval(self) -> bool:
        """Return True if user approval is required before proceeding."""
        return self.is_destructive or self.confidence < 0.7


def _classify_action(action_type: str, params: dict[str, Any]) -> ActionCategory:
    """Heuristic category for a raw action type."""
    lower = action_type.lower()
    param_text = " ".join(str(v).lower() for v in params.values())

    if lower in {"screenshot", "cursor_position"}:
        return ActionCategory.READ
    if lower in {"navigate", "goto", "open_url"}:
        return ActionCategory.NAVIGATION
    if lower in {"type", "key", "paste"}:
        return ActionCategory.WRITE
    if lower in {"click", "double_click", "right_click"}:
        for kw in _DESTRUCTIVE_KEYWORDS:
            if kw in param_text:
                return ActionCategory.DESTRUCTIVE
        return ActionCategory.CLICK
    if lower in {"fill", "select", "submit"}:
        return ActionCategory.FORM
    if lower in {"bash", "exec", "run_command"}:
        return ActionCategory.SYSTEM
    return ActionCategory.UNKNOWN


def _build_intent_string(action_type: str, params: dict[str, Any]) -> str:
    """Generate a one-sentence plain-English description of an action.

    Args:
        action_type: Tool name.
        params: Tool parameters.

    Returns:
        Human-readable description for surfacing to users.
    """
    lower = action_type.lower()

    if lower == "screenshot":
        return "Taking a screenshot to observe the current screen state."
    if lower in {"navigate", "goto", "open_url"}:
        url = params.get("url", params.get("href", "an unknown URL"))
        return f"Navigating to {url}."
    if lower == "click":
        target = params.get("selector", params.get("element", params.get("coordinate", "an element")))
        return f"Clicking on {target!r}."
    if lower == "double_click":
        target = params.get("selector", params.get("element", "an element"))
        return f"Double-clicking on {target!r}."
    if lower in {"type", "key"}:
        text = params.get("text", params.get("key", ""))
        preview = (str(text)[:40] + "…") if len(str(text)) > 40 else str(text)
        return f'Typing "{preview}".'
    if lower in {"fill", "submit"}:
        selector = params.get("selector", "a form field")
        return f"Filling out {selector!r} and submitting."
    if lower in {"bash", "exec", "run_command"}:
        cmd = params.get("command", params.get("cmd", "a shell command"))
        preview = (str(cmd)[:60] + "…") if len(str(cmd)) > 60 else str(cmd)
        return f"Running shell command: {preview!r}."

    # Generic fallback
    param_summary = ", ".join(f"{k}={v!r}" for k, v in list(params.items())[:3])
    return f"Executing {action_type}({param_summary})."


class ActionIntentContext:
    """Context object returned by :meth:`ActionNarrator.announce`.

    Use ``ctx.needs_approval`` to gate user confirmation.
    Call ``ctx.cancel()`` to abort the action.
    """

    def __init__(self, intent: ActionIntent) -> None:
        self._intent = intent

    @property
    def needs_approval(self) -> bool:
        """True if the action should be shown to the user for confirmation."""
        return self._intent.needs_approval

    @property
    def intent(self) -> str:
        """Plain-English description of the planned action."""
        return self._intent.intent

    @property
    def is_destructive(self) -> bool:
        """True if the action cannot easily be undone."""
        return self._intent.is_destructive

    @property
    def confidence(self) -> float:
        """Agent confidence in this action (0.0-1.0)."""
        return self._intent.confidence

    def cancel(self) -> None:
        """Cancel the action."""
        self._intent.cancel()

    @property
    def cancelled(self) -> bool:
        """True if the action was cancelled."""
        return self._intent.cancelled


class ActionNarrator:
    """Narrate agent actions and gate destructive/uncertain ones.

    Args:
        min_confidence_to_surface: Actions below this confidence are flagged
            for user approval via :attr:`ActionIntentContext.needs_approval`.
        extra_destructive_keywords: Additional keywords to treat as destructive.

    Example::

        narrator = ActionNarrator()
        with narrator.announce("click", {"selector": "#delete-button"}) as ctx:
            if ctx.needs_approval:
                confirmed = await confirm_dialog(ctx.intent)
                if not confirmed:
                    ctx.cancel()
                    return
            await driver.click("#delete-button")
    """

    def __init__(
        self,
        min_confidence_to_surface: float = 0.7,
        extra_destructive_keywords: list[str] | None = None,
    ) -> None:
        self._threshold = min_confidence_to_surface
        self._destructive: frozenset[str] = _DESTRUCTIVE_KEYWORDS | frozenset(
            extra_destructive_keywords or []
        )
        self._history: list[ActionIntent] = []

    @contextlib.contextmanager
    def announce(
        self,
        action_type: str,
        params: dict[str, Any],
        confidence: float = _DEFAULT_CONFIDENCE,
    ) -> Generator[ActionIntentContext, None, None]:
        """Announce a planned action and optionally gate it.

        Args:
            action_type: Tool name (``click``, ``type``, ``bash``, …).
            params: Parameters for the tool.
            confidence: How confident the agent is (0.0-1.0).

        Yields:
            :class:`ActionIntentContext` with ``needs_approval`` and
            ``cancel()`` helper.
        """
        category = _classify_action(action_type, params)
        intent_str = _build_intent_string(action_type, params)
        is_destructive = category == ActionCategory.DESTRUCTIVE

        # Also check param values for destructive keywords
        if not is_destructive:
            param_text = " ".join(str(v).lower() for v in params.values())
            is_destructive = any(kw in param_text for kw in self._destructive)

        intent = ActionIntent(
            action_type=action_type,
            params=params,
            category=category,
            intent=intent_str,
            confidence=confidence,
            is_destructive=is_destructive,
        )
        self._history.append(intent)
        ctx = ActionIntentContext(intent)

        yield ctx

        # Record cancellation in history
        if ctx.cancelled:
            intent.cancelled = True

    def history(self) -> list[ActionIntent]:
        """Return immutable copy of all announced actions."""
        return list(self._history)

    def recent_actions(self, n: int = 5) -> list[str]:
        """Return the last N intent strings (for showing the user a summary).

        Args:
            n: Number of recent actions to return.

        Returns:
            List of plain-English action descriptions, most recent last.
        """
        return [a.intent for a in self._history[-n:]]


@dataclass
class StepAnnouncement:
    """A high-level step announcement for multi-step task UX.

    Use this to give users a progress indicator without flooding them
    with every low-level click/type event.

    Attributes:
        task_description: What the agent is trying to accomplish overall.
        current_step: Short description of the current sub-step.
        step_number: 1-indexed position in the overall plan.
        total_steps: Estimated total steps (may update mid-execution).
        percent_complete: Rough completion percentage for a progress bar.
    """

    task_description: str
    current_step: str
    step_number: int = 1
    total_steps: int = 1
    percent_complete: float = 0.0

    def advance(self, next_step: str) -> None:
        """Move to the next step and update progress.

        Args:
            next_step: Description of the next step.
        """
        self.step_number = min(self.step_number + 1, self.total_steps)
        self.current_step = next_step
        self.percent_complete = (self.step_number - 1) / max(self.total_steps, 1) * 100

    def complete(self) -> None:
        """Mark the task as 100% complete."""
        self.step_number = self.total_steps
        self.percent_complete = 100.0
        self.current_step = "Done"
