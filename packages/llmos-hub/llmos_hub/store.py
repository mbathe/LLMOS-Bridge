"""SQLite-backed store for hub data (publishers, modules, versions, ratings)."""

from __future__ import annotations

import json
import time

import aiosqlite
import structlog

from llmos_hub.models import ModuleRecord, PublisherRecord, RatingRecord, VersionRecord

log = structlog.get_logger(__name__)

_SCHEMA = """\
CREATE TABLE IF NOT EXISTS publishers (
    publisher_id TEXT PRIMARY KEY,
    name         TEXT NOT NULL,
    api_key_hash TEXT NOT NULL UNIQUE,
    created_at   REAL NOT NULL,
    enabled      INTEGER NOT NULL DEFAULT 1,
    email        TEXT NOT NULL DEFAULT '',
    description  TEXT NOT NULL DEFAULT '',
    website      TEXT NOT NULL DEFAULT '',
    verified     INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS modules (
    module_id              TEXT PRIMARY KEY,
    latest_version         TEXT NOT NULL,
    description            TEXT NOT NULL DEFAULT '',
    author                 TEXT NOT NULL DEFAULT '',
    license                TEXT NOT NULL DEFAULT '',
    tags_json              TEXT NOT NULL DEFAULT '[]',
    downloads              INTEGER NOT NULL DEFAULT 0,
    publisher_id           TEXT NOT NULL DEFAULT '',
    created_at             REAL NOT NULL,
    updated_at             REAL NOT NULL,
    average_rating         REAL NOT NULL DEFAULT 0.0,
    rating_count           INTEGER NOT NULL DEFAULT 0,
    category               TEXT NOT NULL DEFAULT '',
    deprecated             INTEGER NOT NULL DEFAULT 0,
    deprecated_message     TEXT NOT NULL DEFAULT '',
    replacement_module_id  TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS versions (
    module_id          TEXT NOT NULL,
    version            TEXT NOT NULL,
    package_path       TEXT NOT NULL,
    checksum           TEXT NOT NULL,
    scan_score         REAL NOT NULL DEFAULT 0.0,
    published_at       REAL NOT NULL,
    yanked             INTEGER NOT NULL DEFAULT 0,
    scan_verdict       TEXT NOT NULL DEFAULT '',
    scan_findings_json TEXT NOT NULL DEFAULT '[]',
    min_bridge_version TEXT NOT NULL DEFAULT '',
    max_bridge_version TEXT NOT NULL DEFAULT '',
    python_requires    TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (module_id, version)
);

CREATE TABLE IF NOT EXISTS ratings (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    module_id    TEXT NOT NULL,
    publisher_id TEXT NOT NULL,
    stars        INTEGER NOT NULL CHECK(stars >= 1 AND stars <= 5),
    comment      TEXT NOT NULL DEFAULT '',
    created_at   REAL NOT NULL,
    UNIQUE(module_id, publisher_id)
);
"""

# Migrations for databases created with earlier schema versions.
_MIGRATIONS = [
    # Phase 4: publisher self-service fields
    "ALTER TABLE publishers ADD COLUMN email TEXT NOT NULL DEFAULT ''",
    "ALTER TABLE publishers ADD COLUMN description TEXT NOT NULL DEFAULT ''",
    "ALTER TABLE publishers ADD COLUMN website TEXT NOT NULL DEFAULT ''",
    "ALTER TABLE publishers ADD COLUMN verified INTEGER NOT NULL DEFAULT 0",
    # Phase 4: module ecosystem fields
    "ALTER TABLE modules ADD COLUMN average_rating REAL NOT NULL DEFAULT 0.0",
    "ALTER TABLE modules ADD COLUMN rating_count INTEGER NOT NULL DEFAULT 0",
    "ALTER TABLE modules ADD COLUMN category TEXT NOT NULL DEFAULT ''",
    "ALTER TABLE modules ADD COLUMN deprecated INTEGER NOT NULL DEFAULT 0",
    "ALTER TABLE modules ADD COLUMN deprecated_message TEXT NOT NULL DEFAULT ''",
    "ALTER TABLE modules ADD COLUMN replacement_module_id TEXT NOT NULL DEFAULT ''",
    # Phase 4: version extended fields
    "ALTER TABLE versions ADD COLUMN scan_verdict TEXT NOT NULL DEFAULT ''",
    "ALTER TABLE versions ADD COLUMN scan_findings_json TEXT NOT NULL DEFAULT '[]'",
    "ALTER TABLE versions ADD COLUMN min_bridge_version TEXT NOT NULL DEFAULT ''",
    "ALTER TABLE versions ADD COLUMN max_bridge_version TEXT NOT NULL DEFAULT ''",
    "ALTER TABLE versions ADD COLUMN python_requires TEXT NOT NULL DEFAULT ''",
]


