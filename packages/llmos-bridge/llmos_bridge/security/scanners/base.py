"""Security scanners — base abstractions for pluggable input scanning.

Defines the abstract interface that any input security scanner must
implement.  This keeps the scanner pipeline completely vendor-neutral.

Implementations:
  - HeuristicScanner  — regex/pattern-based, zero dependencies (<1ms)
  - LLMGuardScanner   — wraps ``llm-guard`` library (optional)
  - PromptGuardScanner — wraps Meta Prompt Guard 86M (optional)

Community scanners::

    class MyScanner(InputScanner):
        scanner_id = "my_scanner"
        priority = 40

        async def scan(self, text, context=None):
            ...
            return ScanResult(scanner_id=self.scanner_id, verdict=ScanVerdict.ALLOW)
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class ScanVerdict(str, Enum):
    """Verdict from a single scanner."""

    ALLOW = "allow"
    WARN = "warn"
    REJECT = "reject"


@dataclass
class ScanResult:
    """Result from a single scanner execution.

    Attributes:
        scanner_id:       Which scanner produced this result.
        verdict:          ALLOW / WARN / REJECT.
        risk_score:       0.0 (safe) to 1.0 (definitely malicious).
        threat_types:     List of detected threat category IDs.
        details:          Human-readable detail string.
        matched_patterns: Pattern names/IDs that matched (heuristic).
        scan_duration_ms: How long this scanner took.
        metadata:         Scanner-specific extra data.
    """

    scanner_id: str
    verdict: ScanVerdict
    risk_score: float = 0.0
    threat_types: list[str] = field(default_factory=list)
    details: str = ""
    matched_patterns: list[str] = field(default_factory=list)
    scan_duration_ms: float = 0.0
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "scanner_id": self.scanner_id,
            "verdict": self.verdict.value,
            "risk_score": self.risk_score,
            "threat_types": self.threat_types,
            "details": self.details,
            "matched_patterns": self.matched_patterns,
            "scan_duration_ms": self.scan_duration_ms,
            "metadata": self.metadata,
        }


@dataclass
class ScanContext:
    """Contextual information passed to scanners for richer analysis.

    Scanners that only need raw text can ignore this, but ML-based
    scanners benefit from knowing the plan structure.
    """

    plan_id: str = ""
    plan_description: str = ""
    action_count: int = 0
    module_ids: list[str] = field(default_factory=list)
    session_id: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)


class InputScanner(ABC):
    """Abstract base class for all pre-execution input security scanners.

    Contract:
      - ``scanner_id`` must be unique across all registered scanners.
      - ``priority`` determines execution order (lower = first = fastest).
      - ``scan()`` must be async and must NOT raise — errors should be
        returned as ``ScanResult(verdict=WARN, details="error: ...")``.
      - ``close()`` releases any held resources (model handles, etc.).
    """

    scanner_id: str = ""
    priority: int = 100
    version: str = "0.1.0"
    description: str = ""

    @abstractmethod
    async def scan(
        self, text: str, context: ScanContext | None = None
    ) -> ScanResult:
        """Scan input text for security threats.

        Args:
            text: The raw input text (serialised IML plan JSON).
            context: Optional contextual information about the plan.

        Returns:
            ScanResult with verdict, risk score, and details.
            Must NOT raise — wrap errors in ScanResult(verdict=WARN).
        """
        ...

    async def close(self) -> None:
        """Release any held resources (model handles, HTTP clients, etc.)."""

    def status(self) -> dict[str, Any]:
        """Return scanner status for REST API introspection."""
        return {
            "scanner_id": self.scanner_id,
            "priority": self.priority,
            "version": self.version,
            "description": self.description,
        }
