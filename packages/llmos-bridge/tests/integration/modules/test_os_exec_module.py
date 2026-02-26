"""Integration tests — OSExecModule against the real OS."""

from __future__ import annotations

import os
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import psutil
import pytest

from llmos_bridge.modules.os_exec import OSExecModule


@pytest.fixture
def module() -> OSExecModule:
    return OSExecModule()


@pytest.mark.integration
class TestRunCommand:
    async def test_run_echo(self, module: OSExecModule) -> None:
        result = await module._action_run_command(
            {"command": ["echo", "hello"]}
        )
        assert result["return_code"] == 0
        assert "hello" in result["stdout"]

    async def test_run_true(self, module: OSExecModule) -> None:
        result = await module._action_run_command({"command": ["true"]})
        assert result["return_code"] == 0
        assert result["success"] is True

    async def test_run_false_returns_nonzero(self, module: OSExecModule) -> None:
        result = await module._action_run_command({"command": ["false"]})
        assert result["return_code"] != 0
        assert result["success"] is False

    async def test_run_with_env(self, module: OSExecModule) -> None:
        result = await module._action_run_command(
            {
                "command": ["env"],
                "env": {"MY_TEST_VAR": "hello_llmos"},
            }
        )
        assert result["return_code"] == 0
        assert "MY_TEST_VAR=hello_llmos" in result["stdout"]

    async def test_run_with_working_directory(self, module: OSExecModule, tmp_path: Path) -> None:
        result = await module._action_run_command(
            {"command": ["pwd"], "working_directory": str(tmp_path)}
        )
        assert result["return_code"] == 0
        assert str(tmp_path) in result["stdout"]

    async def test_run_with_stdin(self, module: OSExecModule) -> None:
        result = await module._action_run_command(
            {"command": ["cat"], "stdin": "piped input"}
        )
        assert result["return_code"] == 0
        assert "piped input" in result["stdout"]

    async def test_run_stderr_captured(self, module: OSExecModule) -> None:
        result = await module._action_run_command(
            {"command": ["sh", "-c", "echo error >&2"]}
        )
        assert "error" in result["stderr"]

    async def test_run_multiword_command(self, module: OSExecModule) -> None:
        result = await module._action_run_command(
            {"command": ["sh", "-c", "echo 'hello world' | tr ' ' '_'"]}
        )
        assert result["return_code"] == 0
        assert "hello_world" in result["stdout"]

    async def test_run_timeout_raises(self, module: OSExecModule) -> None:
        with pytest.raises(TimeoutError):
            await module._action_run_command(
                {"command": ["sleep", "10"], "timeout": 1}
            )


@pytest.mark.integration
class TestProcessManagement:
    async def test_list_processes(self, module: OSExecModule) -> None:
        result = await module._action_list_processes({})
        assert result["count"] > 0
        assert all("pid" in p for p in result["processes"])

    async def test_list_processes_with_filter(self, module: OSExecModule) -> None:
        result = await module._action_list_processes({"name_filter": "python"})
        # May or may not find python processes, just test it doesn't crash
        assert "processes" in result
        assert "count" in result

    async def test_get_process_info_self(self, module: OSExecModule) -> None:
        result = await module._action_get_process_info({"pid": os.getpid()})
        assert result["pid"] == os.getpid()
        assert "name" in result
        assert result["memory_mb"] > 0

    async def test_get_process_info_nonexistent_raises(self, module: OSExecModule) -> None:
        with pytest.raises(ProcessLookupError):
            await module._action_get_process_info({"pid": 9999999})

    async def test_kill_nonexistent_raises(self, module: OSExecModule) -> None:
        with pytest.raises(ProcessLookupError):
            await module._action_kill_process({"pid": 9999999})

    async def test_kill_process_sigkill(self, module: OSExecModule) -> None:
        """kill_process with SIGKILL succeeds against a mock process."""
        mock_proc = MagicMock()
        mock_proc.send_signal = MagicMock(return_value=None)
        with patch("psutil.Process", return_value=mock_proc):
            result = await module._action_kill_process({"pid": 12345, "signal": "SIGKILL"})
        assert result["success"] is True
        assert result["pid"] == 12345

    async def test_kill_process_sigterm(self, module: OSExecModule) -> None:
        """kill_process with SIGTERM succeeds against a mock process."""
        mock_proc = MagicMock()
        mock_proc.send_signal = MagicMock(return_value=None)
        with patch("psutil.Process", return_value=mock_proc):
            result = await module._action_kill_process({"pid": 99999, "signal": "SIGTERM"})
        assert result["success"] is True

    async def test_kill_process_access_denied_raises(self, module: OSExecModule) -> None:
        """kill_process raises PermissionError on AccessDenied."""
        mock_proc = MagicMock()
        mock_proc.send_signal = MagicMock(side_effect=psutil.AccessDenied(12345))
        with patch("psutil.Process", return_value=mock_proc):
            with pytest.raises(PermissionError):
                await module._action_kill_process({"pid": 12345})

    async def test_list_processes_skips_dead_process(self, module: OSExecModule) -> None:
        """list_processes silently skips NoSuchProcess exceptions."""
        def _iter(*args, **kwargs):
            proc1 = MagicMock()
            proc1.info = {"pid": 1, "name": "init", "status": "running",
                          "cpu_percent": 0.0, "memory_info": MagicMock(rss=1024*1024)}
            proc2 = MagicMock()
            # Accessing .info raises NoSuchProcess
            type(proc2).info = property(lambda self: (_ for _ in ()).throw(psutil.NoSuchProcess(2)))
            return iter([proc1, proc2])

        with patch("psutil.process_iter", side_effect=_iter):
            result = await module._action_list_processes({})
        # proc1 should be counted, proc2 should be skipped
        assert result["count"] >= 1