class HubStore:
    """Async SQLite store for the hub server."""

    def __init__(self, db_path: str) -> None:
        self._db_path = db_path
        self._db: aiosqlite.Connection | None = None

    async def init(self) -> None:
        self._db = await aiosqlite.connect(self._db_path)
        self._db.row_factory = aiosqlite.Row
        await self._db.executescript(_SCHEMA)
        await self._db.commit()
        # Run migrations for pre-existing databases.
        for sql in _MIGRATIONS:
            try:
                await self._db.execute(sql)
                await self._db.commit()
            except Exception:
                pass  # Column already exists
        log.info("hub_store_initialized", db=self._db_path)

    async def close(self) -> None:
        if self._db:
            await self._db.close()
            self._db = None

    # ------------------------------------------------------------------
    # Publishers
    # ------------------------------------------------------------------

    async def create_publisher(
        self,
        publisher_id: str,
        name: str,
        key_hash: str,
        *,
        email: str = "",
        description: str = "",
        website: str = "",
    ) -> PublisherRecord:
        now = time.time()
        await self._db.execute(
            """INSERT INTO publishers
                   (publisher_id, name, api_key_hash, created_at, email, description, website)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (publisher_id, name, key_hash, now, email, description, website),
        )
        await self._db.commit()
        return PublisherRecord(
            publisher_id=publisher_id,
            name=name,
            api_key_hash=key_hash,
            created_at=now,
            email=email,
            description=description,
            website=website,
        )

    async def get_publisher_by_key_hash(self, key_hash: str) -> PublisherRecord | None:
        cursor = await self._db.execute(
            "SELECT * FROM publishers WHERE api_key_hash = ? AND enabled = 1", (key_hash,)
        )
        row = await cursor.fetchone()
        if row is None:
            return None
        return self._row_to_publisher(row)

    async def get_publisher(self, publisher_id: str) -> PublisherRecord | None:
        cursor = await self._db.execute(
            "SELECT * FROM publishers WHERE publisher_id = ?", (publisher_id,)
        )
        row = await cursor.fetchone()
        if row is None:
            return None
        return self._row_to_publisher(row)

    async def update_publisher(
        self,
        publisher_id: str,
        *,
        name: str | None = None,
        email: str | None = None,
        description: str | None = None,
        website: str | None = None,
    ) -> PublisherRecord | None:
        sets: list[str] = []
        vals: list = []
        if name is not None:
            sets.append("name = ?")
            vals.append(name)
        if email is not None:
            sets.append("email = ?")
            vals.append(email)
        if description is not None:
            sets.append("description = ?")
            vals.append(description)
        if website is not None:
            sets.append("website = ?")
            vals.append(website)
        if not sets:
            return await self.get_publisher(publisher_id)
        vals.append(publisher_id)
        await self._db.execute(
            f"UPDATE publishers SET {', '.join(sets)} WHERE publisher_id = ?", vals
        )
        await self._db.commit()
        return await self.get_publisher(publisher_id)

    async def rotate_api_key(self, publisher_id: str, new_key_hash: str) -> bool:
        cursor = await self._db.execute(
            "UPDATE publishers SET api_key_hash = ? WHERE publisher_id = ?",
            (new_key_hash, publisher_id),
        )
        await self._db.commit()
        return cursor.rowcount > 0

    async def list_publisher_modules(self, publisher_id: str) -> list[ModuleRecord]:
        cursor = await self._db.execute(
            "SELECT * FROM modules WHERE publisher_id = ? ORDER BY updated_at DESC",
            (publisher_id,),
        )
        rows = await cursor.fetchall()
        return [self._row_to_module(r) for r in rows]

    # ------------------------------------------------------------------
    # Modules
    # ------------------------------------------------------------------

    async def upsert_module(self, record: ModuleRecord) -> None:
        now = time.time()
        await self._db.execute(
            """INSERT INTO modules
                   (module_id, latest_version, description, author, license, tags_json,
                    downloads, publisher_id, created_at, updated_at, category)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(module_id) DO UPDATE SET
                   latest_version = excluded.latest_version,
                   description    = excluded.description,
                   author         = excluded.author,
                   license        = excluded.license,
                   tags_json      = excluded.tags_json,
                   publisher_id   = excluded.publisher_id,
                   category       = excluded.category,
                   updated_at     = ?""",
            (
                record.module_id,
                record.latest_version,
                record.description,
                record.author,
                record.license,
                json.dumps(record.tags),
                record.downloads,
                record.publisher_id,
                record.created_at or now,
                now,
                record.category,
                now,
            ),
        )
        await self._db.commit()

    async def get_module(self, module_id: str) -> ModuleRecord | None:
        cursor = await self._db.execute("SELECT * FROM modules WHERE module_id = ?", (module_id,))
        row = await cursor.fetchone()
        if row is None:
            return None
        return self._row_to_module(row)

    async def search_modules(
        self,
        query: str = "",
        *,
        limit: int = 20,
        tags: list[str] | None = None,
        category: str = "",
        min_rating: float = 0.0,
        sort_by: str = "downloads",
        include_deprecated: bool = False,
    ) -> list[ModuleRecord]:
        conditions: list[str] = []
        params: list = []

        if query:
            pattern = f"%{query}%"
            conditions.append("(module_id LIKE ? OR description LIKE ? OR tags_json LIKE ?)")
            params.extend([pattern, pattern, pattern])

        if category:
            conditions.append("category = ?")
            params.append(category)

        if min_rating > 0:
            conditions.append("average_rating >= ?")
            params.append(min_rating)

        if not include_deprecated:
            conditions.append("deprecated = 0")

        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""

        order_map = {
            "downloads": "downloads DESC",
            "rating": "average_rating DESC, rating_count DESC",
            "newest": "updated_at DESC",
        }
        order = order_map.get(sort_by, "downloads DESC")

        params.append(limit)
        cursor = await self._db.execute(
            f"SELECT * FROM modules {where} ORDER BY {order} LIMIT ?", params
        )
        rows = await cursor.fetchall()
        results = [self._row_to_module(r) for r in rows]

        if tags:
            tag_set = set(tags)
            results = [m for m in results if tag_set.intersection(m.tags)]

        return results

    async def increment_downloads(self, module_id: str) -> None:
        await self._db.execute(
            "UPDATE modules SET downloads = downloads + 1 WHERE module_id = ?", (module_id,)
        )
        await self._db.commit()

    async def delete_module(self, module_id: str) -> bool:
        cursor = await self._db.execute("DELETE FROM modules WHERE module_id = ?", (module_id,))
        await self._db.execute("DELETE FROM versions WHERE module_id = ?", (module_id,))
        await self._db.execute("DELETE FROM ratings WHERE module_id = ?", (module_id,))
        await self._db.commit()
        return cursor.rowcount > 0

    async def deprecate_module(
        self,
        module_id: str,
        message: str = "",
        replacement_id: str = "",
    ) -> bool:
        cursor = await self._db.execute(
            """UPDATE modules SET deprecated = 1, deprecated_message = ?,
                   replacement_module_id = ? WHERE module_id = ?""",
            (message, replacement_id, module_id),
        )
        await self._db.commit()
        return cursor.rowcount > 0

    # ------------------------------------------------------------------
    # Versions
    # ------------------------------------------------------------------

    async def add_version(self, record: VersionRecord) -> None:
        await self._db.execute(
            """INSERT INTO versions
                   (module_id, version, package_path, checksum, scan_score, published_at,
                    scan_verdict, scan_findings_json, min_bridge_version, max_bridge_version, python_requires)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                record.module_id, record.version, record.package_path,
                record.checksum, record.scan_score, record.published_at,
                record.scan_verdict, record.scan_findings_json,
                record.min_bridge_version, record.max_bridge_version, record.python_requires,
            ),
        )
        await self._db.commit()

    async def get_versions(self, module_id: str) -> list[VersionRecord]:
        cursor = await self._db.execute(
            "SELECT * FROM versions WHERE module_id = ? ORDER BY published_at DESC", (module_id,)
        )
        rows = await cursor.fetchall()
        return [self._row_to_version(r) for r in rows]

    async def get_latest_version(self, module_id: str) -> VersionRecord | None:
        cursor = await self._db.execute(
            "SELECT * FROM versions WHERE module_id = ? AND yanked = 0 ORDER BY published_at DESC LIMIT 1",
            (module_id,),
        )
        row = await cursor.fetchone()
        if row is None:
            return None
        return self._row_to_version(row)

    async def yank_version(self, module_id: str, version: str) -> bool:
        cursor = await self._db.execute(
            "UPDATE versions SET yanked = 1 WHERE module_id = ? AND version = ?",
            (module_id, version),
        )
        await self._db.commit()
        return cursor.rowcount > 0

    # ------------------------------------------------------------------
    # Ratings
    # ------------------------------------------------------------------

    async def add_rating(
        self,
        module_id: str,
        publisher_id: str,
        stars: int,
        comment: str = "",
    ) -> RatingRecord:
        now = time.time()
        await self._db.execute(
            """INSERT INTO ratings (module_id, publisher_id, stars, comment, created_at)
               VALUES (?, ?, ?, ?, ?)
               ON CONFLICT(module_id, publisher_id) DO UPDATE SET
                   stars = excluded.stars,
                   comment = excluded.comment,
                   created_at = excluded.created_at""",
            (module_id, publisher_id, stars, comment, now),
        )
        # Recalculate average.
        await self._db.execute(
            """UPDATE modules SET
                   average_rating = (SELECT COALESCE(AVG(stars), 0) FROM ratings WHERE module_id = ?),
                   rating_count = (SELECT COUNT(*) FROM ratings WHERE module_id = ?)
               WHERE module_id = ?""",
            (module_id, module_id, module_id),
        )
        await self._db.commit()
        cursor = await self._db.execute(
            "SELECT * FROM ratings WHERE module_id = ? AND publisher_id = ?",
            (module_id, publisher_id),
        )
        row = await cursor.fetchone()
        return self._row_to_rating(row)

    async def get_ratings(self, module_id: str, *, limit: int = 50) -> list[RatingRecord]:
        cursor = await self._db.execute(
            "SELECT * FROM ratings WHERE module_id = ? ORDER BY created_at DESC LIMIT ?",
            (module_id, limit),
        )
        rows = await cursor.fetchall()
        return [self._row_to_rating(r) for r in rows]

    # ------------------------------------------------------------------
    # Categories
    # ------------------------------------------------------------------

    async def get_categories(self) -> list[dict]:
        cursor = await self._db.execute(
            """SELECT category, COUNT(*) as count FROM modules
               WHERE category != '' AND deprecated = 0
               GROUP BY category ORDER BY count DESC"""
        )
        rows = await cursor.fetchall()
        return [{"name": r["category"], "count": r["count"]} for r in rows]

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _row_to_publisher(row) -> PublisherRecord:
        d = dict(row)
        return PublisherRecord(
            publisher_id=d["publisher_id"],
            name=d["name"],
            api_key_hash=d["api_key_hash"],
            created_at=d["created_at"],
            enabled=bool(d.get("enabled", 1)),
            email=d.get("email", ""),
            description=d.get("description", ""),
            website=d.get("website", ""),
            verified=bool(d.get("verified", 0)),
        )

    @staticmethod
    def _row_to_module(row) -> ModuleRecord:
        d = dict(row)
        tags = []
        try:
            tags = json.loads(d.get("tags_json", "[]"))
        except (json.JSONDecodeError, TypeError):
            pass
        return ModuleRecord(
            module_id=d["module_id"],
            latest_version=d["latest_version"],
            description=d.get("description", ""),
            author=d.get("author", ""),
            license=d.get("license", ""),
            tags=tags,
            downloads=d.get("downloads", 0),
            publisher_id=d.get("publisher_id", ""),
            created_at=d.get("created_at", 0.0),
            updated_at=d.get("updated_at", 0.0),
            average_rating=d.get("average_rating", 0.0),
            rating_count=d.get("rating_count", 0),
            category=d.get("category", ""),
            deprecated=bool(d.get("deprecated", 0)),
            deprecated_message=d.get("deprecated_message", ""),
            replacement_module_id=d.get("replacement_module_id", ""),
        )

    @staticmethod
    def _row_to_version(row) -> VersionRecord:
        d = dict(row)
        return VersionRecord(
            module_id=d["module_id"],
            version=d["version"],
            package_path=d["package_path"],
            checksum=d["checksum"],
            scan_score=d.get("scan_score", 0.0),
            published_at=d.get("published_at", 0.0),
            yanked=bool(d.get("yanked", 0)),
            scan_verdict=d.get("scan_verdict", ""),
            scan_findings_json=d.get("scan_findings_json", ""),
            min_bridge_version=d.get("min_bridge_version", ""),
            max_bridge_version=d.get("max_bridge_version", ""),
            python_requires=d.get("python_requires", ""),
        )

    @staticmethod
    def _row_to_rating(row) -> RatingRecord:
        d = dict(row)
        return RatingRecord(
            id=d["id"],
            module_id=d["module_id"],
            publisher_id=d["publisher_id"],
            stars=d["stars"],
            comment=d.get("comment", ""),
            created_at=d.get("created_at", 0.0),
        )
