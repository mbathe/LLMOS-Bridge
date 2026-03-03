"""CLI commands -- Module structure validation, signing, scanning, and packaging.

Usage:
    llmos-bridge module validate <path>    # Check structure + score
    llmos-bridge module scan <path>        # Security scan source code
    llmos-bridge module sign <path>        # Ed25519 sign
    llmos-bridge module package <path>     # Create distributable .tar.gz
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

import typer

app = typer.Typer(name="module", help="Module management, validation, and packaging.")


@app.command()
def validate(
    path: Path = typer.Argument(..., help="Path to the module directory to validate."),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Show detailed validation output."),
    include_scan: bool = typer.Option(False, "--scan", "-s", help="Include source code security scan."),
) -> None:
    """Validate a module directory structure for hub publishing readiness."""
    # Typer 0.12.x bug: bool options may arrive as str('False') or None.
    verbose = verbose is None or verbose is True
    include_scan = include_scan is None or include_scan is True

    from llmos_bridge.hub.validator import ModuleValidator

    if not path.exists():
        typer.echo(f"Error: Directory not found: {path}", err=True)
        raise typer.Exit(code=1)

    if not path.is_dir():
        typer.echo(f"Error: Not a directory: {path}", err=True)
        raise typer.Exit(code=1)

    validator = ModuleValidator()
    result = validator.validate(path)

    # Display results
    status_str = "PASS" if result.hub_ready else "FAIL"
    typer.echo(f"\nModule Validation: {status_str}")
    typer.echo(f"Score: {result.score}/100")
    typer.echo(f"Hub Ready: {'Yes' if result.hub_ready else 'No'}")

    if result.issues:
        typer.echo(f"\nIssues ({len(result.issues)}):")
        for issue in result.issues:
            typer.echo(f"  - {issue}")

    if result.warnings and verbose:
        typer.echo(f"\nWarnings ({len(result.warnings)}):")
        for warning in result.warnings:
            typer.echo(f"  - {warning}")
    elif result.warnings:
        typer.echo(f"\n{len(result.warnings)} warning(s) (use --verbose to see)")

    # Optional source code security scan
    if include_scan:
        import asyncio
        from llmos_bridge.hub.source_scanner import SourceCodeScanner

        scanner = SourceCodeScanner()
        scan_result = asyncio.run(scanner.scan_directory(path))

        typer.echo(f"\nSecurity Scan: {scan_result.verdict.value.upper()}")
        typer.echo(f"Scan Score: {scan_result.score:.0f}/100")
        typer.echo(f"Files Scanned: {scan_result.files_scanned}")
        typer.echo(f"Duration: {scan_result.scan_duration_ms:.1f}ms")

        if scan_result.findings:
            typer.echo(f"\nFindings ({len(scan_result.findings)}):")
            for f in scan_result.findings:
                severity_pct = f"{f.severity:.0%}"
                typer.echo(f"  [{severity_pct:>4}] {f.file_path}:{f.line_number} - {f.description}")

    if not result.hub_ready:
        raise typer.Exit(code=1)


@app.command(name="scan")
def scan_module(
    path: Path = typer.Argument(..., help="Path to the module directory to scan."),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Show all matched lines."),
    json_output: bool = typer.Option(False, "--json", help="Output as JSON."),
) -> None:
    """Run source code security scanner on a module directory (pre-install check)."""
    # Typer 0.12.x bug: bool options may arrive as str('False') or None.
    verbose = verbose is None or verbose is True
    json_output = json_output is None or json_output is True

    import asyncio

    if not path.exists():
        typer.echo(f"Error: Directory not found: {path}", err=True)
        raise typer.Exit(code=1)

    if not path.is_dir():
        typer.echo(f"Error: Not a directory: {path}", err=True)
        raise typer.Exit(code=1)

    from llmos_bridge.hub.source_scanner import SourceCodeScanner

    scanner = SourceCodeScanner()
    result = asyncio.run(scanner.scan_directory(path))

    if json_output:
        import json
        typer.echo(json.dumps(result.to_dict(), indent=2))
        return

    # Pretty-print results
    verdict_colors = {"allow": typer.colors.GREEN, "warn": typer.colors.YELLOW, "reject": typer.colors.RED}
    verdict_str = result.verdict.value.upper()
    color = verdict_colors.get(result.verdict.value, typer.colors.WHITE)

    typer.echo(f"\nSource Code Security Scan")
    typer.echo(f"{'=' * 40}")
    typer.secho(f"Verdict: {verdict_str}", fg=color, bold=True)
    typer.echo(f"Score: {result.score:.0f}/100")
    typer.echo(f"Files Scanned: {result.files_scanned}")
    typer.echo(f"Findings: {len(result.findings)}")
    typer.echo(f"Duration: {result.scan_duration_ms:.1f}ms")

    if result.findings:
        typer.echo(f"\n{'Severity':>8}  {'File':>30}  {'Line':>5}  Description")
        typer.echo("-" * 80)
        for f in result.findings:
            severity_pct = f"{f.severity:.0%}"
            file_display = f.file_path if len(f.file_path) <= 30 else f"...{f.file_path[-27:]}"
            typer.echo(f"{severity_pct:>8}  {file_display:>30}  {f.line_number:>5}  {f.description}")
            if verbose:
                typer.secho(f"{'':>8}  {'':>30}  {'':>5}  {f.line_content}", dim=True)

    typer.echo("")
    if result.verdict.value == "reject":
        raise typer.Exit(code=1)


@app.command()
def validate_all(
    path: Path = typer.Argument(..., help="Path to the modules parent directory."),
) -> None:
    """Validate all module directories under a parent path."""
    from llmos_bridge.hub.validator import ModuleValidator

    if not path.exists() or not path.is_dir():
        typer.echo(f"Error: Invalid directory: {path}", err=True)
        raise typer.Exit(code=1)

    validator = ModuleValidator()
    results = validator.validate_all(path)

    if not results:
        typer.echo("No modules found.")
        raise typer.Exit(code=1)

    typer.echo(f"\nValidated {len(results)} module(s):\n")
    typer.echo(f"{'Module':<25} {'Score':>5} {'Status':>8} {'Issues':>6} {'Warnings':>8}")
    typer.echo("-" * 60)

    all_ready = True
    for name, result in results.items():
        status = "PASS" if result.hub_ready else "FAIL"
        if not result.hub_ready:
            all_ready = False
        typer.echo(
            f"{name:<25} {result.score:>5} {status:>8} "
            f"{len(result.issues):>6} {len(result.warnings):>8}"
        )

    typer.echo("")
    if not all_ready:
        raise typer.Exit(code=1)


@app.command(name="sign")
def sign_module(
    path: Path = typer.Argument(..., help="Path to the module directory to sign."),
    key_path: Path = typer.Option(..., "--key", "-k", help="Path to Ed25519 private key."),
) -> None:
    """Sign a module package with an Ed25519 private key."""
    typer.echo(f"Signing module at {path} with key {key_path}")

    if not path.exists():
        typer.echo(f"Error: Directory not found: {path}", err=True)
        raise typer.Exit(code=1)

    toml_path = path / "llmos-module.toml"
    if not toml_path.exists():
        typer.echo("Error: No llmos-module.toml found. Run 'module validate' first.", err=True)
        raise typer.Exit(code=1)

    if not key_path.exists():
        typer.echo(f"Error: Key file not found: {key_path}", err=True)
        raise typer.Exit(code=1)

    try:
        from llmos_bridge.modules.signing import ModuleSigner

        private_bytes = ModuleSigner.load_private_key(key_path)
        signer = ModuleSigner(private_bytes)
        signature = signer.sign_module(path)
        typer.echo("Module signed successfully.")
        typer.echo(f"Signature: {signature.signature_hex[:16]}...")
    except ImportError:
        typer.echo("Error: Signing module not available. Install cryptography package.", err=True)
        raise typer.Exit(code=1)
    except Exception as e:
        typer.echo(f"Error signing module: {e}", err=True)
        raise typer.Exit(code=1)


@app.command(name="package")
def package_module(
    path: Path = typer.Argument(..., help="Path to the module directory to package."),
    output: Optional[Path] = typer.Option(None, "--output", "-o", help="Output .tar.gz path."),
) -> None:
    """Create a distributable .tar.gz package from a module directory."""
    import tarfile

    if not path.exists():
        typer.echo(f"Error: Directory not found: {path}", err=True)
        raise typer.Exit(code=1)

    # Validate first
    from llmos_bridge.hub.validator import ModuleValidator

    validator = ModuleValidator()
    result = validator.validate(path)

    if not result.passed:
        typer.echo("Error: Module has validation issues. Fix them before packaging:")
        for issue in result.issues:
            typer.echo(f"  - {issue}")
        raise typer.Exit(code=1)

    # Read module config for naming
    toml_path = path / "llmos-module.toml"
    if toml_path.exists():
        from llmos_bridge.hub.package import ModulePackageConfig

        config = ModulePackageConfig.from_toml(toml_path)
        name = f"{config.module_id}-{config.version}.tar.gz"
    else:
        name = f"{path.name}.tar.gz"

    if output is None:
        output = path.parent / name

    with tarfile.open(output, "w:gz") as tar:
        tar.add(path, arcname=path.name)

    typer.echo(f"Package created: {output}")
    typer.echo(f"Score: {result.score}/100")
