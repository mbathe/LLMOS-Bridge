"""File memory backend — Markdown-based human-readable memory.

Stores memories as structured sections in a Markdown file.
Suitable for: project memory, shared knowledge, human-editable notes.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from llmos_bridge.modules.memory.backends.base import BaseMemoryBackend, MemoryEntry


class FileMemoryBackend(BaseMemoryBackend):
    """Markdown file-based memory backend.

    Each key becomes a ## section in the file. Values are the section content.
    Human-readable and editable. Git-friendly.
    """

    BACKEND_ID = "file"
    DESCRIPTION = "Markdown file — human-readable, git-friendly project memory"

    def __init__(self, file_path: Path | None = None, max_lines: int = 500):
        self._path = file_path or Path(".llmos/MEMORY.md")
        self._max_lines = max_lines

    async def init(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        if not self._path.exists():
            self._path.write_text("# Memory\n\n", encoding="utf-8")

    async def close(self) -> None:
        pass

    def _parse_sections(self) -> dict[str, str]:
        """Parse the markdown file into key -> content sections."""
        if not self._path.exists():
            return {}
        text = self._path.read_text(encoding="utf-8")
        sections: dict[str, str] = {}
        current_key: str | None = None
        current_lines: list[str] = []

        for line in text.split("\n"):
            match = re.match(r"^## (.+)$", line)
            if match:
                if current_key is not None:
                    sections[current_key] = "\n".join(current_lines).strip()
                current_key = match.group(1).strip()
                current_lines = []
            elif current_key is not None:
                current_lines.append(line)

        if current_key is not None:
            sections[current_key] = "\n".join(current_lines).strip()

        return sections

    def _write_sections(self, sections: dict[str, str]) -> None:
        """Write sections back to the markdown file."""
        parts = ["# Memory\n"]
        for key, content in sections.items():
            parts.append(f"\n## {key}\n{content}\n")

        text = "\n".join(parts)
        lines = text.split("\n")
        if len(lines) > self._max_lines:
            lines = lines[:self._max_lines]
            text = "\n".join(lines)

        self._path.write_text(text, encoding="utf-8")

    async def store(self, key: str, value: Any, *, metadata: dict[str, Any] | None = None, ttl_seconds: float | None = None) -> MemoryEntry:
        sections = self._parse_sections()
        sections[key] = str(value)
        self._write_sections(sections)
        return MemoryEntry(key=key, value=str(value), metadata=metadata or {}, backend=self.BACKEND_ID)

    async def recall(self, key: str) -> MemoryEntry | None:
        sections = self._parse_sections()
        if key not in sections:
            return None
        return MemoryEntry(key=key, value=sections[key], metadata={}, backend=self.BACKEND_ID)

    async def delete(self, key: str) -> bool:
        sections = self._parse_sections()
        if key not in sections:
            return False
        del sections[key]
        self._write_sections(sections)
        return True

    async def list_keys(self, *, prefix: str | None = None, limit: int = 100) -> list[str]:
        sections = self._parse_sections()
        keys = list(sections.keys())
        if prefix:
            keys = [k for k in keys if k.startswith(prefix)]
        return keys[:limit]

    async def search(self, query: str, *, top_k: int = 5, filters: dict[str, Any] | None = None) -> list[MemoryEntry]:
        """Simple substring search across all sections."""
        sections = self._parse_sections()
        query_lower = query.lower()
        results = []
        for key, content in sections.items():
            text = f"{key} {content}".lower()
            if query_lower in text:
                results.append(MemoryEntry(key=key, value=content, metadata={}, score=1.0, backend=self.BACKEND_ID))
        return results[:top_k]

    async def read_all(self) -> str:
        """Read the entire file content (for prompt injection)."""
        if not self._path.exists():
            return ""
        text = self._path.read_text(encoding="utf-8")
        lines = text.split("\n")
        if len(lines) > self._max_lines:
            lines = lines[:self._max_lines]
        return "\n".join(lines)
