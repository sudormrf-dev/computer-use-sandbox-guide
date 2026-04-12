"""Tests for retry_deterministic.py."""

from __future__ import annotations

import pytest

from patterns.retry_deterministic import (
    DeterministicRetry,
    UIStateVerifier,
    _default_verify,
)


async def _succeed() -> str:
    return "ok"


async def _fail() -> str:
    msg = "always fails"
    raise ValueError(msg)


class TestDeterministicRetry:
    @pytest.mark.asyncio
    async def test_success_on_first_attempt(self):
        retry = DeterministicRetry(max_attempts=3)
        result = await retry.run(_succeed)
        assert result.success
        assert result.result == "ok"
        assert result.attempt_count == 1

    @pytest.mark.asyncio
    async def test_failure_exhausts_attempts(self):
        retry = DeterministicRetry(max_attempts=3, base_delay=0.0)
        result = await retry.run(_fail)
        assert not result.success
        assert result.attempt_count == 3

    @pytest.mark.asyncio
    async def test_retry_succeeds_on_second_attempt(self):
        calls = [0]

        async def flaky() -> str:
            calls[0] += 1
            if calls[0] < 2:
                msg = "not ready yet"
                raise RuntimeError(msg)
            return "ready"

        retry = DeterministicRetry(max_attempts=3, base_delay=0.0)
        result = await retry.run(flaky)
        assert result.success
        assert result.result == "ready"
        assert calls[0] == 2

    @pytest.mark.asyncio
    async def test_fallback_called_on_exhaustion(self):
        async def my_fallback() -> str:
            return "fallback_result"

        retry = DeterministicRetry(max_attempts=2, base_delay=0.0, fallback=my_fallback)
        result = await retry.run(_fail)
        assert result.fallback_used
        assert result.result == "fallback_result"
        # success=True when fallback returns
        assert result.success

    @pytest.mark.asyncio
    async def test_no_fallback_returns_failure(self):
        retry = DeterministicRetry(max_attempts=2, base_delay=0.0)
        result = await retry.run(_fail)
        assert not result.success
        assert not result.fallback_used

    @pytest.mark.asyncio
    async def test_verify_fn_blocks_retry(self):
        calls = [0]

        async def bad_verify() -> bool:
            return False

        async def sometimes_fails() -> str:
            calls[0] += 1
            if calls[0] == 1:
                msg = "first fail"
                raise RuntimeError(msg)
            return "ok"

        retry = DeterministicRetry(
            max_attempts=3,
            base_delay=0.0,
            verify_fn=bad_verify,
        )
        result = await retry.run(sometimes_fails)
        # verify fails on retries → pre-action check blocks execution
        assert not result.success

    def test_delay_for_zero(self):
        retry = DeterministicRetry(base_delay=1.0, backoff_factor=2.0)
        assert retry._delay_for(0) == 0.0

    def test_delay_for_capped_at_max(self):
        retry = DeterministicRetry(base_delay=1.0, max_delay=5.0, backoff_factor=10.0)
        assert retry._delay_for(5) <= 5.0

    @pytest.mark.asyncio
    async def test_context_manager(self):
        async with DeterministicRetry(max_attempts=1) as retry:
            result = await retry.run(_succeed)
        assert result.success

    @pytest.mark.asyncio
    async def test_args_forwarded(self):
        async def add(a: int, b: int) -> int:
            return a + b

        retry = DeterministicRetry(max_attempts=1)
        result = await retry.run(add, 3, 4)
        assert result.result == 7


class TestDefaultVerify:
    @pytest.mark.asyncio
    async def test_always_returns_true(self):
        result = await _default_verify()
        assert result is True


class TestUIStateVerifier:
    @pytest.mark.asyncio
    async def test_has_changed_on_first_call(self):
        screenshot_data = iter([b"screen_v1", b"screen_v2"])

        async def fake_screenshot() -> bytes:
            return next(screenshot_data)

        verifier = UIStateVerifier(screenshot_fn=fake_screenshot)
        # First call: no previous hash, so it "changed"
        assert await verifier.has_changed() is True
        # Second call: different data → changed
        assert await verifier.has_changed() is True

    @pytest.mark.asyncio
    async def test_has_not_changed_same_data(self):
        async def fake_screenshot() -> bytes:
            return b"same_screen"

        verifier = UIStateVerifier(screenshot_fn=fake_screenshot)
        await verifier.has_changed()  # prime
        assert await verifier.has_changed() is False

    @pytest.mark.asyncio
    async def test_verify_element_visible_found(self):
        async def fake_screenshot() -> bytes:
            return b"<html>Submit button</html>"

        verifier = UIStateVerifier(screenshot_fn=fake_screenshot)
        assert await verifier.verify_element_visible(b"Submit") is True

    @pytest.mark.asyncio
    async def test_verify_element_not_visible(self):
        async def fake_screenshot() -> bytes:
            return b"<html>No button here</html>"

        verifier = UIStateVerifier(screenshot_fn=fake_screenshot)
        assert await verifier.verify_element_visible(b"Submit") is False
