"""Element resolution — semantic matching engine.

Converts natural language descriptions ("the Validate button", "email input field")
into concrete pixel coordinates by querying the vision module and applying
multi-strategy matching: exact, substring, text, fuzzy, type+label.

Design:
  - Does NOT import pyautogui or torch — works with any backend.
  - Receives VisionParseResult as input (from any BaseVisionModule).
  - Returns ResolvedElement or None (caller decides whether to retry/error).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from difflib import SequenceMatcher

from llmos_bridge.modules.perception_vision.base import VisionElement, VisionParseResult


@dataclass
class ResolvedElement:
    """An element resolved to pixel coordinates."""

    element: VisionElement
    pixel_x: int
    pixel_y: int
    confidence: float
    match_strategy: str  # "exact", "substring", "text_match", "fuzzy"
    all_candidates: int
    alternatives: list[VisionElement] = field(default_factory=list)


class ElementResolver:
    """Multi-strategy element resolver.

    Resolution order:
    1. Exact label match (case-insensitive)
    2. Substring match on label
    3. Substring match on OCR text within element
    4. Fuzzy match (Levenshtein distance above threshold)

    When multiple matches exist:
    - Prioritize interactable elements
    - Prioritize by confidence score
    - Return top match + alternatives list for LLM feedback
    """

    def __init__(self, fuzzy_threshold: float = 0.6) -> None:
        self._fuzzy_threshold = fuzzy_threshold

    def resolve(
        self,
        query: str,
        parse_result: VisionParseResult,
        *,
        element_type: str | None = None,
        prefer_interactable: bool = True,
    ) -> ResolvedElement | None:
        """Resolve a natural language query to a specific element.

        Returns None if no match found (caller decides whether to retry/error).
        """
        candidates = list(parse_result.elements)

        # Apply type filter if specified.
        if element_type:
            type_filtered = [e for e in candidates if e.element_type == element_type]
            if type_filtered:
                candidates = type_filtered

        total = len(candidates)

        # Strategy 1: exact match on label.
        exact = [e for e in candidates if e.label.lower() == query.lower()]
        if exact:
            return self._best_match(exact, parse_result, "exact", total)

        # Strategy 2: substring in label.
        substr = [e for e in candidates if query.lower() in e.label.lower()]
        if substr:
            return self._best_match(substr, parse_result, "substring", total)

        # Strategy 3: substring in OCR text within element.
        text_match = [
            e for e in candidates
            if e.text and query.lower() in e.text.lower()
        ]
        if text_match:
            return self._best_match(text_match, parse_result, "text_match", total)

        # Strategy 4: fuzzy match.
        fuzzy = self._fuzzy_search(query, candidates)
        if fuzzy:
            return self._best_match(fuzzy, parse_result, "fuzzy", total)

        return None

    def _best_match(
        self,
        matches: list[VisionElement],
        parse_result: VisionParseResult,
        strategy: str,
        total_candidates: int,
    ) -> ResolvedElement:
        # Sort: interactable first, then by confidence descending.
        sorted_matches = sorted(
            matches,
            key=lambda e: (e.interactable, e.confidence),
            reverse=True,
        )
        best = sorted_matches[0]
        px, py = best.pixel_center(parse_result.width, parse_result.height)
        return ResolvedElement(
            element=best,
            pixel_x=px,
            pixel_y=py,
            confidence=best.confidence,
            match_strategy=strategy,
            all_candidates=total_candidates,
            alternatives=sorted_matches[1:4],  # Top 3 alternatives
        )

    def _fuzzy_search(
        self, query: str, candidates: list[VisionElement]
    ) -> list[VisionElement]:
        scored: list[tuple[float, VisionElement]] = []
        q = query.lower()
        for elem in candidates:
            ratio = SequenceMatcher(None, q, elem.label.lower()).ratio()
            if ratio >= self._fuzzy_threshold:
                scored.append((ratio, elem))

        scored.sort(key=lambda x: x[0], reverse=True)
        return [elem for _, elem in scored]
