"""Module installer — download, verify, create venv, register, load.

The installer is the orchestrator for community module installation:
  1. Load package from local path (or download from hub)
  2. Validate module structure (ModuleValidator — blocks on issues)
  3. Verify signature against trust store (hub installs only)
  4. Resolve module-to-module dependencies (DependencyResolver)
  5. Create isolated venv via VenvManager (eager — fail fast on bad deps)
  6. Add to ModuleIndex (SQLite)
  7. Register in ModuleRegistry with source_path for PYTHONPATH injection
  8. Call on_install() lifecycle hook
"""

from __future__ import annotations

import asyncio
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, TYPE_CHECKING

from llmos_bridge.logging import get_logger

if TYPE_CHECKING:
    from llmos_bridge.hub.index import InstalledModule, ModuleIndex
    from llmos_bridge.hub.package import ModulePackage, ModulePackageConfig
    from llmos_bridge.hub.resolver import DependencyResolver
    from llmos_bridge.hub.source_scanner import SourceCodeScanner
    from llmos_bridge.isolation.venv_manager import VenvManager
    from llmos_bridge.modules.lifecycle import ModuleLifecycleManager
    from llmos_bridge.modules.registry import ModuleRegistry
    from llmos_bridge.modules.signing import ModuleSigner, SignatureVerifier

log = get_logger(__name__)


@dataclass
class InstallResult:
    """Result of a module installation."""

    success: bool
    module_id: str
    version: str = ""
    error: str = ""
    installed_deps: list[str] = field(default_factory=list)
    validation_warnings: list[str] = field(default_factory=list)
    # Security scan results (Phase 1)
    scan_score: float = -1.0
    trust_tier: str = ""
    scan_findings_count: int = 0


