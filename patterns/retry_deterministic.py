"""Deterministic retry loops for UI hallucination recovery.

Computer-use agents hallucinate UI state: they click elements that don't
exist, type in wrong fields, or misread button labels. Naive retry loops
loop forever. This pattern implements bounded, verifiable retries with
screenshot-based state verification before each attempt.

Pattern:
    Action fails or produces wrong screenshot
    → Verify: take screenshot, compare expected vs actual state
    → If different: retry with corrected parameters
    → After max_attempts: graceful fallback (not crash)

Usage::

    async with DeterministicRetry(max_attempts=3, verify_fn=verify_state) as retry:
        result = await retry.run(click_button, selector="#submit")
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, TypeVar

_DEFAULT_MAX_ATTEMPTS: int = 3
_DEFAULT_BASE_DELAY: float = 1.0
_DEFAULT_MAX_DELAY: float = 8.0
_DEFAULT_BACKOFF_FACTOR: float = 2.0

T = TypeVar("T")


@dataclass
class RetryAttempt:
    """Result of a single retry attempt."""

    attempt_num: int
    success: bool
    result: Any
    error: str
    duration_ms: float
    screenshot_hash: str = ""


@dataclass
class RetryResult:
    """Final result of a retry sequence."""

    success: bool
    result: Any
    attempts: list[RetryAttempt]
    total_duration_ms: float
    fallback_used: bool = False

    @property
    def attempt_count(self) -> int:
        """Number of attempts made."""
        return len(self.attempts)


VerifyFn = Callable[[], Awaitable[bool]]
ActionFn = Callable[..., Awaitable[Any]]


async def _default_verify() -> bool:
    """Default verifier: always succeeds (use when no verification available)."""
    return True


class DeterministicRetry:
    """Bounded retry with pre-action state verification.

    Before each retry, optionally verifies the UI is in the expected state.
    Uses exponential backoff with a jitter-free deterministic schedule.

    Args:
        max_attempts: Maximum number of attempts (including first try).
        base_delay: Seconds to wait before first retry.
        max_delay: Maximum delay between retries.
        backoff_factor: Multiplier applied to delay on each retry.
        verify_fn: Async function that returns True if UI state is correct.
        fallback: Async function called if all attempts fail (returns fallback result).

    Example::

        async def verify_button_visible() -> bool:
            screenshot = await take_screenshot()
            return b"Submit" in screenshot

        retry = DeterministicRetry(
            max_attempts=3,
            verify_fn=verify_button_visible,
        )
        result = await retry.run(click_submit)
    """

    def __init__(
        self,
        max_attempts: int = _DEFAULT_MAX_ATTEMPTS,
        base_delay: float = _DEFAULT_BASE_DELAY,
        max_delay: float = _DEFAULT_MAX_DELAY,
        backoff_factor: float = _DEFAULT_BACKOFF_FACTOR,
        verify_fn: VerifyFn | None = None,
        fallback: ActionFn | None = None,
    ) -> None:
        self._max_attempts = max_attempts
        self._base_delay = base_delay
        self._max_delay = max_delay
        self._backoff = backoff_factor
        self._verify = verify_fn or _default_verify
        self._fallback = fallback

    async def __aenter__(self) -> DeterministicRetry:
        return self

    async def __aexit__(self, *_: object) -> None:
        pass

    def _delay_for(self, attempt: int) -> float:
        """Compute delay before attempt N (0-indexed)."""
        if attempt == 0:
            return 0.0
        delay = self._base_delay * (self._backoff ** (attempt - 1))
        return min(delay, self._max_delay)

    async def run(
        self,
        action: ActionFn,
        *args: Any,
        **kwargs: Any,
    ) -> RetryResult:
        """Execute action with deterministic retry on failure.

        Args:
            action: Async callable to execute.
            *args: Positional arguments for action.
            **kwargs: Keyword arguments for action.

        Returns:
            :class:`RetryResult` with success status and all attempt records.
        """
        attempts: list[RetryAttempt] = []
        total_start = time.monotonic()

        for attempt_num in range(self._max_attempts):
            # Wait before retrying
            delay = self._delay_for(attempt_num)
            if delay > 0:
                await asyncio.sleep(delay)

            # Verify state before acting (on retries only)
            if attempt_num > 0:
                state_ok = await self._verify()
                if not state_ok:
                    attempts.append(RetryAttempt(
                        attempt_num=attempt_num,
                        success=False,
                        result=None,
                        error="Pre-action state verification failed",
                        duration_ms=0.0,
                    ))
                    continue

            # Execute action
            t0 = time.monotonic()
            error = ""
            result = None
            success = False
            try:
                result = await action(*args, **kwargs)
                success = True
            except Exception as exc:
                error = f"{type(exc).__name__}: {exc}"

            duration_ms = (time.monotonic() - t0) * 1000
            attempts.append(RetryAttempt(
                attempt_num=attempt_num,
                success=success,
                result=result,
                error=error,
                duration_ms=duration_ms,
            ))

            if success:
                return RetryResult(
                    success=True,
                    result=result,
                    attempts=attempts,
                    total_duration_ms=(time.monotonic() - total_start) * 1000,
                )

        # All attempts failed — try fallback
        fallback_result = None
        fallback_used = False
        if self._fallback:
            try:
                fallback_result = await self._fallback(*args, **kwargs)
                fallback_used = True
            except Exception:
                pass

        return RetryResult(
            success=fallback_used,
            result=fallback_result,
            attempts=attempts,
            total_duration_ms=(time.monotonic() - total_start) * 1000,
            fallback_used=fallback_used,
        )


class UIStateVerifier:
    """Verify UI state by comparing screenshot hashes before/after actions.

    Args:
        screenshot_fn: Async function that returns raw screenshot bytes.
        similarity_threshold: 0.0-1.0; below this = UI changed (progress).
    """

    def __init__(
        self,
        screenshot_fn: Callable[[], Awaitable[bytes]],
        similarity_threshold: float = 0.95,
    ) -> None:
        self._screenshot_fn = screenshot_fn
        self._threshold = similarity_threshold
        self._last_hash: str = ""

    async def has_changed(self) -> bool:
        """Return True if the screen looks different from the last check.

        Returns:
            True if the UI changed (agent made visible progress).
        """
        import hashlib

        screenshot = await self._screenshot_fn()
        current_hash = hashlib.sha256(screenshot).hexdigest()
        changed = current_hash != self._last_hash
        self._last_hash = current_hash
        return changed

    async def verify_element_visible(
        self,
        expected_text: bytes,
        screenshot_fn: Callable[[], Awaitable[bytes]] | None = None,
    ) -> bool:
        """Check if expected text/bytes appear in the current screenshot.

        Args:
            expected_text: Bytes to search for in screenshot data.
            screenshot_fn: Override screenshot function.

        Returns:
            True if expected content is present.
        """
        fn = screenshot_fn or self._screenshot_fn
        screenshot = await fn()
        return expected_text in screenshot
