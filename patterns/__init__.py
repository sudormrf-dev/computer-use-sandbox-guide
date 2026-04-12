"""Computer-use sandbox patterns for production Claude agents."""

from .kill_switch import AgentKilledException, AgentWatchdog, KillReason, ProcessKillSwitch
from .recording_observability import AgentRecorder, ScreenRecorder
from .retry_deterministic import DeterministicRetry, RetryResult, UIStateVerifier
from .ux_transparency import ActionCategory, ActionNarrator, StepAnnouncement
from .virtual_display import DockerDisplayConfig, VirtualDisplay, VirtualDisplayConfig

__all__ = [
    "ActionCategory",
    "ActionNarrator",
    "AgentKilledException",
    "AgentRecorder",
    "AgentWatchdog",
    "DeterministicRetry",
    "DockerDisplayConfig",
    "KillReason",
    "ProcessKillSwitch",
    "RetryResult",
    "ScreenRecorder",
    "StepAnnouncement",
    "UIStateVerifier",
    "VirtualDisplay",
    "VirtualDisplayConfig",
]
