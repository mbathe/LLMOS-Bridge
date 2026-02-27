"""Security scanners — ultra-fast heuristic/regex-based scanner (Layer 1).

Zero external dependencies.  Runs in <1ms for typical inputs.
Ships with ~35 built-in patterns across 9 threat categories.

Users can add/remove/disable individual patterns at runtime.

Usage::

    scanner = HeuristicScanner()
    result = await scanner.scan(plan_json)

    # Add a custom pattern
    scanner.add_pattern(PatternRule(
        id="my_pattern",
        category="custom",
        pattern=re.compile(r"my_suspicious_keyword", re.IGNORECASE),
        severity=0.7,
    ))
"""

from __future__ import annotations

import base64
import re
import unicodedata
from dataclasses import dataclass
from typing import Any

from llmos_bridge.security.scanners.base import (
    InputScanner,
    ScanContext,
    ScanResult,
    ScanVerdict,
)

_I = re.IGNORECASE


@dataclass
class PatternRule:
    """A single detection rule for the heuristic scanner.

    Attributes:
        id:          Unique identifier (e.g. ``"pi_ignore_instructions"``).
        category:    Threat category (e.g. ``"prompt_injection"``).
        pattern:     Compiled regex pattern.
        severity:    Risk score contribution (0.0–1.0).
        description: Human-readable description.
        enabled:     Whether this rule is active.
    """

    id: str
    category: str
    pattern: re.Pattern[str]
    severity: float = 0.5
    description: str = ""
    enabled: bool = True


# ---------------------------------------------------------------------------
# Built-in pattern definitions (~35 rules across 9 categories)
# ---------------------------------------------------------------------------


