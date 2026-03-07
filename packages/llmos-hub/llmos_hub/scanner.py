"""Hub-side source code scanner — lightweight security gate for publishing.

Self-contained module with no ``llmos_bridge`` imports.  Implements a
subset (~15) of the daemon's SourceCodeScanner rules, focused on
high-severity threats that should block module publishing.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path


class ScanVerdict(str, Enum):
    ALLOW = "allow"
    WARN = "warn"
    REJECT = "reject"


@dataclass
class HubScanFinding:
    rule_id: str
    category: str
    severity: float  # 0-10
    file_path: str
    line_number: int
    description: str

    def to_dict(self) -> dict:
        return {
            "rule_id": self.rule_id,
            "category": self.category,
            "severity": self.severity,
            "file_path": self.file_path,
            "line_number": self.line_number,
            "description": self.description,
        }


@dataclass
class HubScanResult:
    verdict: ScanVerdict
    score: float  # 0-100, 100 = clean
    findings: list[HubScanFinding] = field(default_factory=list)
    files_scanned: int = 0

    def to_dict(self) -> dict:
        return {
            "verdict": self.verdict.value,
            "score": self.score,
            "findings": [f.to_dict() for f in self.findings],
            "files_scanned": self.files_scanned,
        }


@dataclass
class _Rule:
    rule_id: str
    category: str
    pattern: re.Pattern
    severity: float
    description: str


def _build_hub_rules() -> list[_Rule]:
    """Build the hub scanner rule set (~15 high-severity rules)."""
    rules = [
        # Dangerous builtins
        _Rule("sc_eval", "dangerous_builtins", re.compile(r"\beval\s*\("), 8.0,
              "Use of eval() — arbitrary code execution"),
        _Rule("sc_exec", "dangerous_builtins", re.compile(r"\bexec\s*\("), 8.0,
              "Use of exec() — arbitrary code execution"),
        _Rule("sc_compile", "dangerous_builtins", re.compile(r"\bcompile\s*\("), 6.0,
              "Use of compile() — dynamic code compilation"),
        _Rule("sc_dunder_import", "dangerous_builtins", re.compile(r"__import__\s*\("), 7.0,
              "Use of __import__() — dynamic module import"),

        # Subprocess / shell
        _Rule("sc_subprocess_shell", "subprocess_shell",
              re.compile(r"subprocess\.\w+\([^)]*shell\s*=\s*True"), 9.0,
              "subprocess with shell=True — command injection risk"),
        _Rule("sc_os_system", "subprocess_shell", re.compile(r"\bos\.system\s*\("), 9.0,
              "os.system() — shell command execution"),
        _Rule("sc_os_popen", "subprocess_shell", re.compile(r"\bos\.popen\s*\("), 8.0,
              "os.popen() — shell command execution"),

        # Obfuscation
        _Rule("sc_b64_exec", "obfuscation",
              re.compile(r"base64\..*decode.*exec|exec.*base64\..*decode"), 10.0,
              "base64 decode + exec combo — obfuscated execution"),
        _Rule("sc_marshal_loads", "obfuscation", re.compile(r"\bmarshal\.loads?\s*\("), 8.0,
              "marshal.loads() — binary code loading"),
        _Rule("sc_pickle_loads", "obfuscation", re.compile(r"\bpickle\.loads?\s*\("), 7.0,
              "pickle.loads() — arbitrary object deserialization"),

        # Credential exposure
        _Rule("sc_hardcoded_aws", "credential_exposure",
              re.compile(r"['\"]AKIA[A-Z0-9]{16}['\"]"), 9.0,
              "Hardcoded AWS access key"),
        _Rule("sc_hardcoded_token", "credential_exposure",
              re.compile(r"['\"](?:ghp_|sk-|xoxb-)[a-zA-Z0-9_-]{20,}['\"]"), 9.0,
              "Hardcoded API token (GitHub/OpenAI/Slack)"),

        # Code injection
        _Rule("sc_ctypes_cdll", "code_injection", re.compile(r"\bctypes\.(?:cdll|CDLL)\b"), 8.0,
              "ctypes native library loading"),

        # Permission abuse
        _Rule("sc_unrestricted_profile", "permission_abuse",
              re.compile(r"permission_profile.*unrestricted|unrestricted.*permission_profile"), 7.0,
              "Attempts to set unrestricted permission profile"),
        _Rule("sc_disable_decorators", "permission_abuse",
              re.compile(r"enable_decorators.*False|False.*enable_decorators"), 7.0,
              "Attempts to disable security decorators"),
    ]
    return rules


class HubSourceScanner:
    """Simplified source code scanner for the hub server.

    Scans extracted module directories at publish time.  Rejects modules
    with dangerous patterns (eval+base64, shell=True, etc.).
    """

    def __init__(self, *, reject_threshold: float = 30.0) -> None:
        self._rules = _build_hub_rules()
        self._reject_threshold = reject_threshold

    def scan_directory(self, module_dir: Path) -> HubScanResult:
        """Synchronous scan of a module directory."""
        findings: list[HubScanFinding] = []
        files_scanned = 0

        for py_file in module_dir.rglob("*.py"):
            # Skip test dirs, __pycache__, hidden dirs.
            parts = py_file.relative_to(module_dir).parts
            if any(p.startswith(".") or p == "__pycache__" or p in ("tests", "test") for p in parts):
                continue

            files_scanned += 1
            try:
                lines = py_file.read_text(encoding="utf-8", errors="replace").splitlines()
            except OSError:
                continue

            rel_path = str(py_file.relative_to(module_dir))
            for line_num, line in enumerate(lines, 1):
                stripped = line.strip()
                # Skip comments.
                if stripped.startswith("#"):
                    continue
                for rule in self._rules:
                    if rule.pattern.search(line):
                        findings.append(HubScanFinding(
                            rule_id=rule.rule_id,
                            category=rule.category,
                            severity=rule.severity,
                            file_path=rel_path,
                            line_number=line_num,
                            description=rule.description,
                        ))

        # Score: start at 100, deduct per finding with diminishing returns.
        score = 100.0
        seen_rules: dict[str, int] = {}
        for f in findings:
            count = seen_rules.get(f.rule_id, 0) + 1
            seen_rules[f.rule_id] = count
            # Diminishing: first hit = full, second = half, etc.
            deduction = f.severity / count
            score -= deduction
        score = max(0.0, min(100.0, score))

        # Verdict.
        if score < self._reject_threshold:
            verdict = ScanVerdict.REJECT
        elif findings:
            verdict = ScanVerdict.WARN
        else:
            verdict = ScanVerdict.ALLOW

        return HubScanResult(
            verdict=verdict,
            score=round(score, 1),
            findings=findings,
            files_scanned=files_scanned,
        )
