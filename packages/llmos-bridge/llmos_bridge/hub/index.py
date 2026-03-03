"""Module index — SQLite registry of installed community modules.

Tracks which community modules are installed, their versions, install paths,
and whether they are enabled.  Survives daemon restarts.  Consistent with
the project's existing ``aiosqlite`` persistence pattern (PlanStateStore,
PermissionStore, TriggerStore, RecordingStore).
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import aiosqlite

from llmos_bridge.logging import get_logger

log = get_logger(__name__)


@dataclass
class InstalledModule:
    """Record of an installed community module."""

    module_id: str
    version: str
    install_path: str
    module_class_path: str
    requirements: list[str] = field(default_factory=list)
    installed_at: float = 0.0
    updated_at: float = 0.0
    enabled: bool = True
    signature_fingerprint: str = ""
    sandbox_level: str = "basic"
    python_version: str = ""  # e.g. "3.11". Empty = host Python used.
    # Security metadata (Phase 1 — Secure Hub)
    trust_tier: str = "unverified"
    scan_score: float = -1.0  # -1 = never scanned, 0-100
    scan_result_json: str = ""  # JSON blob of SourceScanResult findings
    signature_status: str = "unsigned"  # unsigned/signed/verified/expired
    publisher_id: str = ""
    checksum: str = ""  # SHA-256 of module source at install time


class ModuleIndex:
    """SQLite-backed registry of installed community modules."""

    def __init__(self, db_path: Path | str) -> None:
        self._db_path = Path(db_path).expanduser()
        self._db: aiosqlite.Connection | None = None

    async def init(self) -> None:
        """Create the database and table if needed."""
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._db = await aiosqlite.connect(str(self._db_path))
        await self._db.execute("""
            CREATE TABLE IF NOT EXISTS modules (
                module_id TEXT PRIMARY KEY,
                version TEXT NOT NULL,
                install_path TEXT NOT NULL,
                module_class_path TEXT NOT NULL,
                requirements TEXT NOT NULL DEFAULT '[]',
                installed_at REAL NOT NULL,
                updated_at REAL NOT NULL,
                enabled INTEGER NOT NULL DEFAULT 1,
                signature_fingerprint TEXT DEFAULT '',
                sandbox_level TEXT DEFAULT 'basic',
                python_version TEXT DEFAULT ''
            )
        """)
        # Schema migrations: add columns if missing (backward compat).
        _migrations = [
            "ALTER TABLE modules ADD COLUMN python_version TEXT DEFAULT ''",
            "ALTER TABLE modules ADD COLUMN trust_tier TEXT DEFAULT 'unverified'",
            "ALTER TABLE modules ADD COLUMN scan_score REAL DEFAULT -1.0",
            "ALTER TABLE modules ADD COLUMN scan_result_json TEXT DEFAULT ''",
            "ALTER TABLE modules ADD COLUMN signature_status TEXT DEFAULT 'unsigned'",
            "ALTER TABLE modules ADD COLUMN publisher_id TEXT DEFAULT ''",
            "ALTER TABLE modules ADD COLUMN checksum TEXT DEFAULT ''",
        ]
        for migration in _migrations:
            try:
                await self._db.execute(migration)
            except Exception:
                pass  # Column already exists.
        await self._db.commit()

    async def close(self) -> None:
        """Close the database connection."""
        if self._db is not None:
            await self._db.close()
            self._db = None

    async def add(self, module: InstalledModule) -> None:
        """Add a newly installed module to the index."""
        assert self._db is not None
        now = time.time()
        await self._db.execute(
            """INSERT OR REPLACE INTO modules
               (module_id, version, install_path, module_class_path,
                requirements, installed_at, updated_at, enabled,
                signature_fingerprint, sandbox_level, python_version,
                trust_tier, scan_score, scan_result_json,
                signature_status, publisher_id, checksum)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                module.module_id,
                module.version,
                module.install_path,
                module.module_class_path,
                json.dumps(module.requirements),
                module.installed_at or now,
                module.updated_at or now,
                1 if module.enabled else 0,
                module.signature_fingerprint,
                module.sandbox_level,
                module.python_version,
                module.trust_tier,
                module.scan_score,
                module.scan_result_json,
                module.signature_status,
                module.publisher_id,
                module.checksum,
            ),
        )
        await self._db.commit()

    async def remove(self, module_id: str) -> None:
        """Remove a module from the index."""
        assert self._db is not None
        await self._db.execute(
            "DELETE FROM modules WHERE module_id = ?", (module_id,)
        )
        await self._db.commit()

    async def get(self, module_id: str) -> InstalledModule | None:
        """Get a single installed module by ID."""
        assert self._db is not None
        async with self._db.execute(
            "SELECT * FROM modules WHERE module_id = ?", (module_id,)
        ) as cursor:
            row = await cursor.fetchone()
            if row is None:
                return None
            return self._row_to_module(row)

    async def list_all(self) -> list[InstalledModule]:
        """List all installed modules."""
        assert self._db is not None
        async with self._db.execute(
            "SELECT * FROM modules ORDER BY module_id"
        ) as cursor:
            rows = await cursor.fetchall()
            return [self._row_to_module(r) for r in rows]

    async def list_enabled(self) -> list[InstalledModule]:
        """List only enabled modules."""
        assert self._db is not None
        async with self._db.execute(
            "SELECT * FROM modules WHERE enabled = 1 ORDER BY module_id"
        ) as cursor:
            rows = await cursor.fetchall()
            return [self._row_to_module(r) for r in rows]

    async def update_version(
        self, module_id: str, version: str, install_path: str
    ) -> None:
        """Update a module's version and path after upgrade."""
        assert self._db is not None
        await self._db.execute(
            "UPDATE modules SET version = ?, install_path = ?, updated_at = ? WHERE module_id = ?",
            (version, install_path, time.time(), module_id),
        )
        await self._db.commit()

    async def set_enabled(self, module_id: str, enabled: bool) -> None:
        """Enable or disable a module."""
        assert self._db is not None
        await self._db.execute(
            "UPDATE modules SET enabled = ? WHERE module_id = ?",
            (1 if enabled else 0, module_id),
        )
        await self._db.commit()

    @staticmethod
    def _row_to_module(row: Any) -> InstalledModule:
        """Convert a database row to an InstalledModule.

        Uses positional indexing with ``len(row)`` guards for backward
        compat with databases that don't yet have all columns.
        """
        n = len(row)
        return InstalledModule(
            module_id=row[0],
            version=row[1],
            install_path=row[2],
            module_class_path=row[3],
            requirements=json.loads(row[4]) if row[4] else [],
            installed_at=row[5],
            updated_at=row[6],
            enabled=bool(row[7]),
            signature_fingerprint=row[8] or "",
            sandbox_level=row[9] or "basic",
            python_version=row[10] if n > 10 else "",
            trust_tier=row[11] if n > 11 else "unverified",
            scan_score=row[12] if n > 12 else -1.0,
            scan_result_json=row[13] if n > 13 else "",
            signature_status=row[14] if n > 14 else "unsigned",
            publisher_id=row[15] if n > 15 else "",
            checksum=row[16] if n > 16 else "",
        )

    # ------------------------------------------------------------------
    # Security metadata
    # ------------------------------------------------------------------

    async def update_security_data(
        self,
        module_id: str,
        *,
        trust_tier: str = "",
        scan_score: float = -1.0,
        scan_result_json: str = "",
        signature_status: str = "",
        checksum: str = "",
    ) -> None:
        """Update security metadata for an installed module."""
        assert self._db is not None
        updates: list[str] = []
        values: list[Any] = []
        if trust_tier:
            updates.append("trust_tier = ?")
            values.append(trust_tier)
        if scan_score >= 0:
            updates.append("scan_score = ?")
            values.append(scan_score)
        if scan_result_json:
            updates.append("scan_result_json = ?")
            values.append(scan_result_json)
        if signature_status:
            updates.append("signature_status = ?")
            values.append(signature_status)
        if checksum:
            updates.append("checksum = ?")
            values.append(checksum)
        if not updates:
            return
        updates.append("updated_at = ?")
        values.append(time.time())
        values.append(module_id)
        sql = f"UPDATE modules SET {', '.join(updates)} WHERE module_id = ?"
        await self._db.execute(sql, values)
        await self._db.commit()

    async def update_trust_tier(self, module_id: str, trust_tier: str) -> None:
        """Update only the trust tier for a module."""
        await self.update_security_data(module_id, trust_tier=trust_tier)

    async def get_security_data(self, module_id: str) -> dict[str, Any] | None:
        """Return security-related fields for a module, or None if not found."""
        module = await self.get(module_id)
        if module is None:
            return None
        return {
            "module_id": module.module_id,
            "trust_tier": module.trust_tier,
            "scan_score": module.scan_score,
            "scan_result_json": module.scan_result_json,
            "signature_status": module.signature_status,
            "publisher_id": module.publisher_id,
            "checksum": module.checksum,
        }