def _build_default_patterns() -> list[PatternRule]:
    """Build the default pattern ruleset."""
    rules: list[PatternRule] = []

    # --- 1. Prompt injection keywords (8) ---
    rules.extend([
        PatternRule(
            id="pi_ignore_instructions",
            category="prompt_injection",
            pattern=re.compile(
                r"ignore\s+(?:all\s+)?(?:previous|prior|earlier|above)\s+instructions?",
                _I,
            ),
            severity=0.9,
            description="Classic 'ignore previous instructions' injection",
        ),
        PatternRule(
            id="pi_disregard",
            category="prompt_injection",
            pattern=re.compile(
                r"disregard\s+(?:all\s+)?(?:your|previous|prior|earlier)\s+"
                r"(?:instructions?|rules?|guidelines?)",
                _I,
            ),
            severity=0.9,
            description="Disregard instructions variant",
        ),
        PatternRule(
            id="pi_new_instructions",
            category="prompt_injection",
            pattern=re.compile(
                r"(?:your|my)\s+new\s+(?:instructions?|task|objective|goal)\s+(?:is|are)",
                _I,
            ),
            severity=0.85,
            description="Overriding instructions with new ones",
        ),
        PatternRule(
            id="pi_forget_everything",
            category="prompt_injection",
            pattern=re.compile(
                r"forget\s+(?:everything|all)\s+(?:you\s+)?(?:know|were\s+told|learned)",
                _I,
            ),
            severity=0.9,
            description="Forget everything variant",
        ),
        PatternRule(
            id="pi_do_not_follow",
            category="prompt_injection",
            pattern=re.compile(
                r"do\s+not\s+follow\s+(?:any|your|the)\s+(?:previous|original|initial)",
                _I,
            ),
            severity=0.85,
            description="Do not follow previous instructions",
        ),
        PatternRule(
            id="pi_override_rules",
            category="prompt_injection",
            pattern=re.compile(
                r"(?:override|bypass|skip|circumvent)\s+(?:all\s+)?"
                r"(?:safety|security|content)\s+(?:rules?|filters?|checks?|guidelines?)",
                _I,
            ),
            severity=0.95,
            description="Explicit override of safety rules",
        ),
        PatternRule(
            id="pi_jailbreak_keywords",
            category="prompt_injection",
            pattern=re.compile(r"\b(?:DAN|jailbreak|DUDE|AIM|STAN|DevMode)\b", _I),
            severity=0.8,
            description="Known jailbreak persona names",
        ),
        PatternRule(
            id="pi_pretend_no_restrictions",
            category="prompt_injection",
            pattern=re.compile(
                r"(?:pretend|imagine|act\s+as\s+if)\s+(?:you\s+)?"
                r"(?:have\s+no|don'?t\s+have\s+any|without\s+any)\s+"
                r"(?:restrictions?|limitations?|rules?|filters?)",
                _I,
            ),
            severity=0.85,
            description="Pretend no restrictions",
        ),
    ])

    # --- 2. Role manipulation (4) ---
    rules.extend([
        PatternRule(
            id="role_system_override",
            category="role_manipulation",
            pattern=re.compile(r"system\s*:\s*you\s+are\s+now", _I),
            severity=0.9,
            description="System role override",
        ),
        PatternRule(
            id="role_act_as",
            category="role_manipulation",
            pattern=re.compile(
                r"(?:act|behave|respond|function)\s+as\s+"
                r"(?:if\s+you\s+(?:are|were)\s+)?(?:a|an|the)\s+"
                r"(?:unrestricted|unfiltered|uncensored)",
                _I,
            ),
            severity=0.85,
            description="Act as unrestricted entity",
        ),
        PatternRule(
            id="role_you_are_now",
            category="role_manipulation",
            pattern=re.compile(
                r"(?:from\s+now\s+on\s+)?you\s+are\s+(?:now\s+)?"
                r"(?:a|an)\s+(?:different|new|unrestricted)",
                _I,
            ),
            severity=0.85,
            description="You are now a different entity",
        ),
        PatternRule(
            id="role_developer_mode",
            category="role_manipulation",
            pattern=re.compile(
                r"(?:enable|activate|enter|switch\s+to)\s+"
                r"(?:developer|dev|debug|admin|root|god)\s+mode",
                _I,
            ),
            severity=0.9,
            description="Developer/admin mode activation",
        ),
    ])

    # --- 3. Delimiter injection (4) ---
    rules.extend([
        PatternRule(
            id="delim_inst_tag",
            category="delimiter_injection",
            pattern=re.compile(
                r"<\s*/?(?:INST|s|system|human|assistant)\s*>", _I
            ),
            severity=0.85,
            description="Chat template delimiter tags",
        ),
        PatternRule(
            id="delim_system_bracket",
            category="delimiter_injection",
            pattern=re.compile(r"\[(?:SYSTEM|INST|/INST|SYS|/SYS)\]", _I),
            severity=0.85,
            description="System bracket delimiters",
        ),
        PatternRule(
            id="delim_markdown_system",
            category="delimiter_injection",
            pattern=re.compile(r"```\s*system\s*\n", _I),
            severity=0.7,
            description="Markdown code block with system label",
        ),
        PatternRule(
            id="delim_separator_injection",
            category="delimiter_injection",
            pattern=re.compile(
                r"(?:---+|===+|####+)\s*(?:system|instructions?|new\s+task)", _I
            ),
            severity=0.7,
            description="Separator-based instruction injection",
        ),
    ])

    # --- 4. Base64/encoding attacks (3) ---
    rules.extend([
        PatternRule(
            id="enc_base64_long",
            category="encoding_attack",
            pattern=re.compile(r"(?:[A-Za-z0-9+/]{40,}={0,2})"),
            severity=0.4,
            description="Suspiciously long base64 string in params",
        ),
        PatternRule(
            id="enc_hex_payload",
            category="encoding_attack",
            pattern=re.compile(r"\\x[0-9a-fA-F]{2}(?:\\x[0-9a-fA-F]{2}){7,}"),
            severity=0.6,
            description="Hex-encoded payload (8+ bytes)",
        ),
        PatternRule(
            id="enc_url_encoded_injection",
            category="encoding_attack",
            pattern=re.compile(
                r"%(?:69|49)(?:%67|%47)(?:%6e|%4e)(?:%6f|%4f)(?:%72|%52)(?:%65|%45)",
                _I,
            ),
            severity=0.8,
            description="URL-encoded 'ignore' keyword",
        ),
    ])

    # --- 5. Unicode tricks (2) ---
    rules.extend([
        PatternRule(
            id="unicode_rtl_override",
            category="unicode_attack",
            pattern=re.compile(r"[\u200e\u200f\u202a-\u202e\u2066-\u2069]"),
            severity=0.7,
            description="Unicode BiDi control characters (RTL override)",
        ),
        PatternRule(
            id="unicode_homoglyph",
            category="unicode_attack",
            pattern=re.compile(r"[\u0400-\u04ff\uff00-\uffef]"),
            severity=0.3,
            description="Non-ASCII lookalike characters (potential homoglyph)",
        ),
    ])

    # --- 6. Path traversal (3) ---
    rules.extend([
        PatternRule(
            id="path_traversal_dots",
            category="path_traversal",
            pattern=re.compile(r"\.\.[/\\](?:\.\.[/\\])*"),
            severity=0.7,
            description="Directory traversal with ../",
        ),
        PatternRule(
            id="path_traversal_encoded",
            category="path_traversal",
            pattern=re.compile(r"%2e%2e[%2f%5c]", _I),
            severity=0.8,
            description="URL-encoded directory traversal",
        ),
        PatternRule(
            id="path_sensitive_files",
            category="path_traversal",
            pattern=re.compile(
                r"(?:/etc/(?:passwd|shadow|sudoers)|"
                r"\.ssh/(?:id_rsa|authorized_keys|config)|"
                r"\.(?:bashrc|profile|zshrc|env)|"
                r"\.llmos/config\.yaml|"
                r"\.aws/credentials|"
                r"\.kube/config)",
                _I,
            ),
            severity=0.85,
            description="Access to known sensitive files",
        ),
    ])

    # --- 7. Shell injection indicators (4) ---
    rules.extend([
        PatternRule(
            id="shell_pipe_chain",
            category="shell_injection",
            pattern=re.compile(
                r"[|;`]\s*(?:curl|wget|nc|ncat|python|perl|ruby|php|bash|sh|zsh|powershell)\b",
                _I,
            ),
            severity=0.8,
            description="Pipe/chain to network or scripting tools",
        ),
        PatternRule(
            id="shell_subcommand",
            category="shell_injection",
            pattern=re.compile(r"\$\(.*\)|\x60[^`]+\x60"),
            severity=0.6,
            description="Command substitution in params",
        ),
        PatternRule(
            id="shell_reverse_shell",
            category="shell_injection",
            pattern=re.compile(
                r"(?:bash\s+-i\s+>&|/dev/tcp/|mkfifo|nc\s+-[el]|ncat\s+-[el])",
                _I,
            ),
            severity=0.95,
            description="Reverse shell pattern",
        ),
        PatternRule(
            id="shell_rm_rf",
            category="shell_injection",
            pattern=re.compile(r"\brm\s+-[rR]?f\s+/", _I),
            severity=0.95,
            description="Destructive rm -rf / command",
        ),
    ])

    # --- 8. Data exfiltration indicators (3) ---
    rules.extend([
        PatternRule(
            id="exfil_curl_post",
            category="data_exfiltration",
            pattern=re.compile(r"curl\s+.*-(?:X\s+POST|d\s+@|-data)", _I),
            severity=0.7,
            description="curl POST with data (potential exfil)",
        ),
        PatternRule(
            id="exfil_dns_tunnel",
            category="data_exfiltration",
            pattern=re.compile(r"(?:dig|nslookup|host)\s+.*\.\w{2,4}$", _I),
            severity=0.6,
            description="DNS lookup pattern (potential DNS tunnel)",
        ),
        PatternRule(
            id="exfil_webhook",
            category="data_exfiltration",
            pattern=re.compile(
                r"https?://(?:webhook\.site|requestbin|hookbin|pipedream|ngrok|burp)",
                _I,
            ),
            severity=0.85,
            description="Known exfiltration webhook URLs",
        ),
    ])

    # --- 9. Privilege escalation file targets (4) ---
    rules.extend([
        PatternRule(
            id="privesc_sudoers",
            category="privilege_escalation",
            pattern=re.compile(
                r"(?:write_file|append|create).*(?:/etc/sudoers|/etc/passwd|/etc/shadow)",
                _I,
            ),
            severity=0.95,
            description="Write to privilege escalation targets",
        ),
        PatternRule(
            id="privesc_cron",
            category="privilege_escalation",
            pattern=re.compile(
                r"(?:write_file|append|create).*(?:/etc/cron|/var/spool/cron|crontab)",
                _I,
            ),
            severity=0.85,
            description="Write to cron files",
        ),
        PatternRule(
            id="privesc_ssh_keys",
            category="privilege_escalation",
            pattern=re.compile(
                r"(?:write_file|append|create).*(?:authorized_keys|\.ssh/)", _I
            ),
            severity=0.9,
            description="Write to SSH authorized_keys",
        ),
        PatternRule(
            id="privesc_systemd",
            category="privilege_escalation",
            pattern=re.compile(
                r"(?:write_file|create).*(?:/etc/systemd/|/lib/systemd/|\.service$)",
                _I,
            ),
            severity=0.85,
            description="Write to systemd service files",
        ),
    ])

    return rules


