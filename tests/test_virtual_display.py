"""Tests for virtual_display.py."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from patterns.virtual_display import (
    DockerDisplayConfig,
    VirtualDisplay,
    VirtualDisplayConfig,
)


class TestVirtualDisplayConfig:
    def test_display_env(self):
        cfg = VirtualDisplayConfig(display_num=99)
        assert cfg.display_env == ":99"

    def test_vnc_port(self):
        cfg = VirtualDisplayConfig(display_num=5)
        assert cfg.vnc_port == 5905

    def test_screen_spec(self):
        cfg = VirtualDisplayConfig(width=1920, height=1080, depth=24)
        assert cfg.screen_spec == "1920x1080x24"

    def test_defaults(self):
        cfg = VirtualDisplayConfig()
        assert cfg.width == 1280
        assert cfg.height == 800
        assert cfg.depth == 24
        assert not cfg.vnc_enabled


class TestVirtualDisplay:
    def test_get_env(self):
        display = VirtualDisplay(VirtualDisplayConfig(display_num=99))
        env = display.get_env()
        assert env["DISPLAY"] == ":99"
        assert "XAUTHORITY" in env

    def test_is_running_false_initially(self):
        display = VirtualDisplay()
        assert not display.is_running()

    @patch("patterns.virtual_display.subprocess.Popen")
    @patch("patterns.virtual_display.time.sleep")
    def test_start_launches_xvfb(self, mock_sleep, mock_popen):
        mock_proc = MagicMock()
        mock_proc.poll.return_value = None
        mock_popen.return_value = mock_proc

        display = VirtualDisplay()
        display.start()

        assert mock_popen.called
        cmd = mock_popen.call_args[0][0]
        assert "Xvfb" in cmd
        assert ":99" in cmd

    @patch("patterns.virtual_display.subprocess.Popen")
    @patch("patterns.virtual_display.time.sleep")
    def test_start_raises_if_xvfb_exits(self, mock_sleep, mock_popen):
        mock_proc = MagicMock()
        mock_proc.poll.return_value = 1  # already exited
        mock_proc.returncode = 1
        mock_popen.return_value = mock_proc

        display = VirtualDisplay()
        with pytest.raises(RuntimeError, match="Xvfb failed"):
            display.start()

    @patch("patterns.virtual_display.subprocess.Popen")
    @patch("patterns.virtual_display.time.sleep")
    def test_stop_terminates_process(self, mock_sleep, mock_popen):
        mock_proc = MagicMock()
        mock_proc.poll.return_value = None
        mock_popen.return_value = mock_proc

        display = VirtualDisplay()
        display.start()
        display.stop()

        mock_proc.terminate.assert_called()

    @patch("patterns.virtual_display.subprocess.Popen")
    @patch("patterns.virtual_display.time.sleep")
    def test_context_manager(self, mock_sleep, mock_popen):
        mock_proc = MagicMock()
        mock_proc.poll.return_value = None
        mock_popen.return_value = mock_proc

        with VirtualDisplay():
            pass

        mock_proc.terminate.assert_called()


class TestDockerDisplayConfig:
    def test_to_docker_run_args_contains_shm(self):
        cfg = DockerDisplayConfig(shared_memory_mb=512)
        args = cfg.to_docker_run_args()
        assert "--shm-size" in args
        assert "512m" in args

    def test_to_docker_run_args_drops_privileges(self):
        cfg = DockerDisplayConfig()
        args = cfg.to_docker_run_args()
        assert "--cap-drop" in args
        assert "ALL" in args
        assert "--security-opt" in args
        assert "no-new-privileges" in args

    def test_to_docker_run_args_sets_display(self):
        cfg = DockerDisplayConfig()
        args = cfg.to_docker_run_args()
        assert any("DISPLAY=" in a for a in args)

    def test_extra_env_included(self):
        cfg = DockerDisplayConfig(extra_env={"MY_VAR": "value123"})
        args = cfg.to_docker_run_args()
        assert any("MY_VAR=value123" in a for a in args)