class ModuleInstaller:
    """Installs, upgrades, and uninstalls community modules."""

    def __init__(
        self,
        index: ModuleIndex,
        registry: ModuleRegistry,
        venv_manager: VenvManager,
        verifier: SignatureVerifier | None = None,
        require_signatures: bool = True,
        install_dir: Path | None = None,
        lifecycle_manager: ModuleLifecycleManager | None = None,
        source_scanner: SourceCodeScanner | None = None,
        source_scan_enabled: bool = True,
    ) -> None:
        self._index = index
        self._registry = registry
        self._venv_manager = venv_manager
        self._verifier = verifier
        self._require_signatures = require_signatures
        self._install_dir = (install_dir or Path("~/.llmos/modules")).expanduser()
        self._lifecycle = lifecycle_manager
        self._source_scanner = source_scanner
        self._source_scan_enabled = source_scan_enabled

    def set_lifecycle_manager(self, manager: ModuleLifecycleManager) -> None:
        """Inject a ModuleLifecycleManager for on_install() / on_update() hooks."""
        self._lifecycle = manager

    async def install_from_path(self, package_path: Path) -> InstallResult:
        """Install a module from a local directory.

        Installation steps (in order):
          1. Parse llmos-module.toml
          2. Validate module structure (block on issues, warn on warnings)
          3. Resolve module-to-module dependencies
          4. Check if already installed
          5. Create isolated venv + install Python deps (eager)
          6. Register in ModuleIndex (SQLite)
          7. Register in ModuleRegistry (IsolatedModuleProxy + PYTHONPATH)
          8. Call on_install() lifecycle hook
        """
        from llmos_bridge.hub.index import InstalledModule
        from llmos_bridge.hub.package import ModulePackage
        from llmos_bridge.hub.validator import ModuleValidator

        # --- Step 1: Parse package ---
        try:
            package = ModulePackage.from_directory(package_path)
        except Exception as e:
            return InstallResult(
                success=False,
                module_id="unknown",
                error=f"Invalid package: {e}",
            )

        config = package.config
        module_id = config.module_id

        # --- Step 2: Validate module structure ---
        validation = ModuleValidator().validate(package_path)
        if not validation.passed:
            return InstallResult(
                success=False,
                module_id=module_id,
                version=config.version,
                error=(
                    "Module validation failed. Fix these issues before installing:\n"
                    + "\n".join(f"  • {issue}" for issue in validation.issues)
                ),
                validation_warnings=validation.warnings,
            )
        if validation.warnings:
            for w in validation.warnings:
                log.warning("module_validation_warning", module_id=module_id, warning=w)

        # --- Step 2.5: Source code security scan ---
        scan_result = None
        if self._source_scan_enabled:
            scanner = self._source_scanner
            if scanner is None:
                from llmos_bridge.hub.source_scanner import SourceCodeScanner
                scanner = SourceCodeScanner()
            scan_result = await scanner.scan_directory(package_path)
            log.info(
                "source_scan_complete",
                module_id=module_id,
                score=scan_result.score,
                verdict=scan_result.verdict.value,
                findings=len(scan_result.findings),
            )
            from llmos_bridge.security.scanners.base import ScanVerdict
            if scan_result.verdict == ScanVerdict.REJECT:
                findings_summary = "\n".join(
                    f"  [{f.severity:.0%}] {f.file_path}:{f.line_number} — {f.description}"
                    for f in scan_result.findings[:10]
                )
                return InstallResult(
                    success=False,
                    module_id=module_id,
                    version=config.version,
                    error=(
                        f"Source code security scan REJECTED (score: {scan_result.score:.0f}/100). "
                        f"{len(scan_result.findings)} finding(s):\n{findings_summary}"
                    ),
                    scan_score=scan_result.score,
                    scan_findings_count=len(scan_result.findings),
                )
            if scan_result.verdict == ScanVerdict.WARN:
                for finding in scan_result.findings:
                    log.warning(
                        "source_scan_warning",
                        module_id=module_id,
                        file=finding.file_path,
                        line=finding.line_number,
                        rule=finding.rule_id,
                        description=finding.description,
                    )

        # --- Step 3: Resolve module-to-module dependencies ---
        if config.module_dependencies:
            dep_errors = self._check_module_dependencies(module_id, config)
            if dep_errors:
                return InstallResult(
                    success=False,
                    module_id=module_id,
                    version=config.version,
                    error=(
                        "Module dependency requirements not met:\n"
                        + "\n".join(f"  • {e}" for e in dep_errors)
                    ),
                )

        # --- Step 4: Check if already installed ---
        existing = await self._index.get(module_id)
        if existing is not None:
            return InstallResult(
                success=False,
                module_id=module_id,
                version=config.version,
                error=(
                    f"Module '{module_id}' is already installed (v{existing.version}). "
                    "Use upgrade instead."
                ),
            )

        # --- Step 4.5: Copy source to stable install location ---
        # Like pip copying into site-packages, the module source is stored under
        # ~/.llmos/modules/{module_id}/ so deleting the original folder never
        # breaks the installed module.
        stable_path = self._install_dir / module_id
        if package_path.resolve() != stable_path.resolve():
            if stable_path.exists():
                shutil.rmtree(stable_path)
            try:
                await asyncio.to_thread(shutil.copytree, str(package_path), str(stable_path))
                install_path = stable_path
                log.info(
                    "module_source_copied",
                    module_id=module_id,
                    src=str(package_path),
                    dest=str(stable_path),
                )
            except Exception as e:
                return InstallResult(
                    success=False,
                    module_id=module_id,
                    version=config.version,
                    error=f"Failed to copy module source to install dir: {e}",
                )
        else:
            # Already in the install dir (hub download) — no copy needed.
            install_path = package_path

        # --- Step 5: Create isolated venv + install Python deps (eager) ---
        python_version = getattr(config, "python_version", "") or ""
        if config.requirements or python_version:
            try:
                await self._venv_manager.ensure_venv(
                    module_id, config.requirements, python_version=python_version
                )
                log.info(
                    "module_venv_created",
                    module_id=module_id,
                    requirements=config.requirements,
                    python_version=python_version or "host",
                )
            except Exception as e:
                # Clean up the stable copy we just created.
                if install_path != package_path and stable_path.exists():
                    shutil.rmtree(stable_path, ignore_errors=True)
                return InstallResult(
                    success=False,
                    module_id=module_id,
                    version=config.version,
                    error=f"Failed to install Python dependencies: {e}",
                )
        else:
            log.debug("module_no_requirements", module_id=module_id)

        # --- Step 6: Register in the index ---
        installed = InstalledModule(
            module_id=module_id,
            version=config.version,
            install_path=str(install_path),
            module_class_path=config.module_class_path,
            requirements=config.requirements,
            installed_at=time.time(),
            updated_at=time.time(),
            enabled=True,
            sandbox_level=config.sandbox_level,
            python_version=python_version,
        )
        await self._index.add(installed)

        # --- Step 6b: Compute and store security metadata ---
        scan_score = scan_result.score if scan_result else -1.0
        scan_findings_count = len(scan_result.findings) if scan_result else 0
        scan_json = ""
        if scan_result:
            import json as _json
            scan_json = _json.dumps(scan_result.to_dict())

        # Compute trust tier from scan results.
        from llmos_bridge.hub.trust import TrustPolicy
        trust_tier = TrustPolicy.compute_tier(
            scan_score=scan_score,
            signature_verified=False,
            module_id=module_id,
        )

        # Compute source checksum.
        from llmos_bridge.modules.signing import ModuleSigner
        checksum = ModuleSigner.compute_module_hash(install_path)

        await self._index.update_security_data(
            module_id,
            trust_tier=trust_tier.value,
            scan_score=scan_score,
            scan_result_json=scan_json,
            signature_status="unsigned",
            checksum=checksum,
        )

        # --- Step 7: Register in the runtime registry ---
        try:
            self._registry.register_isolated(
                module_id=module_id,
                module_class_path=config.module_class_path,
                venv_manager=self._venv_manager,
                requirements=config.requirements,
                source_path=install_path,  # injects PYTHONPATH from stable location
            )
        except Exception as e:
            # Rollback: remove index entry and stable copy.
            await self._index.remove(module_id)
            if install_path != package_path and stable_path.exists():
                shutil.rmtree(stable_path, ignore_errors=True)
            return InstallResult(
                success=False,
                module_id=module_id,
                version=config.version,
                error=f"Registration failed: {e}",
            )

        # --- Step 7b: Start the module so its manifest is available ---
        try:
            proxy = self._registry.get(module_id)
            await proxy.start()
            log.info("module_started_after_install", module_id=module_id)
        except Exception as e:
            log.warning("module_start_after_install_failed", module_id=module_id, error=str(e))

        # --- Step 8: Lifecycle hook ---
        if self._lifecycle is not None:
            try:
                await self._lifecycle.install_module(module_id)
            except Exception as e:
                log.warning("lifecycle_install_hook_failed", module_id=module_id, error=str(e))

        log.info(
            "module_installed",
            module_id=module_id,
            version=config.version,
            path=str(package_path),
            hub_ready=validation.hub_ready,
            validation_score=validation.score,
        )

        return InstallResult(
            success=True,
            module_id=module_id,
            version=config.version,
            installed_deps=config.requirements,
            validation_warnings=validation.warnings,
            scan_score=scan_score,
            trust_tier=trust_tier.value,
            scan_findings_count=scan_findings_count,
        )

    def _check_module_dependencies(
        self, module_id: str, config: Any
    ) -> list[str]:
        """Check that module-to-module dependencies are satisfied by the registry."""
        errors: list[str] = []
        for dep_id, version_spec in config.module_dependencies.items():
            if not self._registry.is_available(dep_id):
                errors.append(
                    f"Required module '{dep_id}' ({version_spec}) is not installed or available."
                )
        return errors

    async def install_from_hub(
        self,
        module_id: str,
        version: str = "latest",
        hub_client: Any = None,
    ) -> InstallResult:
        """Download and install a module from the hub.

        Requires a HubClient to download the package. If the hub is not
        yet implemented, returns an error indicating the hub is unavailable.
        """
        if hub_client is None:
            return InstallResult(
                success=False,
                module_id=module_id,
                error="Hub client not configured. Use install_from_path for local installs.",
            )

        try:
            dest = self._install_dir / module_id
            dest.mkdir(parents=True, exist_ok=True)
            await hub_client.download_package(module_id, version, dest)
            return await self.install_from_path(dest)
        except Exception as e:
            return InstallResult(
                success=False,
                module_id=module_id,
                error=f"Hub download failed: {e}",
            )

    async def uninstall(self, module_id: str) -> InstallResult:
        """Uninstall a module: stop worker, unregister, remove venv, remove from index."""
        existing = await self._index.get(module_id)
        if existing is None:
            return InstallResult(
                success=False,
                module_id=module_id,
                error=f"Module '{module_id}' is not installed.",
            )

        # Stop isolated worker subprocess before unregistering.
        from llmos_bridge.isolation.proxy import IsolatedModuleProxy

        instance = self._registry._instances.get(module_id)
        if isinstance(instance, IsolatedModuleProxy) and instance.is_alive:
            try:
                await instance.stop()
            except Exception as exc:
                log.warning("uninstall_stop_worker_failed", module_id=module_id, error=str(exc))

        # Call lifecycle uninstall hook.
        if self._lifecycle is not None:
            try:
                await self._lifecycle.uninstall_module(module_id)
            except Exception as exc:
                log.warning("lifecycle_uninstall_hook_failed", module_id=module_id, error=str(exc))

        # Unregister from runtime registry.
        try:
            self._registry.unregister(module_id)
        except Exception:
            pass  # Module may not be in the registry (e.g., failed to load).

        # Remove venv from disk.
        try:
            await self._venv_manager.remove_venv(module_id)
        except Exception as exc:
            log.warning("uninstall_venv_cleanup_failed", module_id=module_id, error=str(exc))

        # Remove stable install copy (like pip uninstall removing from site-packages).
        stable_path = self._install_dir / module_id
        if stable_path.exists():
            try:
                await asyncio.to_thread(shutil.rmtree, str(stable_path))
            except Exception as exc:
                log.warning("uninstall_source_cleanup_failed", module_id=module_id, error=str(exc))

        # Remove from index.
        await self._index.remove(module_id)

        log.info("module_uninstalled", module_id=module_id)

        return InstallResult(
            success=True,
            module_id=module_id,
            version=existing.version,
        )

    async def upgrade(
        self,
        module_id: str,
        new_package_path: Path,
    ) -> InstallResult:
        """Upgrade a module to a new version from a local path."""
        from llmos_bridge.hub.package import ModulePackage
        from llmos_bridge.hub.validator import ModuleValidator

        existing = await self._index.get(module_id)
        if existing is None:
            return InstallResult(
                success=False,
                module_id=module_id,
                error=f"Module '{module_id}' is not installed. Use install instead.",
            )

        try:
            package = ModulePackage.from_directory(new_package_path)
        except Exception as e:
            return InstallResult(
                success=False,
                module_id=module_id,
                error=f"Invalid package: {e}",
            )

        config = package.config

        # Validate new version.
        validation = ModuleValidator().validate(new_package_path)
        if not validation.passed:
            return InstallResult(
                success=False,
                module_id=module_id,
                version=config.version,
                error=(
                    "New version validation failed:\n"
                    + "\n".join(f"  • {issue}" for issue in validation.issues)
                ),
                validation_warnings=validation.warnings,
            )

        old_version = existing.version

        # --- Source code security scan (upgrade) ---
        scan_result = None
        if self._source_scan_enabled:
            scanner = self._source_scanner
            if scanner is None:
                from llmos_bridge.hub.source_scanner import SourceCodeScanner
                scanner = SourceCodeScanner()
            scan_result = await scanner.scan_directory(new_package_path)
            log.info(
                "upgrade_source_scan_complete",
                module_id=module_id,
                score=scan_result.score,
                verdict=scan_result.verdict.value,
            )
            from llmos_bridge.security.scanners.base import ScanVerdict
            if scan_result.verdict == ScanVerdict.REJECT:
                findings_summary = "\n".join(
                    f"  [{f.severity:.0%}] {f.file_path}:{f.line_number} — {f.description}"
                    for f in scan_result.findings[:10]
                )
                return InstallResult(
                    success=False,
                    module_id=module_id,
                    version=config.version,
                    error=(
                        f"Upgrade source scan REJECTED (score: {scan_result.score:.0f}/100). "
                        f"{len(scan_result.findings)} finding(s):\n{findings_summary}"
                    ),
                    scan_score=scan_result.score,
                    scan_findings_count=len(scan_result.findings),
                )

        # Copy new package to stable install location (overwriting old version).
        stable_path = self._install_dir / module_id
        if new_package_path.resolve() != stable_path.resolve():
            if stable_path.exists():
                shutil.rmtree(stable_path)
            try:
                await asyncio.to_thread(shutil.copytree, str(new_package_path), str(stable_path))
                upgrade_path = stable_path
                log.info(
                    "module_source_upgraded",
                    module_id=module_id,
                    dest=str(stable_path),
                )
            except Exception as e:
                return InstallResult(
                    success=False,
                    module_id=module_id,
                    version=config.version,
                    error=f"Failed to copy new module source: {e}",
                )
        else:
            upgrade_path = new_package_path

        # Re-create venv if requirements or python_version changed.
        python_version = getattr(config, "python_version", "") or ""
        if config.requirements or python_version:
            try:
                await self._venv_manager.ensure_venv(
                    module_id, config.requirements, python_version=python_version
                )
            except Exception as e:
                return InstallResult(
                    success=False,
                    module_id=module_id,
                    version=config.version,
                    error=f"Failed to update Python dependencies: {e}",
                )

        # Update the index with the stable path.
        await self._index.update_version(
            module_id, config.version, str(upgrade_path)
        )

        # Update security metadata for the new version.
        if scan_result is not None:
            import json as _json
            from llmos_bridge.hub.trust import TrustPolicy
            from llmos_bridge.modules.signing import ModuleSigner

            trust_tier = TrustPolicy.compute_tier(
                scan_score=scan_result.score,
                signature_verified=False,
                module_id=module_id,
            )
            checksum = ModuleSigner.compute_module_hash(upgrade_path)
            await self._index.update_security_data(
                module_id,
                trust_tier=trust_tier.value,
                scan_score=scan_result.score,
                scan_result_json=_json.dumps(scan_result.to_dict()),
                signature_status="unsigned",
                checksum=checksum,
            )

        # Stop old isolated worker subprocess before re-registering.
        from llmos_bridge.isolation.proxy import IsolatedModuleProxy

        old_instance = self._registry._instances.get(module_id)
        if isinstance(old_instance, IsolatedModuleProxy) and old_instance.is_alive:
            try:
                await old_instance.stop()
            except Exception as exc:
                log.warning("upgrade_stop_worker_failed", module_id=module_id, error=str(exc))

        # Re-register in the runtime registry.
        try:
            self._registry.unregister(module_id)
        except Exception:
            pass
        try:
            self._registry.register_isolated(
                module_id=module_id,
                module_class_path=config.module_class_path,
                venv_manager=self._venv_manager,
                requirements=config.requirements,
                source_path=upgrade_path,
            )
        except Exception as e:
            return InstallResult(
                success=False,
                module_id=module_id,
                version=config.version,
                error=f"Re-registration failed: {e}",
            )

        # Lifecycle hook.
        if self._lifecycle is not None:
            try:
                await self._lifecycle.upgrade_module(module_id, old_version)
            except Exception as e:
                log.warning("lifecycle_upgrade_hook_failed", module_id=module_id, error=str(e))

        log.info(
            "module_upgraded",
            module_id=module_id,
            old_version=old_version,
            new_version=config.version,
        )

        return InstallResult(
            success=True,
            module_id=module_id,
            version=config.version,
            validation_warnings=validation.warnings,
            scan_score=scan_result.score if scan_result else -1.0,
            trust_tier=trust_tier.value if scan_result else "",
            scan_findings_count=len(scan_result.findings) if scan_result else 0,
        )

    async def verify_module(self, module_id: str) -> dict[str, Any]:
        """Verify an installed module's integrity (signature + hash)."""
        existing = await self._index.get(module_id)
        if existing is None:
            return {"verified": False, "error": f"Module '{module_id}' not installed"}

        if self._verifier is None:
            return {"verified": False, "error": "No signature verifier configured"}

        from llmos_bridge.modules.signing import ModuleSigner

        install_path = Path(existing.install_path)
        if not install_path.exists():
            return {"verified": False, "error": f"Install path missing: {install_path}"}

        content_hash = ModuleSigner.compute_module_hash(install_path)
        return {
            "verified": True,
            "module_id": module_id,
            "content_hash": content_hash,
            "install_path": str(install_path),
        }
