"""Shared pytest fixtures for the llmos-bridge test suite."""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import AsyncGenerator, Generator
from unittest.mock import AsyncMock, MagicMock

import pytest
import pytest_asyncio

from llmos_bridge.config import Settings, SecurityConfig, override_settings
from llmos_bridge.modules.filesystem import FilesystemModule
from llmos_bridge.modules.os_exec import OSExecModule
from llmos_bridge.modules.registry import ModuleRegistry
from llmos_bridge.orchestration.state import PlanStateStore
from llmos_bridge.protocol.models import (
    ExecutionMode,
    IMLAction,
    IMLPlan,
    OnErrorBehavior,
)
from llmos_bridge.protocol.parser import IMLParser
from llmos_bridge.security.audit import AuditLogger
from llmos_bridge.security.guard import PermissionGuard
from llmos_bridge.security.profiles import PermissionProfile, get_profile_config


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------


@pytest.fixture
def test_settings(tmp_path: Path) -> Settings:
    settings = Settings(
        memory={"state_db_path": str(tmp_path / "state.db"), "vector_db_path": str(tmp_path / "vector")},
        logging={"level": "debug", "format": "console", "audit_file": None},
    )
    override_settings(settings)
    return settings


# ---------------------------------------------------------------------------
# Protocol
# ---------------------------------------------------------------------------


@pytest.fixture
def parser() -> IMLParser:
    return IMLParser()


@pytest.fixture
def minimal_plan() -> IMLPlan:
    return IMLPlan(
        plan_id="test-plan-001",
        description="Minimal test plan",
        actions=[
            IMLAction(
                id="a1",
                action="read_file",
                module="filesystem",
                params={"path": "/tmp/test.txt"},
            )
        ],
    )


@pytest.fixture
def sequential_plan() -> IMLPlan:
    return IMLPlan(
        plan_id="test-plan-seq",
        description="Sequential plan with dependencies",
        execution_mode=ExecutionMode.SEQUENTIAL,
        actions=[
            IMLAction(id="a1", action="read_file", module="filesystem", params={"path": "/tmp/a.txt"}),
            IMLAction(id="a2", action="write_file", module="filesystem",
                      params={"path": "/tmp/b.txt", "content": "{{result.a1.content}}"},
                      depends_on=["a1"]),
            IMLAction(id="a3", action="get_file_info", module="filesystem",
                      params={"path": "/tmp/b.txt"}, depends_on=["a2"]),
        ],
    )


@pytest.fixture
def parallel_plan() -> IMLPlan:
    return IMLPlan(
        plan_id="test-plan-par",
        description="Parallel plan",
        execution_mode=ExecutionMode.PARALLEL,
        actions=[
            IMLAction(id="a1", action="get_file_info", module="filesystem", params={"path": "/tmp/x.txt"}),
            IMLAction(id="a2", action="get_file_info", module="filesystem", params={"path": "/tmp/y.txt"}),
            IMLAction(id="a3", action="get_file_info", module="filesystem", params={"path": "/tmp/z.txt"}),
        ],
    )


# ---------------------------------------------------------------------------
# Security
# ---------------------------------------------------------------------------


@pytest.fixture
def readonly_guard() -> PermissionGuard:
    return PermissionGuard(profile=get_profile_config(PermissionProfile.READONLY))


@pytest.fixture
def local_worker_guard() -> PermissionGuard:
    return PermissionGuard(profile=get_profile_config(PermissionProfile.LOCAL_WORKER))


@pytest.fixture
def unrestricted_guard() -> PermissionGuard:
    return PermissionGuard(profile=get_profile_config(PermissionProfile.UNRESTRICTED))


@pytest.fixture
def audit_logger() -> AuditLogger:
    return AuditLogger(audit_file=None)  # No file — sink to /dev/null


# ---------------------------------------------------------------------------
# Modules
# ---------------------------------------------------------------------------


@pytest.fixture
def module_registry() -> ModuleRegistry:
    registry = ModuleRegistry()
    registry.register(FilesystemModule)
    registry.register(OSExecModule)
    return registry


# ---------------------------------------------------------------------------
# State store
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def state_store(tmp_path: Path) -> AsyncGenerator[PlanStateStore, None]:
    store = PlanStateStore(tmp_path / "test_state.db")
    await store.init()
    yield store
    await store.close()


# ---------------------------------------------------------------------------
# Temp filesystem
# ---------------------------------------------------------------------------


@pytest.fixture
def tmp_text_file(tmp_path: Path) -> Path:
    f = tmp_path / "test.txt"
    f.write_text("Hello, LLMOS Bridge!\nLine 2\nLine 3\n", encoding="utf-8")
    return f