@pytest.mark.integration
class TestOpenCloseApplication:
    async def test_open_application_returns_pid(self, module: OSExecModule) -> None:
        """open_application spawns a process and returns its PID."""
        result = await module._action_open_application(
            {"application": "sleep", "arguments": ["60"]}
        )
        assert "pid" in result
        assert result["application"] == "sleep"
        pid = result["pid"]
        # Cleanup: kill the spawned process
        try:
            proc = psutil.Process(pid)
            proc.kill()
            proc.wait(timeout=2)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass

    async def test_close_application_terminates_matching(self, module: OSExecModule) -> None:
        """close_application terminates processes matching the name."""
        mock_proc = MagicMock()
        mock_proc.info = {"pid": 5678, "name": "test_fake_app"}
        mock_proc.terminate = MagicMock()

        with patch("psutil.process_iter", return_value=[mock_proc]):
            result = await module._action_close_application(
                {"application_name": "test_fake_app"}
            )
        assert 5678 in result["closed_pids"]
        mock_proc.terminate.assert_called_once()

    async def test_close_application_force_kills(self, module: OSExecModule) -> None:
        """close_application with force=True uses kill() instead of terminate()."""
        mock_proc = MagicMock()
        mock_proc.info = {"pid": 5679, "name": "test_fake_app2"}
        mock_proc.kill = MagicMock()
        mock_proc.terminate = MagicMock()

        with patch("psutil.process_iter", return_value=[mock_proc]):
            result = await module._action_close_application(
                {"application_name": "test_fake_app2", "force": True}
            )
        assert 5679 in result["closed_pids"]
        mock_proc.kill.assert_called_once()
        mock_proc.terminate.assert_not_called()

    async def test_close_application_no_match(self, module: OSExecModule) -> None:
        """close_application returns empty list when no process matches."""
        mock_proc = MagicMock()
        mock_proc.info = {"pid": 1000, "name": "other_app"}

        with patch("psutil.process_iter", return_value=[mock_proc]):
            result = await module._action_close_application(
                {"application_name": "nonexistent_app_xyz"}
            )
        assert result["closed_pids"] == []


@pytest.mark.integration
class TestEnvironmentVars:
    async def test_set_and_get_env_var(self, module: OSExecModule) -> None:
        await module._action_set_env_var({"name": "LLMOS_TEST_VAR", "value": "test_value"})
        result = await module._action_get_env_var({"name": "LLMOS_TEST_VAR"})
        assert result["value"] == "test_value"
        assert result["exists"] is True
        # Cleanup
        del os.environ["LLMOS_TEST_VAR"]

    async def test_get_nonexistent_env_var(self, module: OSExecModule) -> None:
        result = await module._action_get_env_var({"name": "LLMOS_NONEXISTENT_VAR_XYZ"})
        assert result["exists"] is False
        assert result["value"] is None

    async def test_get_path_env_var(self, module: OSExecModule) -> None:
        result = await module._action_get_env_var({"name": "PATH"})
        assert result["exists"] is True
        assert result["value"] is not None


@pytest.mark.integration
class TestSystemInfo:
    async def test_get_system_info_os(self, module: OSExecModule) -> None:
        result = await module._action_get_system_info({"include": ["os"]})
        assert "os" in result
        assert result["os"]["system"] in ("Linux", "Darwin", "Windows")

    async def test_get_system_info_cpu(self, module: OSExecModule) -> None:
        result = await module._action_get_system_info({"include": ["cpu"]})
        assert "cpu" in result
        assert result["cpu"]["count"] >= 1

    async def test_get_system_info_memory(self, module: OSExecModule) -> None:
        result = await module._action_get_system_info({"include": ["memory"]})
        assert "memory" in result
        assert result["memory"]["total_gb"] > 0

    async def test_get_system_info_disk(self, module: OSExecModule) -> None:
        result = await module._action_get_system_info({"include": ["disk"]})
        assert "disk" in result
        assert result["disk"]["total_gb"] > 0

    async def test_get_system_info_all(self, module: OSExecModule) -> None:
        result = await module._action_get_system_info(
            {"include": ["os", "cpu", "memory", "disk"]}
        )
        assert all(k in result for k in ("os", "cpu", "memory", "disk"))
