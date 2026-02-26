"""IML Protocol — JSON repair and LLM correction feedback.

LLMs frequently generate JSON that is *almost* valid but fails strict parsing:
trailing commas, unquoted keys, single quotes instead of double quotes,
Python literals (True/False/None), C-style comments, and truncated output.

This module provides two capabilities:

1. **IMLRepair** — a best-effort JSON fixer that applies a cascade of
   lightweight transformations before attempting ``json.loads``.  It does NOT
   rely on external libraries so it works offline and adds zero dependencies.

2. **CorrectionPromptFormatter** — formats a structured error report that
   can be appended to the LLM prompt to request a corrected plan.  The report
   includes the original error, the line/column of the failure, and a concise
   repair hint so the LLM can fix the exact problem rather than regenerating
   the entire plan.

Design decisions:
  - Repair is applied greedily in fixed order.  Each transformation is
    independent and does not depend on earlier ones having succeeded.
  - Repair never changes semantic meaning.  It only fixes syntax.
  - If repair fails, ``IMLRepair.repair`` raises ``IMLParseError`` with the
    *original* parse error embedded so the caller can surface both.
  - CorrectionPromptFormatter produces plain text, not JSON, so it can be
    concatenated into any LLM prompt without further escaping.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

from llmos_bridge.exceptions import IMLParseError


# ---------------------------------------------------------------------------
# Internal repair transformations (applied in order, each is a pure function)
# ---------------------------------------------------------------------------


def _remove_js_comments(text: str) -> str:
    """Strip // line comments and /* block comments */ from JSON-like text."""
    # Block comments first (non-greedy, dotall).
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.DOTALL)
    # Line comments — must not touch URLs (://), so match only whitespace before //.
    text = re.sub(r"(?<!\:)//[^\n]*", "", text)
    return text


def _trailing_commas(text: str) -> str:
    """Remove trailing commas before } or ] (illegal in JSON)."""
    return re.sub(r",\s*([}\]])", r"\1", text)


def _single_to_double_quotes(text: str) -> str:
    """Heuristically convert single-quoted strings to double-quoted.

    This only handles the common case where single quotes wrap simple values
    with no internal single quotes.  It does not handle escaped single quotes
    inside strings — that would require a full parser.
    """
    # Match: ': 'value' or , 'value'  but not contractions inside words.
    return re.sub(r"'([^']*)'", r'"\1"', text)


def _python_literals(text: str) -> str:
    """Replace Python literals True/False/None with JSON equivalents."""
    text = re.sub(r"\bTrue\b", "true", text)
    text = re.sub(r"\bFalse\b", "false", text)
    text = re.sub(r"\bNone\b", "null", text)
    return text


def _unquoted_keys(text: str) -> str:
    """Quote unquoted object keys: {key: ...} → {"key": ...}."""
    return re.sub(r'(?<=[{,])\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*:', r' "\1":', text)


def _close_open_structure(text: str) -> str:
    """Append missing closing braces/brackets for truncated output."""
    opens = text.count("{") - text.count("}")
    closes = text.count("[") - text.count("]")
    if opens > 0:
        text = text.rstrip() + ("}" * opens)
    if closes > 0:
        text = text.rstrip() + ("]" * closes)
    return text


_REPAIRS: list[Any] = [
    _remove_js_comments,
    _trailing_commas,
    _python_literals,
    _unquoted_keys,
    _single_to_double_quotes,
    _close_open_structure,
]


# ---------------------------------------------------------------------------
# Public repair class
# ---------------------------------------------------------------------------


@dataclass
class RepairResult:
    """Outcome of an ``IMLRepair.repair`` call."""

    original_text: str
    repaired_text: str
    parsed: dict[str, Any]
    transformations_applied: list[str]
    was_modified: bool


class IMLRepair:
    """Best-effort JSON repair for LLM-generated IML plan payloads.

    Usage::

        repair = IMLRepair()
        try:
            result = repair.repair(raw_json_string)
            plan = IMLParser().parse(result.parsed)
        except IMLParseError as exc:
            feedback = CorrectionPromptFormatter().format(raw_json_string, exc)
            # Append feedback to LLM prompt and retry.

    The repair pipeline applies each transformation in sequence and retries
    ``json.loads`` after each successful fix.  The first clean parse wins.
    """

    def repair(self, text: str) -> RepairResult:
        """Attempt to repair *text* into valid JSON.

        Args:
            text: Raw string from the LLM that is expected to be a JSON object.

        Returns:
            A :class:`RepairResult` with the parsed dict and metadata.

        Raises:
            IMLParseError: If all repair attempts fail.  The error message
                contains the last ``json.JSONDecodeError`` for diagnostics.
        """
        text = text.strip()

        # Strip common LLM wrapper patterns: ```json ... ``` or ``` ... ```
        text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE)
        text = re.sub(r"\s*```$", "", text)
        text = text.strip()

        # Fast path: already valid.
        try:
            parsed = json.loads(text)
            return RepairResult(
                original_text=text,
                repaired_text=text,
                parsed=parsed,
                transformations_applied=[],
                was_modified=False,
            )
        except json.JSONDecodeError:
            pass

        applied: list[str] = []
        current = text
        last_error: json.JSONDecodeError | None = None

        for fn in _REPAIRS:
            candidate = fn(current)
            try:
                parsed = json.loads(candidate)
                applied.append(fn.__name__)
                return RepairResult(
                    original_text=text,
                    repaired_text=candidate,
                    parsed=parsed,
                    transformations_applied=applied,
                    was_modified=True,
                )
            except json.JSONDecodeError as exc:
                if candidate != current:
                    # The transformation produced a change — keep it even though
                    # it didn't fix things yet, so subsequent transforms build on it.
                    applied.append(fn.__name__)
                    current = candidate
                last_error = exc

        # All repairs exhausted — return parse failure with context.
        error_detail = (
            f"line {last_error.lineno}, col {last_error.colno}: {last_error.msg}"
            if last_error
            else "unknown parse error"
        )
        raise IMLParseError(
            f"IMLRepair failed after {len(applied)} transformation(s).  "
            f"Last JSON error: {error_detail}"
        )


