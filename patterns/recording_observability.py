"""Recording + observability for computer-use agents.

Synchronised video recording of the virtual display with structured JSON
logs of every agent action. Essential for post-mortem debugging when an
agent does something unexpected.

Pattern:
    Agent session starts → recorder starts capturing frames
    Every tool call is logged with timestamp + screenshot hash
    Session ends → video + log written atomically
    On failure: video + log available for replay

Usage::

    async with AgentRecorder(session_id="run-001", output_dir="/recordings") as rec:
        await rec.log_action("screenshot", {})
        await rec.log_action("click", {"x": 100, "y": 200})
    # → /recordings/run-001.mp4 + /recordings/run-001.jsonl
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

_DEFAULT_FPS: int = 5
_DEFAULT_OUTPUT_DIR: str = "/tmp/agent_recordings"  # nosec B108
_FFMPEG_LOGLEVEL: str = "warning"


@dataclass
class ActionLog:
    """A single agent action with timing and context."""

    session_id: str
    action_type: str
    params: dict[str, Any]
    timestamp: float = field(default_factory=time.time)
    screenshot_hash: str = ""
    duration_ms: float = 0.0
    error: str = ""

    def to_jsonl(self) -> str:
        """Serialize to a single JSONL line."""
        return json.dumps(
            {
                "session_id": self.session_id,
                "action_type": self.action_type,
                "params": self.params,
                "timestamp": self.timestamp,
                "screenshot_hash": self.screenshot_hash,
                "duration_ms": self.duration_ms,
                "error": self.error,
            }
        )


class ScreenRecorder:
    """Record the virtual display to MP4 using ffmpeg.

    Args:
        display: X display string (e.g. ``:99``).
        output_path: Path for the output MP4 file.
        fps: Recording frame rate (lower = smaller file).
        width: Capture width (should match display).
        height: Capture height.
    """

    def __init__(
        self,
        display: str,
        output_path: Path,
        fps: int = _DEFAULT_FPS,
        width: int = 1280,
        height: int = 800,
    ) -> None:
        self._display = display
        self._output_path = output_path
        self._fps = fps
        self._width = width
        self._height = height
        self._proc: subprocess.Popen[bytes] | None = None

    def start(self) -> None:
        """Start ffmpeg screen capture in the background.

        Raises:
            FileNotFoundError: If ffmpeg is not installed.
            RuntimeError: If recording fails to start.
        """
        self._output_path.parent.mkdir(parents=True, exist_ok=True)
        cmd = [
            "ffmpeg",
            "-loglevel", _FFMPEG_LOGLEVEL,
            "-f", "x11grab",
            "-framerate", str(self._fps),
            "-video_size", f"{self._width}x{self._height}",
            "-i", self._display,
            "-c:v", "libx264",
            "-preset", "ultrafast",
            "-crf", "28",
            "-y",
            str(self._output_path),
        ]
        self._proc = subprocess.Popen(cmd, stdin=subprocess.PIPE)

    def stop(self) -> None:
        """Stop ffmpeg and finalize the video file."""
        if self._proc and self._proc.poll() is None:
            # Send 'q' to ffmpeg stdin for clean shutdown
            try:
                if self._proc.stdin is None:
                    msg = "ffmpeg stdin is not available"
                    raise RuntimeError(msg)
                self._proc.stdin.write(b"q")
                self._proc.stdin.flush()
                self._proc.wait(timeout=10)
            except (subprocess.TimeoutExpired, OSError):
                self._proc.kill()

    def is_running(self) -> bool:
        """Return True if recording is active."""
        return self._proc is not None and self._proc.poll() is None


def _hash_screenshot(data: bytes) -> str:
    """Return a short hash of screenshot bytes for deduplication."""
    return hashlib.sha256(data).hexdigest()[:16]


class AgentRecorder:
    """Unified recording + structured logging for agent sessions.

    Writes two outputs atomically on session end:
    - ``{session_id}.mp4``: screen recording
    - ``{session_id}.jsonl``: structured action log

    Args:
        session_id: Unique identifier for this agent run.
        output_dir: Directory for recordings and logs.
        display: X display string.
        fps: Recording frame rate.
        record_video: Whether to capture video (disable in CI for speed).

    Example::

        async with AgentRecorder("run-123", Path("/recordings")) as rec:
            screenshot = await take_screenshot()
            await rec.log_action("screenshot", {}, screenshot_bytes=screenshot)
            await rec.log_action("click", {"x": 500, "y": 300})
    """

    def __init__(
        self,
        session_id: str,
        output_dir: Path | str = _DEFAULT_OUTPUT_DIR,
        display: str = ":99",
        fps: int = _DEFAULT_FPS,
        record_video: bool = True,
    ) -> None:
        self._session_id = session_id
        self._output_dir = Path(output_dir)
        self._display = display
        self._record_video = record_video
        self._recorder: ScreenRecorder | None = None
        self._log_path: Path = self._output_dir / f"{session_id}.jsonl"
        self._log_file: Any = None
        self._action_count: int = 0
        self._start_time: float = 0.0

    async def __aenter__(self) -> AgentRecorder:
        self._output_dir.mkdir(parents=True, exist_ok=True)
        self._start_time = time.time()
        self._log_file = self._log_path.open("w", encoding="utf-8")

        if self._record_video:
            video_path = self._output_dir / f"{self._session_id}.mp4"
            self._recorder = ScreenRecorder(
                display=self._display,
                output_path=video_path,
                fps=5,
            )
            self._recorder.start()

        # Write session header
        header = json.dumps(
            {
                "event": "session_start",
                "session_id": self._session_id,
                "timestamp": self._start_time,
                "display": self._display,
            }
        )
        self._log_file.write(header + "\n")
        self._log_file.flush()
        return self

    async def __aexit__(self, exc_type: type | None, *_: object) -> None:
        if self._recorder:
            self._recorder.stop()

        # Write session footer
        if self._log_file:
            footer = json.dumps(
                {
                    "event": "session_end",
                    "session_id": self._session_id,
                    "timestamp": time.time(),
                    "duration_s": time.time() - self._start_time,
                    "action_count": self._action_count,
                    "error": str(exc_type.__name__) if exc_type else None,
                }
            )
            self._log_file.write(footer + "\n")
            self._log_file.close()

    async def log_action(
        self,
        action_type: str,
        params: dict[str, Any],
        screenshot_bytes: bytes | None = None,
    ) -> None:
        """Log an agent action to the structured log file.

        Args:
            action_type: Type of action (e.g. ``"click"``, ``"type"``, ``"screenshot"``).
            params: Action parameters (coordinates, text, etc.).
            screenshot_bytes: Optional screenshot taken just before the action.
        """
        screenshot_hash = ""
        if screenshot_bytes:
            screenshot_hash = _hash_screenshot(screenshot_bytes)

        entry = ActionLog(
            session_id=self._session_id,
            action_type=action_type,
            params=params,
            screenshot_hash=screenshot_hash,
        )
        self._action_count += 1

        if self._log_file:
            self._log_file.write(entry.to_jsonl() + "\n")
            self._log_file.flush()

    def session_summary(self) -> dict[str, Any]:
        """Return a summary of the current session.

        Returns:
            Dict with session stats for monitoring dashboards.
        """
        return {
            "session_id": self._session_id,
            "action_count": self._action_count,
            "duration_s": time.time() - self._start_time,
            "log_path": str(self._log_path),
            "recording_active": self._recorder.is_running() if self._recorder else False,
        }
