"""Typed parameter models for the ``os_exec`` module."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, Field, field_validator


class RunCommandParams(BaseModel):
    """Execute an external command.

    The command is always passed as a list — never as a shell string.
    This prevents shell-injection vulnerabilities.
    """

    command: list[str] = Field(
        min_length=1,
        description="Command and arguments as a list. E.g. ['git', 'status', '--short'].",
    )
    working_directory: str | None = Field(
        default=None, description="Working directory for the command."
    )
    env: dict[str, str] | None = Field(
        default=None,
        description="Additional environment variables (merged with current env).",
    )
    timeout: Annotated[int, Field(ge=1, le=600)] = 30
    capture_output: bool = True
    stdin: str | None = Field(
        default=None, description="Optional data to pipe to stdin."
    )

    @field_validator("command")
    @classmethod
    def command_not_empty_strings(cls, v: list[str]) -> list[str]:
        if any(not part.strip() for part in v):
            raise ValueError("Command parts must not be empty strings.")
        return v


class ListProcessesParams(BaseModel):
    name_filter: str | None = Field(
        default=None, description="Filter processes whose name contains this string."
    )
    include_children: bool = False


class KillProcessParams(BaseModel):
    pid: Annotated[int, Field(ge=1)] = Field(description="PID of the process to kill.")
    signal: Literal["SIGTERM", "SIGKILL"] = "SIGTERM"


class GetProcessInfoParams(BaseModel):
    pid: Annotated[int, Field(ge=1)]


class OpenApplicationParams(BaseModel):
    application: str = Field(
        description="Application name or full path to the executable."
    )
    arguments: list[str] = Field(default_factory=list)
    working_directory: str | None = None


class CloseApplicationParams(BaseModel):
    application_name: str = Field(
        description="Name of the application window or process to close."
    )
    force: bool = Field(
        default=False, description="If True, forcibly kill the process (SIGKILL)."
    )


class SetEnvVarParams(BaseModel):
    name: str = Field(description="Environment variable name.")
    value: str = Field(description="Value to set.")
    scope: Literal["process"] = Field(
        default="process",
        description=(
            "Scope of the variable. Currently only 'process' is supported "
            "(system-wide changes are not permitted)."
        ),
    )


class GetEnvVarParams(BaseModel):
    name: str = Field(description="Environment variable name to read.")


class GetSystemInfoParams(BaseModel):
    include: list[Literal["cpu", "memory", "disk", "network", "os"]] = Field(
        default_factory=lambda: ["cpu", "memory", "disk", "os"]
    )


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

PARAMS_MAP: dict[str, type[BaseModel]] = {
    "run_command": RunCommandParams,
    "list_processes": ListProcessesParams,
    "kill_process": KillProcessParams,
    "get_process_info": GetProcessInfoParams,
    "open_application": OpenApplicationParams,
    "close_application": CloseApplicationParams,
    "set_env_var": SetEnvVarParams,
    "get_env_var": GetEnvVarParams,
    "get_system_info": GetSystemInfoParams,
}
