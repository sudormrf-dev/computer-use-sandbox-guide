"""Virtual display configuration for headless computer-use agents.

Runs Claude computer-use (or any screen agent) inside a Docker container
with Xvfb + VNC for full visual isolation. No host display required.

Pattern:
    Container starts → Xvfb creates virtual framebuffer → VNC exposes it
    Agent calls screenshot → sees isolated virtual desktop
    Inference happens inside container → host is never touched

Usage::

    config = VirtualDisplayConfig(width=1280, height=800, depth=24)
    display = VirtualDisplay(config)
    display.start()
    # Agent can now take screenshots, click, type
    display.stop()
"""

from __future__ import annotations

import subprocess
import time
from dataclasses import dataclass, field

_DEFAULT_DISPLAY_NUM: int = 99
_DEFAULT_WIDTH: int = 1280
_DEFAULT_HEIGHT: int = 800
_DEFAULT_DEPTH: int = 24
_XVFB_STARTUP_WAIT: float = 1.0
_VNC_PORT_BASE: int = 5900


@dataclass
class VirtualDisplayConfig:
    """Configuration for a headless virtual display.

    Args:
        display_num: X display number (e.g. 99 → DISPLAY=:99).
        width: Screen width in pixels.
        height: Screen height in pixels.
        depth: Color depth (16 or 24).
        vnc_enabled: Expose VNC for live debugging.
        vnc_password: VNC password (empty = no auth, dev only).
        dpi: Screen DPI (affects font rendering).
    """

    display_num: int = _DEFAULT_DISPLAY_NUM
    width: int = _DEFAULT_WIDTH
    height: int = _DEFAULT_HEIGHT
    depth: int = _DEFAULT_DEPTH
    vnc_enabled: bool = False
    vnc_password: str = ""
    dpi: int = 96

    @property
    def display_env(self) -> str:
        """DISPLAY environment variable value."""
        return f":{self.display_num}"

    @property
    def vnc_port(self) -> int:
        """VNC port for this display number."""
        return _VNC_PORT_BASE + self.display_num

    @property
    def screen_spec(self) -> str:
        """Xvfb screen specification string."""
        return f"{self.width}x{self.height}x{self.depth}"


class VirtualDisplay:
    """Manage an Xvfb virtual display process.

    Args:
        config: Display configuration.

    Example::

        display = VirtualDisplay(VirtualDisplayConfig(width=1920, height=1080))
        display.start()
        env = display.get_env()  # {"DISPLAY": ":99", ...}
        # Run agent with env
        display.stop()
    """

    def __init__(self, config: VirtualDisplayConfig | None = None) -> None:
        self._config = config or VirtualDisplayConfig()
        self._xvfb_proc: subprocess.Popen[bytes] | None = None
        self._vnc_proc: subprocess.Popen[bytes] | None = None

    def start(self) -> None:
        """Start Xvfb and optionally VNC server.

        Raises:
            RuntimeError: If Xvfb fails to start within the timeout.
            FileNotFoundError: If Xvfb is not installed.
        """
        xvfb_cmd = [
            "Xvfb",
            self._config.display_env,
            "-screen", "0", self._config.screen_spec,
            "-dpi", str(self._config.dpi),
            "-ac",          # Disable access control (container-safe)
            "+extension", "GLX",
            "+render",
            "-noreset",
        ]
        self._xvfb_proc = subprocess.Popen(
            xvfb_cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        # Wait for Xvfb to be ready
        time.sleep(_XVFB_STARTUP_WAIT)
        if self._xvfb_proc.poll() is not None:
            msg = f"Xvfb failed to start (exit code {self._xvfb_proc.returncode})"
            raise RuntimeError(msg)

        if self._config.vnc_enabled:
            self._start_vnc()

    def _start_vnc(self) -> None:
        """Start x11vnc for live debugging access."""
        vnc_cmd = [
            "x11vnc",
            "-display", self._config.display_env,
            "-nopw" if not self._config.vnc_password else "-passwd",
        ]
        if self._config.vnc_password:
            vnc_cmd.append(self._config.vnc_password)
        vnc_cmd += ["-forever", "-shared", "-rfbport", str(self._config.vnc_port)]

        self._vnc_proc = subprocess.Popen(
            vnc_cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

    def stop(self) -> None:
        """Terminate Xvfb and VNC processes."""
        for proc in (self._vnc_proc, self._xvfb_proc):
            if proc and proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    proc.kill()

    def get_env(self) -> dict[str, str]:
        """Return environment variables needed to use this display.

        Merge with subprocess env to direct rendering to this display.

        Returns:
            Dict with ``DISPLAY`` and related variables set.
        """
        return {
            "DISPLAY": self._config.display_env,
            "XAUTHORITY": "/tmp/.Xauthority",  # nosec B108
        }

    def is_running(self) -> bool:
        """Return True if Xvfb is currently running."""
        return self._xvfb_proc is not None and self._xvfb_proc.poll() is None

    def __enter__(self) -> VirtualDisplay:
        self.start()
        return self

    def __exit__(self, *_: object) -> None:
        self.stop()


@dataclass
class DockerDisplayConfig:
    """Configuration for running a computer-use agent in Docker with virtual display.

    Args:
        image: Docker image with Xvfb + browser + agent deps.
        display_config: Virtual display settings.
        shared_memory_mb: /dev/shm size for Chrome (needs ≥512MB).
        extra_env: Additional environment variables.
    """

    image: str = "ghcr.io/anthropics/anthropic-quickstarts:computer-use-demo-latest"
    display_config: VirtualDisplayConfig = field(default_factory=VirtualDisplayConfig)
    shared_memory_mb: int = 512
    extra_env: dict[str, str] = field(default_factory=dict)

    def to_docker_run_args(self, api_key_env: str = "ANTHROPIC_API_KEY") -> list[str]:
        """Build docker run arguments for a sandboxed computer-use session.

        Args:
            api_key_env: Name of the env var holding the Anthropic API key.

        Returns:
            List of arguments to pass after ``docker run``.
        """
        cfg = self.display_config
        env_vars = {
            "DISPLAY": cfg.display_env,
            "SCREEN_WIDTH": str(cfg.width),
            "SCREEN_HEIGHT": str(cfg.height),
            **self.extra_env,
        }
        args = [
            "--shm-size", f"{self.shared_memory_mb}m",
            "--security-opt", "no-new-privileges",
            "--cap-drop", "ALL",
            "--cap-add", "SYS_PTRACE",   # needed by Chrome sandbox
        ]
        for key, val in env_vars.items():
            args += ["-e", f"{key}={val}"]
        # Forward API key from host env (never hardcode)
        args += ["-e", f"{api_key_env}"]
        args.append(self.image)
        return args
