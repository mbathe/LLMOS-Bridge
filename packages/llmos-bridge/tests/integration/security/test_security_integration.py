"""Integration tests — Full security stack: decorators + PermissionManager + executor.

Verifies that security decorators on real module actions interact correctly
with PermissionManager, PermissionStore, ActionRateLimiter, and AuditLogger
as a complete, integrated stack (no mocks on the security path).

Setup:
  - PermissionStore backed by a real SQLite DB in tmp_path
  - PermissionManager(store, audit, auto_grant_low_risk=True)
  - ActionRateLimiter (in-memory)
  - AuditLogger() (NullEventBus — no file output)
  - SecurityManager aggregating all three
  - FilesystemModule + SecurityModule injected with the SecurityManager
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import AsyncGenerator

import pytest
import pytest_asyncio

from llmos_bridge.exceptions import PermissionNotGrantedError, RateLimitExceededError
from llmos_bridge.modules.filesystem import FilesystemModule
from llmos_bridge.modules.security import SecurityModule
from llmos_bridge.security.audit import AuditLogger
from llmos_bridge.security.manager import SecurityManager
from llmos_bridge.security.models import Permission, PermissionScope
from llmos_bridge.security.permission_store import PermissionStore
from llmos_bridge.security.permissions import PermissionManager
from llmos_bridge.security.rate_limiter import ActionRateLimiter


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def permission_store(tmp_path: Path) -> AsyncGenerator[PermissionStore, None]:
    store = PermissionStore(tmp_path / "permissions.db")
    await store.init()
    yield store
    await store.close()


@pytest.fixture
def audit() -> AuditLogger:
    return AuditLogger()  # NullEventBus — no file output


@pytest.fixture
def rate_limiter() -> ActionRateLimiter:
    return ActionRateLimiter()


@pytest_asyncio.fixture
async def security_manager(
    permission_store: PermissionStore,
    audit: AuditLogger,
    rate_limiter: ActionRateLimiter,
) -> SecurityManager:
    pm = PermissionManager(permission_store, audit, auto_grant_low_risk=True)
    return SecurityManager(
        permission_manager=pm,
        rate_limiter=rate_limiter,
        audit=audit,
    )


@pytest.fixture
def fs_module(security_manager: SecurityManager) -> FilesystemModule:
    module = FilesystemModule()
    module.set_security(security_manager)
    return module


@pytest.fixture
def security_module(security_manager: SecurityManager) -> SecurityModule:
    module = SecurityModule()
    module.set_security_manager(security_manager)
    return module


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestSecurityIntegration:
    """Full-stack security integration tests."""

    # 1. read_file succeeds (auto-grants filesystem.read since LOW risk)
    async def test_read_file_auto_grants_low_risk(
        self,
        fs_module: FilesystemModule,
        tmp_path: Path,
    ) -> None:
        """read_file should succeed because filesystem.read is LOW risk
        and auto_grant_low_risk=True causes automatic granting."""
        f = Path(os.path.join(str(tmp_path), "test.txt"))
        f.write_text("hello world", encoding="utf-8")

        result = await fs_module.execute("read_file", {"path": str(f)})
        assert result["content"] == "hello world"

    # 2. After read_file, filesystem.read permission is in the store
    async def test_read_file_persists_grant_in_store(
        self,
        fs_module: FilesystemModule,
        permission_store: PermissionStore,
        tmp_path: Path,
    ) -> None:
        """After a successful read_file, the auto-granted permission should
        be persisted in the PermissionStore."""
        f = Path(os.path.join(str(tmp_path), "test.txt"))
        f.write_text("content", encoding="utf-8")

        await fs_module.execute("read_file", {"path": str(f)})

        granted = await permission_store.is_granted(
            Permission.FILESYSTEM_READ, "filesystem"
        )
        assert granted is True

    # 3. write_file succeeds after granting filesystem.write permission
    async def test_write_file_succeeds_after_manual_grant(
        self,
        fs_module: FilesystemModule,
        security_manager: SecurityManager,
        tmp_path: Path,
    ) -> None:
        """write_file requires filesystem.write (MEDIUM risk), which is NOT
        auto-granted.  After explicitly granting it, the action should succeed."""
        await security_manager.permission_manager.grant(
            Permission.FILESYSTEM_WRITE,
            "filesystem",
            PermissionScope.SESSION,
            reason="test grant",
        )

        f = Path(os.path.join(str(tmp_path), "test.txt"))
        result = await fs_module.execute(
            "write_file", {"path": str(f), "content": "written"}
        )
        assert result["bytes_written"] > 0
        assert f.read_text() == "written"

    # 4. write_file fails with PermissionNotGrantedError when not granted
    #    AND auto_grant_low_risk=False
    async def test_write_file_denied_without_grant(
        self,
        permission_store: PermissionStore,
        audit: AuditLogger,
        tmp_path: Path,
    ) -> None:
        """With auto_grant_low_risk=False, filesystem.write (MEDIUM risk) is
        NOT auto-granted and the action should raise PermissionNotGrantedError."""
        strict_pm = PermissionManager(
            permission_store, audit, auto_grant_low_risk=False
        )
        strict_sm = SecurityManager(
            permission_manager=strict_pm,
            rate_limiter=ActionRateLimiter(),
            audit=audit,
        )
        module = FilesystemModule()
        module.set_security(strict_sm)

        f = Path(os.path.join(str(tmp_path), "test.txt"))
        with pytest.raises(PermissionNotGrantedError) as exc_info:
            await module.execute(
                "write_file", {"path": str(f), "content": "denied"}
            )
        assert exc_info.value.permission == Permission.FILESYSTEM_WRITE
        assert exc_info.value.module_id == "filesystem"

    # 5. delete_file fails with PermissionNotGrantedError (HIGH risk, not auto-granted)
    async def test_delete_file_denied_high_risk(
        self,
        fs_module: FilesystemModule,
        tmp_path: Path,
    ) -> None:
        """filesystem.delete is HIGH risk and should never be auto-granted,
        so delete_file must raise PermissionNotGrantedError."""
        f = Path(os.path.join(str(tmp_path), "test.txt"))
        f.write_text("to delete", encoding="utf-8")

        with pytest.raises(PermissionNotGrantedError) as exc_info:
            await fs_module.execute("delete_file", {"path": str(f)})
        assert exc_info.value.permission == Permission.FILESYSTEM_DELETE

    # 6. After granting filesystem.delete, delete_file succeeds
    async def test_delete_file_succeeds_after_grant(
        self,
        fs_module: FilesystemModule,
        security_manager: SecurityManager,
        tmp_path: Path,
    ) -> None:
        """Once filesystem.delete is explicitly granted, delete_file should
        remove the file successfully."""
        f = Path(os.path.join(str(tmp_path), "test.txt"))
        f.write_text("goodbye", encoding="utf-8")
        assert f.exists()

        await security_manager.permission_manager.grant(
            Permission.FILESYSTEM_DELETE,
            "filesystem",
            PermissionScope.SESSION,
            reason="integration test",
        )

        result = await fs_module.execute("delete_file", {"path": str(f)})
        assert result["deleted"] == str(f)
        assert not f.exists()

    # 7. SecurityModule list_permissions shows granted permissions
    async def test_security_module_list_permissions(
        self,
        security_module: SecurityModule,
        security_manager: SecurityManager,
    ) -> None:
        """After granting permissions, list_permissions should return them."""
        await security_manager.permission_manager.grant(
            Permission.FILESYSTEM_READ,
            "filesystem",
            PermissionScope.SESSION,
        )
        await security_manager.permission_manager.grant(
            Permission.FILESYSTEM_WRITE,
            "filesystem",
            PermissionScope.SESSION,
        )

        result = await security_module.execute("list_permissions", {})
        assert result["count"] == 2
        perms = {g["permission"] for g in result["grants"]}
        assert Permission.FILESYSTEM_READ in perms
        assert Permission.FILESYSTEM_WRITE in perms

    # 8. SecurityModule request_permission grants successfully
    async def test_security_module_request_permission(
        self,
        security_module: SecurityModule,
        permission_store: PermissionStore,
    ) -> None:
        """request_permission via the SecurityModule should persist the
        grant in the store."""
        result = await security_module.execute(
            "request_permission",
            {
                "permission": Permission.NETWORK_READ,
                "module_id": "api_http",
                "reason": "Need to fetch data",
                "scope": "session",
            },
        )
        assert result["granted"] is True
        assert result["permission"] == Permission.NETWORK_READ

        # Verify it is actually in the store
        granted = await permission_store.is_granted(
            Permission.NETWORK_READ, "api_http"
        )
        assert granted is True

    # 9. SecurityModule get_security_status shows correct counts
    async def test_security_module_get_security_status(
        self,
        security_module: SecurityModule,
        security_manager: SecurityManager,
    ) -> None:
        """get_security_status should reflect the correct total grants and
        breakdown by module and risk level."""
        pm = security_manager.permission_manager
        # Grant 2 filesystem permissions (1 LOW, 1 MEDIUM)
        await pm.grant(
            Permission.FILESYSTEM_READ, "filesystem", PermissionScope.SESSION
        )
        await pm.grant(
            Permission.FILESYSTEM_WRITE, "filesystem", PermissionScope.SESSION
        )
        # Grant 1 network permission (LOW)
        await pm.grant(
            Permission.NETWORK_READ, "api_http", PermissionScope.SESSION
        )

        result = await security_module.execute("get_security_status", {})

        assert result["total_grants"] == 3
        assert result["grants_by_module"]["filesystem"] == 2
        assert result["grants_by_module"]["api_http"] == 1
        assert result["grants_by_risk_level"]["low"] == 2   # read + network_read
        assert result["grants_by_risk_level"]["medium"] == 1  # write

    # 10. rate_limiter blocks after exceeding limit
    async def test_rate_limiter_blocks_after_limit(
        self,
        rate_limiter: ActionRateLimiter,
    ) -> None:
        """After recording enough calls to exceed the per-minute limit,
        check_or_raise should raise RateLimitExceededError."""
        action_key = "filesystem.read_file"
        limit = 3

        # Record calls up to the limit
        for _ in range(limit):
            rate_limiter.check_or_raise(
                action_key, calls_per_minute=limit
            )

        # The next call should be blocked
        with pytest.raises(RateLimitExceededError) as exc_info:
            rate_limiter.check_or_raise(
                action_key, calls_per_minute=limit
            )
        assert exc_info.value.action_key == action_key
        assert exc_info.value.limit == limit
        assert exc_info.value.window == "minute"
