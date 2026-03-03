"""Per-module virtual environment manager.

Creates, validates, and caches isolated venvs for subprocess workers.
Prefers ``uv`` for ~10x faster venv creation; falls back to stdlib
``venv`` + ``pip`` when ``uv`` is not available.

Directory layout::

    ~/.llmos/venvs/
        vision_omniparser/
            .venv/              # virtual environment
            .requirements.hash  # SHA-256 of sorted requirements (cache key)
        vision_ultra/
            .venv/
            .requirements.hash
"""

from __future__ import annotations

import asyncio
import hashlib
import shutil
import sys
from pathlib import Path
from typing import Any

from llmos_bridge.exceptions import VenvCreationError
from llmos_bridge.logging import get_logger

log = get_logger(__name__)


class VenvManager:
    """Manage per-module virtual environments for isolated workers.

    Each module gets its own venv under ``base_dir / module_id / .venv/``.
    A ``.requirements.hash`` file stores the SHA-256 of the sorted
    requirements list for cache invalidation — if requirements change the
    venv is deleted and recreated.
    """

    def __init__(
        self,
        base_dir: Path | None = None,
        prefer_uv: bool = True,
    ) -> None:
        self._base_dir = (base_dir or Path("~/.llmos/venvs")).expanduser()
        self._prefer_uv = prefer_uv
        self._uv_available: bool | None = None

    @property
    def base_dir(self) -> Path:
        return self._base_dir

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def has_uv(self) -> bool:
        """Check if the ``uv`` CLI is available on PATH."""
        if self._uv_available is None:
            self._uv_available = shutil.which("uv") is not None
        return self._uv_available

    async def ensure_venv(
        self,
        module_id: str,
        requirements: list[str],
        python_version: str = "",
    ) -> Path:
        """Return the Python executable for *module_id*'s venv.

        If the venv already exists and requirements haven't changed, reuses
        it.  Otherwise creates a fresh venv and installs requirements.

        Args:
            python_version: Optional Python version string (e.g. ``"3.11"``,
                ``"3.12"``).  When provided, ``uv`` will download that version
                if it is not already installed.  When empty, the host Python
                interpreter is used (current behaviour).  The stdlib fallback
                (venv + pip) requires the requested Python to be available on
                PATH as ``python{python_version}`` (e.g. ``python3.11``).

        Returns:
            Path to the Python executable inside the venv.

        Raises:
            VenvCreationError: If venv creation or pip install fails.
        """
        venv_dir = self._module_dir(module_id) / ".venv"
        hash_file = self._module_dir(module_id) / ".requirements.hash"
        # Include python_version in the cache key so that changing the
        # requested interpreter version invalidates the existing venv.
        expected_hash = self._requirements_hash(requirements, python_version)

        # Check cache.
        if venv_dir.exists() and hash_file.exists():
            current_hash = hash_file.read_text().strip()
            if current_hash == expected_hash:
                python = self._venv_python(venv_dir)
                if python.exists():
                    log.debug("venv_cache_hit", module_id=module_id)
                    return python

        # Cache miss — (re)create.
        log.info(
            "venv_creating",
            module_id=module_id,
            requirements_count=len(requirements),
            use_uv=self._prefer_uv and self.has_uv(),
            python_version=python_version or "host",
        )

        # Clean up stale venv.
        if venv_dir.exists():
            shutil.rmtree(venv_dir)

        # Ensure parent directory exists.
        self._module_dir(module_id).mkdir(parents=True, exist_ok=True)

        try:
            if self._prefer_uv and self.has_uv():
                await self._create_with_uv(venv_dir, requirements, python_version)
            else:
                await self._create_with_stdlib(venv_dir, requirements, python_version)
        except Exception as exc:
            # Clean up partial venv on failure.
            if venv_dir.exists():
                shutil.rmtree(venv_dir, ignore_errors=True)
            raise VenvCreationError(module_id=module_id, reason=str(exc)) from exc

        # Write hash file.
        hash_file.write_text(expected_hash)

        python = self._venv_python(venv_dir)
        if not python.exists():
            raise VenvCreationError(
                module_id=module_id,
                reason=f"Python executable not found at {python}",
            )

        log.info("venv_created", module_id=module_id, python=str(python))
        return python

    async def remove_venv(self, module_id: str) -> None:
        """Remove a module's venv directory entirely."""
        module_dir = self._module_dir(module_id)
        if module_dir.exists():
            shutil.rmtree(module_dir)
            log.info("venv_removed", module_id=module_id)

    def list_venvs(self) -> list[str]:
        """List module IDs that have existing venvs."""
        if not self._base_dir.exists():
            return []
        return sorted(
            d.name
            for d in self._base_dir.iterdir()
            if d.is_dir() and (d / ".venv").exists()
        )

    def venv_exists(self, module_id: str) -> bool:
        """Check if a venv exists for the given module."""
        return (self._module_dir(module_id) / ".venv").exists()

    def get_python(self, module_id: str) -> Path | None:
        """Return the Python executable path if the venv exists."""
        venv_dir = self._module_dir(module_id) / ".venv"
        if not venv_dir.exists():
            return None
        python = self._venv_python(venv_dir)
        return python if python.exists() else None

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _module_dir(self, module_id: str) -> Path:
        return self._base_dir / module_id

    def _venv_python(self, venv_dir: Path) -> Path:
        """Return the Python executable inside a venv (platform-aware)."""
        if sys.platform == "win32":
            return venv_dir / "Scripts" / "python.exe"
        return venv_dir / "bin" / "python"

    def _requirements_hash(self, requirements: list[str], python_version: str = "") -> str:
        """Deterministic SHA-256 of sorted requirements + Python version."""
        content = "\n".join(sorted(requirements))
        if python_version:
            content = f"python={python_version}\n{content}"
        return hashlib.sha256(content.encode()).hexdigest()

    def _python_spec(self, python_version: str) -> str:
        """Return the Python spec string for venv creation tools.

        - Empty string  → ``sys.executable`` (exact host interpreter path,
          avoids version mismatch when the daemon runs under conda/pyenv
          but ``uv`` would pick a different system Python)
        - ``"3.11"``    → ``"3.11"`` (passed directly to uv / used for lookup)
        - ``"3"``       → ``"3"`` (major only — uv resolves to latest 3.x)
        """
        if python_version:
            return python_version
        return sys.executable

    async def _create_with_uv(
        self, venv_dir: Path, requirements: list[str], python_version: str = ""
    ) -> None:
        """Create venv and install deps using ``uv``.

        When *python_version* is specified, ``uv`` will download that Python
        version automatically if it is not already installed on the system.
        This is the recommended path for modules that need a specific Python.
        """
        python_spec = self._python_spec(python_version)
        # Create venv with the requested Python version.
        await self._run_subprocess(
            ["uv", "venv", str(venv_dir), "--python", python_spec],
            error_msg=f"uv venv creation failed (python={python_spec})",
        )

        # Install requirements.
        if requirements:
            python = self._venv_python(venv_dir)
            await self._run_subprocess(
                ["uv", "pip", "install", "--python", str(python)] + requirements,
                error_msg="uv pip install failed",
            )

    async def _create_with_stdlib(
        self, venv_dir: Path, requirements: list[str], python_version: str = ""
    ) -> None:
        """Create venv using stdlib ``venv`` + ``pip install``.

        When *python_version* is specified, the interpreter is resolved via
        ``shutil.which`` (e.g. ``python3.11``).  If the requested version is
        not found on PATH, raises ``RuntimeError`` with a clear message.
        Unlike uv, stdlib venv cannot auto-download Python versions.
        """
        if python_version:
            # Try to locate the requested interpreter.
            py_exe = shutil.which(f"python{python_version}") or shutil.which(
                f"python{python_version.split('.')[0]}"
            )
            if py_exe is None:
                raise RuntimeError(
                    f"Python {python_version} not found on PATH. "
                    f"Install it or use 'uv' (which auto-downloads Python versions)."
                )
            interpreter = py_exe
        else:
            interpreter = sys.executable

        # Create venv with the selected interpreter.
        await self._run_subprocess(
            [interpreter, "-m", "venv", str(venv_dir)],
            error_msg=f"venv creation failed (interpreter={interpreter})",
        )

        # Upgrade pip.
        python = self._venv_python(venv_dir)
        await self._run_subprocess(
            [str(python), "-m", "pip", "install", "--upgrade", "pip"],
            error_msg="pip upgrade failed",
        )

        # Install requirements.
        if requirements:
            await self._run_subprocess(
                [str(python), "-m", "pip", "install"] + requirements,
                error_msg="pip install failed",
            )

    async def _run_subprocess(
        self,
        cmd: list[str],
        error_msg: str,
        timeout: float = 300.0,
    ) -> str:
        """Run a subprocess command and return stdout.  Raises on failure."""
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(), timeout=timeout
            )
        except asyncio.TimeoutError:
            proc.kill()
            raise RuntimeError(f"{error_msg}: timed out after {timeout}s")

        if proc.returncode != 0:
            stderr_text = stderr.decode(errors="replace").strip()
            raise RuntimeError(f"{error_msg} (exit {proc.returncode}): {stderr_text[:500]}")

        return stdout.decode(errors="replace")