# ---------------------------------------------------------------------------
# LLM correction prompt formatter
# ---------------------------------------------------------------------------


class CorrectionPromptFormatter:
    """Formats a structured correction request for the LLM.

    The formatted output is plain-text so it can be appended to any prompt
    without escaping or further structuring.

    Usage::

        formatter = CorrectionPromptFormatter()
        repair = IMLRepair()
        try:
            result = repair.repair(raw)
        except IMLParseError as parse_err:
            prompt_suffix = formatter.format_parse_error(raw, parse_err)
            # Send (original_prompt + prompt_suffix) back to the LLM.

        from pydantic import ValidationError
        try:
            plan = IMLParser().parse(result.parsed)
        except IMLValidationError as val_err:
            prompt_suffix = formatter.format_validation_error(raw, val_err)
            # Send (original_prompt + prompt_suffix) back to the LLM.
    """

    _HEADER = (
        "\n\n--- LLMOS BRIDGE CORRECTION REQUEST ---\n"
        "Your previous response contained an error in the IML plan.\n"
        "Please fix ONLY the reported issue and return the corrected plan.\n"
        "Do not change any other part of the plan.\n\n"
    )
    _FOOTER = "\n--- END CORRECTION REQUEST ---\n"

    def format_parse_error(
        self,
        original: str,
        error: Exception,
        hint: str | None = None,
    ) -> str:
        """Format a JSON parse error correction request.

        Args:
            original: The raw string the LLM produced.
            error:    The exception (IMLParseError or JSONDecodeError).
            hint:     Optional extra guidance to include.
        """
        lines = [self._HEADER]
        lines.append("ERROR TYPE: JSON syntax error\n")
        lines.append(f"ERROR: {error}\n")

        if hasattr(error, "lineno") and hasattr(error, "colno"):
            lines.append(f"LOCATION: line {error.lineno}, column {error.colno}\n")  # type: ignore[attr-defined]
            context_lines = original.splitlines()
            bad_line = error.lineno - 1  # type: ignore[attr-defined]
            if 0 <= bad_line < len(context_lines):
                lines.append(f"CONTEXT:  {context_lines[bad_line]}\n")

        lines.append(
            "\nCOMMON FIXES:\n"
            "  - Remove trailing commas before } or ]\n"
            "  - Use double quotes for all strings and keys\n"
            "  - Replace Python True/False/None with JSON true/false/null\n"
            "  - Do not add comments (// or /* */)\n"
            "  - Ensure all opened {{ and [ are closed\n"
        )
        if hint:
            lines.append(f"\nADDITIONAL HINT: {hint}\n")

        lines.append(self._FOOTER)
        return "".join(lines)

    def format_validation_error(
        self,
        original: str,
        error: Exception,
        hint: str | None = None,
    ) -> str:
        """Format a Pydantic validation error correction request.

        Args:
            original: The raw string or dict the LLM produced.
            error:    The exception (IMLValidationError or pydantic.ValidationError).
            hint:     Optional extra guidance to include.
        """
        lines = [self._HEADER]
        lines.append("ERROR TYPE: IML schema validation error\n")
        lines.append(f"ERROR: {error}\n")

        # If it's a pydantic ValidationError, extract field-level details.
        errors_detail = getattr(error, "errors", None)
        if callable(errors_detail):
            try:
                for pydantic_err in errors_detail():
                    loc = " -> ".join(str(l) for l in pydantic_err.get("loc", []))
                    msg = pydantic_err.get("msg", "")
                    lines.append(f"  FIELD: {loc}\n  REASON: {msg}\n")
            except Exception:
                pass

        lines.append(
            "\nCOMMON FIXES:\n"
            "  - Ensure 'protocol_version' is exactly \"2.0\"\n"
            "  - Each action must have: id, action, module, params fields\n"
            "  - Action 'id' must match [a-zA-Z0-9_-] and be unique\n"
            "  - 'module' must be lowercase snake_case (e.g. 'filesystem')\n"
            "  - 'depends_on' must reference existing action IDs\n"
            "  - 'on_error' must be one of: abort, continue, retry, rollback, skip\n"
        )
        if hint:
            lines.append(f"\nADDITIONAL HINT: {hint}\n")

        lines.append(self._FOOTER)
        return "".join(lines)
