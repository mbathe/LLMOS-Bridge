"""OS/Exec module — Implementation.

Security notes:
  - ``run_command`` always uses ``subprocess.run`` with shell=False.
  - The command is passed as a list, never a shell string.
  - Timeout is enforced at the subprocess level.
  - The PermissionGuard blocks this module entirely for READONLY profiles.
"""

from __future__ import annotations

import asyncio
import os
import platform
import subprocess
from typing import Any

import psutil

from llmos_bridge.modules.base import BaseModule, Platform
from llmos_bridge.modules.manifest import ActionSpec, ModuleManifest, ParamSpec
from llmos_bridge.security.decorators import (
    audit_trail,
    rate_limited,
    requires_permission,
    sensitive_action,
)
from llmos_bridge.security.models import Permission, RiskLevel
from llmos_bridge.protocol.params.os_exec import (
    CloseApplicationParams,
    GetEnvVarParams,
    GetProcessInfoParams,
    GetSystemInfoParams,
    KillProcessParams,
    ListProcessesParams,
    OpenApplicationParams,
    RunCommandParams,
    SetEnvVarParams,
)


class OSExecModule(BaseModule):
    MODULE_ID = "os_exec"
    VERSION = "1.0.0"
    SUPPORTED_PLATFORMS = [Platform.ALL]

    def _check_dependencies(self) -> None:
        try:
            import psutil  # noqa: F401
        except ImportError as exc:
            from llmos_bridge.exceptions import ModuleLoadError

            raise ModuleLoadError("os_exec", "psutil is required: pip install psutil") from exc

    @requires_permission(Permission.PROCESS_EXECUTE, reason="Execute system command")
    @sensitive_action(RiskLevel.MEDIUM)
    @rate_limited(calls_per_minute=30)
    @audit_trail("detailed")
    async def _action_run_command(self, params: dict[str, Any]) -> dict[str, Any]:
        p = RunCommandParams.model_validate(params)

        env = os.environ.copy()
        if p.env:
            env.update(p.env)

        proc = await asyncio.create_subprocess_exec(
            *p.command,
            stdout=asyncio.subprocess.PIPE if p.capture_output else None,
            stderr=asyncio.subprocess.PIPE if p.capture_output else None,
            stdin=asyncio.subprocess.PIPE if p.stdin else None,
            cwd=p.working_directory,
            env=env,
        )

        try:
            stdin_bytes = p.stdin.encode() if p.stdin else None
            stdout_bytes, stderr_bytes = await asyncio.wait_for(
                proc.communicate(input=stdin_bytes), timeout=p.timeout
            )
        except asyncio.TimeoutError:
            proc.kill()
            await proc.communicate()
            raise TimeoutError(
                f"Command timed out after {p.timeout}s: {' '.join(p.command)}"
            )

        return {
            "command": p.command,
            "return_code": proc.returncode,
            "stdout": stdout_bytes.decode(errors="replace") if stdout_bytes else "",
            "stderr": stderr_bytes.decode(errors="replace") if stderr_bytes else "",
            "success": proc.returncode == 0,
        }

    async def _action_list_processes(self, params: dict[str, Any]) -> dict[str, Any]:
        p = ListProcessesParams.model_validate(params)
        processes = []

        for proc in psutil.process_iter(["pid", "name", "status", "cpu_percent", "memory_info"]):
            try:
                info = proc.info
                if p.name_filter and p.name_filter.lower() not in (info["name"] or "").lower():
                    continue
                processes.append(
                    {
                        "pid": info["pid"],
                        "name": info["name"],
                        "status": info["status"],
                        "cpu_percent": info["cpu_percent"],
                        "memory_mb": (
                            round(info["memory_info"].rss / 1024 / 1024, 2)
                            if info["memory_info"]
                            else None
                        ),
                    }
                )
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue

        return {"processes": processes, "count": len(processes)}

    @requires_permission(Permission.PROCESS_KILL, reason="Terminate process")
    @sensitive_action(RiskLevel.HIGH, irreversible=True)
    @audit_trail("detailed")
    async def _action_kill_process(self, params: dict[str, Any]) -> dict[str, Any]:
        p = KillProcessParams.model_validate(params)
        import signal as _signal

        sig = _signal.SIGTERM if p.signal == "SIGTERM" else _signal.SIGKILL

        try:
            proc = psutil.Process(p.pid)
            proc.send_signal(sig)
            return {"pid": p.pid, "signal": p.signal, "success": True}
        except psutil.NoSuchProcess:
            raise ProcessLookupError(f"No process with PID {p.pid}")
        except psutil.AccessDenied:
            raise PermissionError(f"Access denied to PID {p.pid}")

    async def _action_get_process_info(self, params: dict[str, Any]) -> dict[str, Any]:
        p = GetProcessInfoParams.model_validate(params)
        try:
            proc = psutil.Process(p.pid)
            with proc.oneshot():
                return {
                    "pid": proc.pid,
                    "name": proc.name(),
                    "status": proc.status(),
                    "cpu_percent": proc.cpu_percent(),
                    "memory_mb": round(proc.memory_info().rss / 1024 / 1024, 2),
                    "cmdline": proc.cmdline(),
                    "cwd": proc.cwd(),
                    "created": proc.create_time(),
                }
        except psutil.NoSuchProcess:
            raise ProcessLookupError(f"No process with PID {p.pid}")

    @requires_permission(Permission.PROCESS_EXECUTE, reason="Launch application")
    @audit_trail("standard")
    async def _action_open_application(self, params: dict[str, Any]) -> dict[str, Any]:
        p = OpenApplicationParams.model_validate(params)
        cmd = [p.application] + p.arguments
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            cwd=p.working_directory,
            start_new_session=True,
        )
        return {"application": p.application, "pid": proc.pid}

    @requires_permission(Permission.PROCESS_KILL, reason="Close application")
    @sensitive_action(RiskLevel.MEDIUM)
    async def _action_close_application(self, params: dict[str, Any]) -> dict[str, Any]:
        p = CloseApplicationParams.model_validate(params)
        closed = []
        for proc in psutil.process_iter(["pid", "name"]):
            try:
                if p.application_name.lower() in (proc.info["name"] or "").lower():
                    if p.force:
                        proc.kill()
                    else:
                        proc.terminate()
                    closed.append(proc.info["pid"])
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
        return {"application": p.application_name, "closed_pids": closed}

    async def _action_set_env_var(self, params: dict[str, Any]) -> dict[str, Any]:
        p = SetEnvVarParams.model_validate(params)
        os.environ[p.name] = p.value
        return {"name": p.name, "scope": p.scope}

    async def _action_get_env_var(self, params: dict[str, Any]) -> dict[str, Any]:
        p = GetEnvVarParams.model_validate(params)
        value = os.environ.get(p.name)
        return {"name": p.name, "value": value, "exists": value is not None}

    async def _action_get_system_info(self, params: dict[str, Any]) -> dict[str, Any]:
        p = GetSystemInfoParams.model_validate(params)
        info: dict[str, Any] = {}

        if "os" in p.include:
            info["os"] = {
                "system": platform.system(),
                "release": platform.release(),
                "version": platform.version(),
                "machine": platform.machine(),
                "python_version": platform.python_version(),
            }
        if "cpu" in p.include:
            info["cpu"] = {
                "count": psutil.cpu_count(),
                "percent": psutil.cpu_percent(interval=0.1),
            }
        if "memory" in p.include:
            mem = psutil.virtual_memory()
            info["memory"] = {
                "total_gb": round(mem.total / 1024**3, 2),
                "available_gb": round(mem.available / 1024**3, 2),
                "percent_used": mem.percent,
            }
        if "disk" in p.include:
            disk = psutil.disk_usage("/")
            info["disk"] = {
                "total_gb": round(disk.total / 1024**3, 2),
                "free_gb": round(disk.free / 1024**3, 2),
                "percent_used": disk.percent,
            }

        return info

    def get_manifest(self) -> ModuleManifest:
        return ModuleManifest(
            module_id=self.MODULE_ID,
            version=self.VERSION,
            description="Execute system commands, manage processes, and query system information.",
            platforms=["all"],
            tags=["system", "process", "os"],
            dependencies=["psutil"],
            actions=[
                ActionSpec(
                    name="run_command",
                    description=(
                        "Run an external command. Command must be a list, never a shell string. "
                        "Returns stdout, stderr, and return code."
                    ),
                    params=[
                        ParamSpec("command", "array", "Command as list: ['git', 'status']."),
                        ParamSpec("working_directory", "string", "Working directory.", required=False),
                        ParamSpec("timeout", "integer", "Timeout in seconds.", required=False, default=30),
                        ParamSpec("capture_output", "boolean", "Capture stdout/stderr.", required=False, default=True),
                    ],
                    permission_required="local_worker",
                ),
                ActionSpec(
                    name="get_system_info",
                    description="Get CPU, memory, disk, network and OS information.",
                    params=[
                        ParamSpec("include", "array", "Categories to include: cpu, memory, disk, os.", required=False),
                    ],
                    permission_required="readonly",
                ),
            ],
        )