# Compiled once at module load time — cheap regex check on import.
_B64_RE = re.compile(r"[A-Za-z0-9+/]{40,}={0,2}")

# Keywords that are suspicious when found inside a decoded base64 payload.
_SUSPICIOUS_DECODED_KEYWORDS = (
    "ignore",
    "system:",
    "instructions",
    "/bin/",
    "curl",
    "wget",
    "/etc/passwd",
    "authorized_keys",
)


class HeuristicScanner(InputScanner):
    """Ultra-fast regex/heuristic-based input scanner (Layer 1).

    Zero external dependencies.  Runs in <1ms for typical inputs.
    Ships with ~35 built-in patterns across 9 categories.
    """

    scanner_id = "heuristic"
    priority = 10  # Runs first (lowest priority number)
    version = "1.0.0"
    description = "Regex/heuristic pattern scanner (zero dependencies, <1ms)"

    def __init__(
        self,
        *,
        extra_patterns: list[PatternRule] | None = None,
        disabled_pattern_ids: list[str] | None = None,
        reject_threshold: float = 0.7,
        warn_threshold: float = 0.3,
    ) -> None:
        self._patterns = _build_default_patterns()
        if extra_patterns:
            self._patterns.extend(extra_patterns)
        if disabled_pattern_ids:
            disabled = set(disabled_pattern_ids)
            for p in self._patterns:
                if p.id in disabled:
                    p.enabled = False
        self._reject_threshold = reject_threshold
        self._warn_threshold = warn_threshold

    @property
    def patterns(self) -> list[PatternRule]:
        return self._patterns

    def add_pattern(self, rule: PatternRule) -> None:
        """Add a custom pattern rule at runtime."""
        self._patterns.append(rule)

    def disable_pattern(self, pattern_id: str) -> bool:
        for p in self._patterns:
            if p.id == pattern_id:
                p.enabled = False
                return True
        return False

    def enable_pattern(self, pattern_id: str) -> bool:
        for p in self._patterns:
            if p.id == pattern_id:
                p.enabled = True
                return True
        return False

    @staticmethod
    def _normalize_text(text: str) -> str:
        """Normalise text for robust pattern matching.

        - NFKC decomposition maps fullwidth/compatibility chars to ASCII
          equivalents (e.g. ``\uff49\uff47\uff4e\uff4f\uff52\uff45`` → ``ignore``).
        - Zero-width characters are stripped so they cannot split keywords.
        """
        # 1. NFKC (compatibility decomposition + canonical composition)
        normalized = unicodedata.normalize("NFKC", text)
        # 2. Strip zero-width characters (ZWJ, ZWNJ, ZWSP, soft-hyphen, BOM)
        _ZERO_WIDTH = frozenset("\u200b\u200c\u200d\ufeff\u00ad\u2060")
        return "".join(ch for ch in normalized if ch not in _ZERO_WIDTH)

    async def scan(
        self, text: str, context: ScanContext | None = None
    ) -> ScanResult:
        """Scan text against all enabled heuristic patterns."""
        normalized = self._normalize_text(text)

        matched: list[str] = []
        threat_types: set[str] = set()
        max_severity = 0.0

        for rule in self._patterns:
            if not rule.enabled:
                continue
            if rule.pattern.search(normalized):
                matched.append(rule.id)
                threat_types.add(rule.category)
                if rule.severity > max_severity:
                    max_severity = rule.severity

        # Additional non-regex heuristic: decode base64 payloads.
        # Use original text for b64 detection (normalisation may corrupt b64 padding).
        extra_score = self._check_base64_payloads(text)
        if extra_score > 0:
            max_severity = max(max_severity, extra_score)
            if "encoding_attack" not in threat_types:
                threat_types.add("encoding_attack")
            matched.append("base64_decoded_suspicious")

        # Determine verdict.
        risk_score = max_severity
        if risk_score >= self._reject_threshold:
            verdict = ScanVerdict.REJECT
        elif risk_score >= self._warn_threshold:
            verdict = ScanVerdict.WARN
        else:
            verdict = ScanVerdict.ALLOW

        details = ""
        if matched:
            shown = ", ".join(matched[:5])
            details = f"Matched {len(matched)} pattern(s): {shown}"
            if len(matched) > 5:
                details += f" (+{len(matched) - 5} more)"

        return ScanResult(
            scanner_id=self.scanner_id,
            verdict=verdict,
            risk_score=round(risk_score, 3),
            threat_types=sorted(threat_types),
            details=details,
            matched_patterns=matched,
        )

    @staticmethod
    def _check_base64_payloads(text: str) -> float:
        """Decode base64 strings and check for suspicious content."""
        for match in _B64_RE.finditer(text):
            try:
                decoded = base64.b64decode(match.group()).decode(
                    "utf-8", errors="ignore"
                )
                lower = decoded.lower()
                if any(kw in lower for kw in _SUSPICIOUS_DECODED_KEYWORDS):
                    return 0.8
            except Exception:
                pass
        return 0.0

    def status(self) -> dict[str, Any]:
        base = super().status()
        enabled_patterns = [p for p in self._patterns if p.enabled]
        base["pattern_count"] = len(self._patterns)
        base["enabled_pattern_count"] = len(enabled_patterns)
        base["categories"] = sorted({p.category for p in enabled_patterns})
        return base
