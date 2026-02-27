"""Unit tests — ElementResolver (pure logic, no mocks needed)."""

from __future__ import annotations

import pytest

from llmos_bridge.modules.computer_control.resolution import ElementResolver, ResolvedElement
from llmos_bridge.modules.perception_vision.base import VisionElement, VisionParseResult


def _make_element(
    element_id: str = "e0",
    label: str = "Submit",
    element_type: str = "button",
    bbox: tuple[float, float, float, float] = (0.5, 0.5, 0.6, 0.55),
    confidence: float = 0.9,
    text: str | None = None,
    interactable: bool = True,
) -> VisionElement:
    return VisionElement(
        element_id=element_id,
        label=label,
        element_type=element_type,
        bbox=bbox,
        confidence=confidence,
        text=text,
        interactable=interactable,
    )


def _make_parse_result(elements: list[VisionElement]) -> VisionParseResult:
    return VisionParseResult(
        elements=elements,
        width=1920,
        height=1080,
        raw_ocr="",
        labeled_image_b64=None,
        parse_time_ms=100.0,
        model_id="test",
    )


@pytest.mark.unit
class TestExactMatch:
    def test_exact_label_match(self) -> None:
        pr = _make_parse_result([_make_element(label="Submit")])
        resolver = ElementResolver()
        result = resolver.resolve("Submit", pr)
        assert result is not None
        assert result.match_strategy == "exact"

    def test_case_insensitive(self) -> None:
        pr = _make_parse_result([_make_element(label="SUBMIT")])
        result = ElementResolver().resolve("submit", pr)
        assert result is not None
        assert result.match_strategy == "exact"

    def test_exact_over_substring(self) -> None:
        pr = _make_parse_result([
            _make_element(element_id="e0", label="Submit Form"),
            _make_element(element_id="e1", label="Submit"),
        ])
        result = ElementResolver().resolve("Submit", pr)
        assert result is not None
        assert result.match_strategy == "exact"
        assert result.element.element_id == "e1"


@pytest.mark.unit
class TestSubstringMatch:
    def test_label_substring(self) -> None:
        pr = _make_parse_result([_make_element(label="Submit Form")])
        result = ElementResolver().resolve("Submit", pr)
        assert result is not None
        assert result.match_strategy == "substring"

    def test_query_in_label(self) -> None:
        pr = _make_parse_result([_make_element(label="Click here to Submit")])
        result = ElementResolver().resolve("Submit", pr)
        assert result is not None
        assert result.match_strategy == "substring"


@pytest.mark.unit
class TestTextMatch:
    def test_ocr_text_match(self) -> None:
        pr = _make_parse_result([
            _make_element(label="btn_1", text="Save Changes"),
        ])
        result = ElementResolver().resolve("Save", pr)
        assert result is not None
        assert result.match_strategy == "text_match"

    def test_text_match_case_insensitive(self) -> None:
        pr = _make_parse_result([
            _make_element(label="icon_x", text="CLOSE WINDOW"),
        ])
        result = ElementResolver().resolve("close window", pr)
        assert result is not None


@pytest.mark.unit
class TestFuzzyMatch:
    def test_fuzzy_match_high_similarity(self) -> None:
        # "Sbumt" is similar to "Submit" but NOT a substring.
        pr = _make_parse_result([_make_element(label="Sbumt")])
        result = ElementResolver(fuzzy_threshold=0.5).resolve("Submit", pr)
        assert result is not None
        assert result.match_strategy == "fuzzy"

    def test_fuzzy_match_below_threshold(self) -> None:
        pr = _make_parse_result([_make_element(label="XYZ")])
        result = ElementResolver(fuzzy_threshold=0.6).resolve("Submit", pr)
        assert result is None


@pytest.mark.unit
class TestTypeFilter:
    def test_filter_by_type(self) -> None:
        pr = _make_parse_result([
            _make_element(element_id="e0", label="Submit", element_type="text"),
            _make_element(element_id="e1", label="Submit", element_type="button"),
        ])
        result = ElementResolver().resolve("Submit", pr, element_type="button")
        assert result is not None
        assert result.element.element_id == "e1"

    def test_filter_fallback_when_no_type_match(self) -> None:
        pr = _make_parse_result([
            _make_element(element_id="e0", label="Submit", element_type="text"),
        ])
        result = ElementResolver().resolve("Submit", pr, element_type="button")
        # When type filter yields no results, falls back to all candidates
        assert result is not None


@pytest.mark.unit
class TestPriorityAndAlternatives:
    def test_interactable_preferred(self) -> None:
        pr = _make_parse_result([
            _make_element(element_id="e0", label="Submit", interactable=False, confidence=0.95),
            _make_element(element_id="e1", label="Submit", interactable=True, confidence=0.8),
        ])
        result = ElementResolver().resolve("Submit", pr)
        assert result is not None
        assert result.element.element_id == "e1"

    def test_higher_confidence_preferred(self) -> None:
        pr = _make_parse_result([
            _make_element(element_id="e0", label="Submit", confidence=0.7),
            _make_element(element_id="e1", label="Submit", confidence=0.95),
        ])
        result = ElementResolver().resolve("Submit", pr)
        assert result is not None
        assert result.element.element_id == "e1"

    def test_alternatives_populated(self) -> None:
        # All 3 match "Submit" exactly (same label) so alternatives appear.
        pr = _make_parse_result([
            _make_element(element_id="e0", label="Submit", confidence=0.95),
            _make_element(element_id="e1", label="Submit", confidence=0.8),
            _make_element(element_id="e2", label="Submit", confidence=0.7),
        ])
        result = ElementResolver().resolve("Submit", pr)
        assert result is not None
        assert result.element.element_id == "e0"
        assert len(result.alternatives) == 2

    def test_max_three_alternatives(self) -> None:
        elems = [
            _make_element(element_id=f"e{i}", label="Submit", confidence=0.9 - i * 0.1)
            for i in range(6)
        ]
        pr = _make_parse_result(elems)
        result = ElementResolver().resolve("Submit", pr)
        assert result is not None
        assert len(result.alternatives) <= 3


@pytest.mark.unit
class TestPixelCoordinates:
    def test_pixel_center_calculation(self) -> None:
        pr = _make_parse_result([
            _make_element(bbox=(0.5, 0.5, 0.6, 0.6)),
        ])
        result = ElementResolver().resolve("Submit", pr)
        assert result is not None
        # Center of (0.5, 0.5, 0.6, 0.6) on 1920x1080 = (1056, 594)
        assert result.pixel_x == round(0.55 * 1920)
        assert result.pixel_y == round(0.55 * 1080)


@pytest.mark.unit
class TestNoMatch:
    def test_returns_none_when_no_elements(self) -> None:
        pr = _make_parse_result([])
        result = ElementResolver().resolve("Submit", pr)
        assert result is None

    def test_returns_none_when_no_match(self) -> None:
        pr = _make_parse_result([_make_element(label="Cancel")])
        result = ElementResolver(fuzzy_threshold=0.9).resolve("Submit", pr)
        assert result is None

    def test_all_candidates_count(self) -> None:
        pr = _make_parse_result([
            _make_element(element_id="e0", label="Submit"),
            _make_element(element_id="e1", label="Cancel"),
            _make_element(element_id="e2", label="Help"),
        ])
        result = ElementResolver().resolve("Submit", pr)
        assert result is not None
        assert result.all_candidates == 3
