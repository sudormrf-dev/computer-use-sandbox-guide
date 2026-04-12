"""Tests for recording_observability.py."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from patterns.recording_observability import (
    ActionLog,
    AgentRecorder,
    ScreenRecorder,
    _hash_screenshot,
)


class TestActionLog:
    def test_to_jsonl_round_trip(self):
        log = ActionLog(
            session_id="s1",
            action_type="click",
            params={"selector": "#btn"},
        )
        line = log.to_jsonl()
        data = json.loads(line)
        assert data["session_id"] == "s1"
        assert data["action_type"] == "click"
        assert data["params"] == {"selector": "#btn"}

    def test_to_jsonl_includes_all_fields(self):
        log = ActionLog(
            session_id="s1",
            action_type="type",
            params={"text": "hello"},
            screenshot_hash="abc123",
            duration_ms=42.5,
            error="",
        )
        data = json.loads(log.to_jsonl())
        assert "screenshot_hash" in data
        assert "duration_ms" in data
        assert "error" in data


class TestHashScreenshot:
    def test_returns_16_char_hex(self):
        h = _hash_screenshot(b"fake screenshot data")
        assert len(h) == 16
        assert all(c in "0123456789abcdef" for c in h)

    def test_deterministic(self):
        data = b"same data"
        assert _hash_screenshot(data) == _hash_screenshot(data)

    def test_different_data_different_hash(self):
        assert _hash_screenshot(b"aaa") != _hash_screenshot(b"bbb")


class TestScreenRecorder:
    def test_is_running_false_initially(self, tmp_path):
        rec = ScreenRecorder(":99", tmp_path / "out.mp4")
        assert not rec.is_running()

    @patch("patterns.recording_observability.subprocess.Popen")
    def test_start_launches_ffmpeg(self, mock_popen, tmp_path):
        mock_proc = MagicMock()
        mock_proc.poll.return_value = None
        mock_popen.return_value = mock_proc

        rec = ScreenRecorder(":99", tmp_path / "out.mp4")
        rec.start()

        assert mock_popen.called
        cmd = mock_popen.call_args[0][0]
        assert "ffmpeg" in cmd
        assert "x11grab" in cmd

    @patch("patterns.recording_observability.subprocess.Popen")
    def test_stop_sends_q(self, mock_popen, tmp_path):
        mock_stdin = MagicMock()
        mock_proc = MagicMock()
        mock_proc.poll.return_value = None
        mock_proc.stdin = mock_stdin
        mock_popen.return_value = mock_proc

        rec = ScreenRecorder(":99", tmp_path / "out.mp4")
        rec.start()
        rec.stop()

        mock_stdin.write.assert_called_with(b"q")


class TestAgentRecorder:
    @pytest.mark.asyncio
    async def test_log_action_writes_jsonl(self, tmp_path):
        async with AgentRecorder("test", output_dir=tmp_path, record_video=False) as rec:
            await rec.log_action("click", {"selector": "#btn"})
            await rec.log_action("type", {"text": "hello"})

        log_path = tmp_path / "test.jsonl"
        assert log_path.exists()
        lines = log_path.read_text().strip().splitlines()
        # session_start + 2 actions + session_end = 4 lines
        assert len(lines) == 4

    @pytest.mark.asyncio
    async def test_session_header_and_footer(self, tmp_path):
        async with AgentRecorder("sess1", output_dir=tmp_path, record_video=False) as rec:
            await rec.log_action("screenshot", {})

        lines = (tmp_path / "sess1.jsonl").read_text().splitlines()
        header = json.loads(lines[0])
        footer = json.loads(lines[-1])
        assert header["event"] == "session_start"
        assert footer["event"] == "session_end"
        assert footer["action_count"] == 1

    @pytest.mark.asyncio
    async def test_session_summary(self, tmp_path):
        async with AgentRecorder("s2", output_dir=tmp_path, record_video=False) as rec:
            await rec.log_action("click", {})
            summary = rec.session_summary()

        assert summary["session_id"] == "s2"
        assert summary["action_count"] == 1
        assert "log_path" in summary

    @pytest.mark.asyncio
    async def test_screenshot_hash_stored(self, tmp_path):
        async with AgentRecorder("s3", output_dir=tmp_path, record_video=False) as rec:
            await rec.log_action("click", {}, screenshot_bytes=b"fake screenshot")

        lines = (tmp_path / "s3.jsonl").read_text().splitlines()
        # Find the click action line (not header/footer)
        action_lines = [line for line in lines if '"action_type"' in line]
        data = json.loads(action_lines[0])
        assert data["screenshot_hash"] != ""
