"""Source code security scanner for community modules.

Scans Python source files in a module directory **before** installation to
detect dangerous patterns: ``eval()``, ``subprocess.call(shell=True)``,
obfuscated code, credential harvesting, and more.

This is distinct from :class:`HeuristicScanner` which scans IML plan text
at runtime.  ``SourceCodeScanner`` analyses static ``.py`` files and returns
structured findings with file paths, line numbers, and severity scores.

Usage::

    scanner = SourceCodeScanner()
    result = await scanner.scan_directory(Path("/path/to/module"))

    if result.verdict == ScanVerdict.REJECT:
        print(f"Module rejected (score {result.score}/100)")
        for f in result.findings:
            print(f"  {f.file_path}:{f.line_number} [{f.category}] {f.description}")
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from pathlib import Path

from llmos_bridge.security.scanners.base import ScanVerdict

_I = re.IGNORECASE


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------


@dataclass
class SourceCodeRule:
    """A single detection rule for the source code scanner.

    Attributes:
        id:          Unique identifier (e.g. ``"sc_eval"``).
        category:    Threat category (e.g. ``"dangerous_builtins"``).
        pattern:     Compiled regex pattern applied to each line.
        severity:    Risk weight (0.0-1.0).  Higher = more dangerous.
        description: Human-readable description shown to the user.
        enabled:     Whether this rule is active.
    """

    id: str
    category: str
    pattern: re.Pattern[str]
    severity: float = 0.5
    description: str = ""
    enabled: bool = True


@dataclass
class SourceScanFinding:
    """A single finding from the source code scanner."""

    rule_id: str
    category: str
    severity: float
    file_path: str
    line_number: int
    line_content: str
    description: str

    def to_dict(self) -> dict:
        return {
            "rule_id": self.rule_id,
            "category": self.category,
            "severity": self.severity,
            "file_path": self.file_path,
            "line_number": self.line_number,
            "line_content": self.line_content[:200],
            "description": self.description,
        }


@dataclass
class SourceScanResult:
    """Aggregated result from scanning a module directory."""

    verdict: ScanVerdict
    score: float
    findings: list[SourceScanFinding] = field(default_factory=list)
    files_scanned: int = 0
    scan_duration_ms: float = 0.0

    def to_dict(self) -> dict:
        return {
            "verdict": self.verdict.value,
            "score": round(self.score, 1),
            "findings": [f.to_dict() for f in self.findings],
            "files_scanned": self.files_scanned,
            "scan_duration_ms": round(self.scan_duration_ms, 2),
        }


# ---------------------------------------------------------------------------
# Built-in rules (~30 rules across 8 categories)
# ---------------------------------------------------------------------------


def _build_source_code_rules() -> list[SourceCodeRule]:
    """Build the default source code scanning ruleset."""
    rules: list[SourceCodeRule] = []

    # --- 1. Dangerous builtins (6) ---
    rules.extend([
        SourceCodeRule(
            id="sc_eval",
            category="dangerous_builtins",
            pattern=re.compile(r"\beval\s*\("),
            severity=0.8,
            description="Use of eval() — arbitrary code execution",
        ),
        SourceCodeRule(
            id="sc_exec",
            category="dangerous_builtins",
            pattern=re.compile(r"\bexec\s*\("),
            severity=0.8,
            description="Use of exec() — arbitrary code execution",
        ),
        SourceCodeRule(
            id="sc_compile",
            category="dangerous_builtins",
            pattern=re.compile(r"\bcompile\s*\("),
            severity=0.6,
            description="Use of compile() — dynamic code compilation",
        ),
        SourceCodeRule(
            id="sc_dunder_import",
            category="dangerous_builtins",
            pattern=re.compile(r"\b__import__\s*\("),
            severity=0.7,
            description="Use of __import__() — dynamic import",
        ),
        SourceCodeRule(
            id="sc_globals_access",
            category="dangerous_builtins",
            pattern=re.compile(r"\bglobals\s*\(\s*\)"),
            severity=0.5,
            description="Use of globals() — global namespace access",
        ),
        SourceCodeRule(
            id="sc_setattr_dynamic",
            category="dangerous_builtins",
            pattern=re.compile(r"\bsetattr\s*\(\s*(?:sys|os|builtins)"),
            severity=0.7,
            description="setattr() on system modules — runtime patching",
        ),
    ])

    # --- 2. Subprocess / shell execution (5) ---
    rules.extend([
        SourceCodeRule(
            id="sc_subprocess_shell",
            category="subprocess_shell",
            pattern=re.compile(
                r"subprocess\.(?:call|run|Popen|check_output|check_call)"
                r"\s*\([^)]*shell\s*=\s*True",
                _I,
            ),
            severity=0.9,
            description="subprocess with shell=True — command injection risk",
        ),
        SourceCodeRule(
            id="sc_os_system",
            category="subprocess_shell",
            pattern=re.compile(r"\bos\.system\s*\("),
            severity=0.9,
            description="os.system() — shell command execution",
        ),
        SourceCodeRule(
            id="sc_os_popen",
            category="subprocess_shell",
            pattern=re.compile(r"\bos\.popen\s*\("),
            severity=0.8,
            description="os.popen() — shell command execution",
        ),
        SourceCodeRule(
            id="sc_os_exec",
            category="subprocess_shell",
            pattern=re.compile(r"\bos\.exec[lv]p?e?\s*\("),
            severity=0.8,
            description="os.exec*() — process replacement",
        ),
        SourceCodeRule(
            id="sc_commands_getoutput",
            category="subprocess_shell",
            pattern=re.compile(r"\bcommands\.get(?:output|statusoutput)\s*\("),
            severity=0.7,
            description="commands.getoutput() — deprecated shell execution",
        ),
    ])

    # --- 3. Network access (4) ---
    rules.extend([
        SourceCodeRule(
            id="sc_socket_create",
            category="network_access",
            pattern=re.compile(r"\bsocket\.socket\s*\("),
            severity=0.5,
            description="Raw socket creation — potential network access",
        ),
        SourceCodeRule(
            id="sc_urllib_open",
            category="network_access",
            pattern=re.compile(
                r"\b(?:urllib\.request\.urlopen|urllib2\.urlopen)\s*\("
            ),
            severity=0.4,
            description="urllib urlopen — HTTP request",
        ),
        SourceCodeRule(
            id="sc_requests_call",
            category="network_access",
            pattern=re.compile(
                r"\brequests\.(?:get|post|put|delete|patch|head|request)\s*\("
            ),
            severity=0.3,
            description="requests library HTTP call",
        ),
        SourceCodeRule(
            id="sc_httpx_call",
            category="network_access",
            pattern=re.compile(
                r"\bhttpx\.(?:get|post|put|delete|patch|head|request|AsyncClient|Client)\s*\("
            ),
            severity=0.3,
            description="httpx library HTTP call",
        ),
    ])

    # --- 4. Filesystem sensitive paths (3) ---
    rules.extend([
        SourceCodeRule(
            id="sc_write_etc",
            category="filesystem_sensitive",
            pattern=re.compile(
                r"""(?:open|write|Path)\s*\(\s*['"]\/etc\/""",
            ),
            severity=0.9,
            description="Writing to /etc/ — system configuration modification",
        ),
        SourceCodeRule(
            id="sc_access_ssh",
            category="filesystem_sensitive",
            pattern=re.compile(
                r"""['"](?:~\/|/home/\w+/)\.ssh/""",
            ),
            severity=0.9,
            description="Access to ~/.ssh/ — SSH key exposure risk",
        ),
        SourceCodeRule(
            id="sc_read_llmos_config",
            category="filesystem_sensitive",
            pattern=re.compile(
                r"""['"](?:~\/|/home/\w+/)\.llmos/config""",
            ),
            severity=0.7,
            description="Reading ~/.llmos/config — daemon configuration access",
        ),
    ])

    # --- 5. Code obfuscation (4) ---
    rules.extend([
        SourceCodeRule(
            id="sc_b64_exec",
            category="obfuscation",
            pattern=re.compile(
                r"(?:base64\.b64decode|b64decode)\s*\([^)]*\).*(?:exec|eval)"
                r"|(?:exec|eval)\s*\(.*(?:base64\.b64decode|b64decode)",
            ),
            severity=0.95,
            description="base64 decode + exec/eval — obfuscated code execution",
        ),
        SourceCodeRule(
            id="sc_marshal_loads",
            category="obfuscation",
            pattern=re.compile(r"\bmarshal\.loads?\s*\("),
            severity=0.8,
            description="marshal.loads() — binary code object deserialization",
        ),
        SourceCodeRule(
            id="sc_pickle_loads",
            category="obfuscation",
            pattern=re.compile(r"\bpickle\.loads?\s*\("),
            severity=0.7,
            description="pickle.loads() — arbitrary object deserialization",
        ),
        SourceCodeRule(
            id="sc_codecs_rot13",
            category="obfuscation",
            pattern=re.compile(r"""codecs\.decode\s*\([^)]*['"]rot""", _I),
            severity=0.7,
            description="codecs.decode with rot13 — string obfuscation",
        ),
    ])

    # --- 6. Credential exposure (4) ---
    rules.extend([
        SourceCodeRule(
            id="sc_hardcoded_aws",
            category="credential_exposure",
            pattern=re.compile(
                r"""(?:AWS_SECRET|aws_secret_access_key)\s*=\s*['"][A-Za-z0-9/+=]{20,}['"]""",
            ),
            severity=0.95,
            description="Hardcoded AWS secret key",
        ),
        SourceCodeRule(
            id="sc_hardcoded_api_key",
            category="credential_exposure",
            pattern=re.compile(
                r"""(?:api_key|apikey|api_secret|secret_key)\s*=\s*['"][a-zA-Z0-9_\-]{16,}['"]""",
                _I,
            ),
            severity=0.8,
            description="Hardcoded API key or secret",
        ),
        SourceCodeRule(
            id="sc_hardcoded_token",
            category="credential_exposure",
            pattern=re.compile(
                r"""['"](?:sk-[a-zA-Z0-9]{20,}|ghp_[a-zA-Z0-9]{36,}|gho_[a-zA-Z0-9]{36,}|glpat-[a-zA-Z0-9\-_]{20,})['"]""",
            ),
            severity=0.95,
            description="Hardcoded service token (OpenAI/GitHub/GitLab)",
        ),
        SourceCodeRule(
            id="sc_password_literal",
            category="credential_exposure",
            pattern=re.compile(
                r"""(?:password|passwd|pwd)\s*=\s*['"][^'"]{8,}['"]""",
                _I,
            ),
            severity=0.6,
            description="Hardcoded password string",
        ),
    ])

    # --- 7. Code injection (3) ---
    rules.extend([
        SourceCodeRule(
            id="sc_ctypes_cdll",
            category="code_injection",
            pattern=re.compile(r"\bctypes\.(?:CDLL|cdll|WinDLL|windll)\s*\("),
            severity=0.8,
            description="ctypes dynamic library loading — native code execution",
        ),
        SourceCodeRule(
            id="sc_importlib_variable",
            category="code_injection",
            pattern=re.compile(
                r"\bimportlib\.import_module\s*\(\s*(?!['\"]\w)",
            ),
            severity=0.6,
            description="importlib.import_module with variable argument",
        ),
        SourceCodeRule(
            id="sc_sys_modules_inject",
            category="code_injection",
            pattern=re.compile(r"\bsys\.modules\s*\["),
            severity=0.6,
            description="sys.modules manipulation — module injection",
        ),
    ])

    # --- 8. Permission abuse (2) ---
    rules.extend([
        SourceCodeRule(
            id="sc_unrestricted_profile",
            category="permission_abuse",
            pattern=re.compile(
                r"""(?:permission_profile|profile)\s*[:=]\s*['"]unrestricted['"]""",
                _I,
            ),
            severity=0.9,
            description="Requesting unrestricted permission profile",
        ),
        SourceCodeRule(
            id="sc_disable_decorators",
            category="permission_abuse",
            pattern=re.compile(
                r"""enable_decorators\s*[:=]\s*(?:False|false|0)""",
            ),
            severity=0.8,
            description="Disabling security decorators",
        ),
    ])

    return rules


