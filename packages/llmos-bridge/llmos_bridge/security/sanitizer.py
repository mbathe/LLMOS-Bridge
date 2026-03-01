"""Security layer — Output sanitiser.

Sanitises module outputs before they are:
  - Returned to the LLM as action results
  - Injected as {{result.X.Y}} template values in subsequent actions

This layer defends against prompt injection via file contents or API responses.

Rules applied:
  1. Truncation     — Outputs exceeding the configured limit are truncated.
  2. Injection scan — Patterns that match known prompt injection attempts are flagged.
  3. Encoding norm  — Normalise encoding to prevent Unicode tricks.
  4. Nested depth   — Prevent deeply nested JSON objects from bloating context.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Any

from llmos_bridge.logging import get_logger

log = get_logger(__name__)

# Patterns that commonly appear in prompt injection payloads embedded in content.
_INJECTION_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"ignore\s+(?:all\s+)?(?:previous|prior|earlier)\s+instructions?", re.IGNORECASE),
    re.compile(r"ignore\s+all\s+(?:previous|prior|earlier\s+)?instructions?", re.IGNORECASE),
    re.compile(r"system\s*:\s*you\s+are\s+now", re.IGNORECASE),
    re.compile(r"<\s*INST\s*>", re.IGNORECASE),
    re.compile(r"\[SYSTEM\]", re.IGNORECASE),
    re.compile(r"act\s+as\s+if\s+you\s+(are|were)", re.IGNORECASE),
    re.compile(r"disregard\s+your\s+(previous|prior|earlier)\s+instructions?", re.IGNORECASE),
    re.compile(r"your\s+new\s+instructions?\s+are", re.IGNORECASE),
]

_DEFAULT_MAX_STR_LEN = 50_000
_DEFAULT_MAX_DEPTH = 10
_DEFAULT_MAX_LIST_ITEMS = 1_000


class OutputSanitizer:
    """Sanitises action outputs before returning them to the LLM context.

    Usage::

        sanitizer = OutputSanitizer()
        clean = sanitizer.sanitize(raw_output, module="filesystem", action="read_file")
    """

    def __init__(
        self,
        max_str_len: int = _DEFAULT_MAX_STR_LEN,
        max_depth: int = _DEFAULT_MAX_DEPTH,
        max_list_items: int = _DEFAULT_MAX_LIST_ITEMS,
        injection_scan: bool = True,
    ) -> None:
        self._max_str_len = max_str_len
        self._max_depth = max_depth
        self._max_list_items = max_list_items
        self._injection_scan = injection_scan

    def sanitize(
        self, output: Any, module: str = "", action: str = ""
    ) -> Any:
        """Sanitise *output* and return the cleaned value."""
        return self._clean(output, depth=0, module=module, action=action)

    # ------------------------------------------------------------------
    # Private
    # ------------------------------------------------------------------

    # Keys whose values are binary/base64 data and should not be sanitised
    # (truncation would corrupt the encoding).
    _BINARY_KEYS = frozenset({
        "screenshot_b64", "labeled_image_b64", "image_b64",
        "annotated_image_b64", "image_base64", "data_b64",
    })

    def _clean(self, value: Any, depth: int, module: str, action: str) -> Any:
        if depth > self._max_depth:
            log.warning(
                "sanitizer_depth_exceeded",
                module=module,
                action=action,
                max_depth=self._max_depth,
            )
            return "[TRUNCATED: max depth exceeded]"

        if isinstance(value, str):
            return self._clean_string(value, module=module, action=action)
        if isinstance(value, dict):
            return {
                k: (v if k in self._BINARY_KEYS and isinstance(v, str)
                    else self._clean(v, depth + 1, module, action))
                for k, v in value.items()
            }
        if isinstance(value, list):
            if len(value) > self._max_list_items:
                log.warning(
                    "sanitizer_list_truncated",
                    original_len=len(value),
                    max_len=self._max_list_items,
                )
                value = value[: self._max_list_items]
            return [self._clean(item, depth + 1, module, action) for item in value]
        return value

    def _clean_string(self, value: str, module: str, action: str) -> str:
        # 1. Normalise Unicode (NFKC) to collapse compatibility characters
        # and prevent homoglyph tricks.
        value = unicodedata.normalize("NFKC", value)

        # 2. Scan for injection patterns and neutralise.
        if self._injection_scan:
            for pattern in _INJECTION_PATTERNS:
                if pattern.search(value):
                    log.warning(
                        "sanitizer_injection_detected",
                        module=module,
                        action=action,
                        pattern=pattern.pattern,
                    )
                    # Replace the match with a placeholder rather than
                    # dropping the entire content — we still want the
                    # LLM to know a file existed.
                    value = pattern.sub("[REDACTED:injection-pattern]", value)

        # 3. Truncate excessively long strings.
        if len(value) > self._max_str_len:
            log.warning(
                "sanitizer_string_truncated",
                original_len=len(value),
                max_len=self._max_str_len,
            )
            value = (
                value[: self._max_str_len]
                + f"\n[TRUNCATED: {len(value) - self._max_str_len} chars omitted]"
            )

        return value