# ---------------------------------------------------------------------------
# Scanner
# ---------------------------------------------------------------------------


class SourceCodeScanner:
    """Scan Python source files in a module directory for security threats.

    Returns a :class:`SourceScanResult` with a score (0-100), verdict, and
    detailed findings including file paths and line numbers.

    The scanner is stateless and can be reused across multiple scans.
    """

    def __init__(
        self,
        *,
        extra_rules: list[SourceCodeRule] | None = None,
        disabled_rule_ids: set[str] | None = None,
        reject_threshold: float = 30.0,
        warn_threshold: float = 70.0,
    ) -> None:
        self._rules = _build_source_code_rules()
        if extra_rules:
            self._rules.extend(extra_rules)
        if disabled_rule_ids:
            for r in self._rules:
                if r.id in disabled_rule_ids:
                    r.enabled = False
        self._reject_threshold = reject_threshold
        self._warn_threshold = warn_threshold

    @property
    def rules(self) -> list[SourceCodeRule]:
        return list(self._rules)

    async def scan_directory(self, module_dir: Path) -> SourceScanResult:
        """Scan all ``.py`` files in *module_dir* for security threats.

        Args:
            module_dir: Path to the module directory to scan.

        Returns:
            Aggregated scan result with verdict, score, and findings.
        """
        t0 = time.monotonic()
        module_dir = module_dir.resolve()

        py_files = sorted(module_dir.rglob("*.py"))
        all_findings: list[SourceScanFinding] = []

        for py_file in py_files:
            # Skip test files and __pycache__
            rel = py_file.relative_to(module_dir)
            parts = rel.parts
            if any(p.startswith("__pycache__") for p in parts):
                continue
            if any(p.startswith("test") or p.startswith(".") for p in parts):
                continue

            findings = self.scan_file(py_file, module_dir)
            all_findings.extend(findings)

        # Compute score: start at 100, subtract based on findings.
        score = self._compute_score(all_findings)
        verdict = self._compute_verdict(score)

        elapsed_ms = (time.monotonic() - t0) * 1000.0

        return SourceScanResult(
            verdict=verdict,
            score=score,
            findings=all_findings,
            files_scanned=len(py_files),
            scan_duration_ms=elapsed_ms,
        )

    def scan_file(
        self, file_path: Path, module_dir: Path
    ) -> list[SourceScanFinding]:
        """Scan a single Python file against all enabled rules.

        Args:
            file_path: Absolute path to the ``.py`` file.
            module_dir: Root module directory (for relative path display).

        Returns:
            List of findings found in this file.
        """
        try:
            content = file_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return []

        try:
            rel_path = str(file_path.relative_to(module_dir))
        except ValueError:
            rel_path = str(file_path)

        findings: list[SourceScanFinding] = []
        lines = content.splitlines()

        for line_num, line in enumerate(lines, start=1):
            stripped = line.strip()
            # Skip empty lines and comments
            if not stripped or stripped.startswith("#"):
                continue

            for rule in self._rules:
                if not rule.enabled:
                    continue
                if rule.pattern.search(line):
                    findings.append(SourceScanFinding(
                        rule_id=rule.id,
                        category=rule.category,
                        severity=rule.severity,
                        file_path=rel_path,
                        line_number=line_num,
                        line_content=stripped[:200],
                        description=rule.description,
                    ))

        return findings

    def _compute_score(self, findings: list[SourceScanFinding]) -> float:
        """Compute a 0-100 score from findings.

        Scoring: each finding reduces the score proportionally to its
        severity.  Duplicate rule matches across different locations
        have diminishing returns (first hit: full weight, subsequent:
        50% weight).
        """
        if not findings:
            return 100.0

        # Track which rules we've already seen for diminishing returns.
        seen_rules: dict[str, int] = {}
        total_penalty = 0.0

        for f in findings:
            count = seen_rules.get(f.rule_id, 0)
            seen_rules[f.rule_id] = count + 1
            # Diminishing: first hit = full severity, subsequent = 50%.
            weight = 1.0 if count == 0 else 0.5
            total_penalty += f.severity * weight * 10.0

        # Clamp to 0-100.
        score = max(0.0, 100.0 - total_penalty)
        return round(score, 1)

    def _compute_verdict(self, score: float) -> ScanVerdict:
        """Map score to verdict based on configured thresholds."""
        if score < self._reject_threshold:
            return ScanVerdict.REJECT
        if score < self._warn_threshold:
            return ScanVerdict.WARN
        return ScanVerdict.ALLOW
