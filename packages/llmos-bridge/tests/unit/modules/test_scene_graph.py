"""Unit tests — SceneGraphBuilder (hierarchical perception)."""

from __future__ import annotations

import pytest

from llmos_bridge.modules.perception_vision.base import VisionElement, VisionParseResult
from llmos_bridge.modules.perception_vision.scene_graph import (
    RegionType,
    SceneGraph,
    SceneGraphBuilder,
    ScreenRegion,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _elem(
    eid: str,
    label: str,
    bbox: tuple[float, float, float, float],
    etype: str = "text",
    interactable: bool = False,
) -> VisionElement:
    return VisionElement(
        element_id=eid,
        label=label,
        element_type=etype,
        bbox=bbox,
        confidence=1.0,
        interactable=interactable,
    )


def _parse_result(elements: list[VisionElement]) -> VisionParseResult:
    return VisionParseResult(
        elements=elements,
        width=1920,
        height=1080,
        parse_time_ms=100.0,
        model_id="test",
    )


# ---------------------------------------------------------------------------
# SceneGraphBuilder — basic
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestSceneGraphBuilder:
    def test_empty_elements(self):
        builder = SceneGraphBuilder()
        result = builder.build(_parse_result([]))
        assert result.element_count == 0
        assert result.region_count == 0

    def test_taskbar_detection(self):
        """Elements at bottom 5% should be in TASKBAR."""
        elems = [
            _elem("e1", "Activities", (0.0, 0.96, 0.1, 0.99), "icon", True),
            _elem("e2", "Clock", (0.4, 0.96, 0.6, 0.99), "text"),
            _elem("e3", "Content", (0.2, 0.3, 0.8, 0.7), "text"),
        ]
        builder = SceneGraphBuilder()
        graph = builder.build(_parse_result(elems))

        taskbar_regions = [r for r in graph.regions if r.region_type == RegionType.TASKBAR]
        assert len(taskbar_regions) == 1
        assert len(taskbar_regions[0].elements) == 2

    def test_window_region_created(self):
        """Non-taskbar elements should be in a WINDOW region."""
        elems = [
            _elem("e1", "Title", (0.0, 0.0, 1.0, 0.03), "text"),
            _elem("e2", "Content", (0.2, 0.3, 0.8, 0.7), "text"),
        ]
        builder = SceneGraphBuilder()
        graph = builder.build(_parse_result(elems))

        window_regions = [r for r in graph.regions if r.region_type == RegionType.WINDOW]
        assert len(window_regions) == 1
        assert graph.active_window is not None
        assert graph.active_window.is_active is True

    def test_title_bar_detection(self):
        """Elements in top 4% should be detected as TITLE_BAR."""
        elems = [
            _elem("e1", "File", (0.0, 0.0, 0.05, 0.03), "text"),
            _elem("e2", "Edit", (0.06, 0.0, 0.1, 0.03), "text"),
            _elem("e3", "Content", (0.2, 0.3, 0.8, 0.7), "text"),
        ]
        builder = SceneGraphBuilder()
        graph = builder.build(_parse_result(elems))

        assert graph.active_window is not None
        title_bars = [c for c in graph.active_window.children if c.region_type == RegionType.TITLE_BAR]
        assert len(title_bars) == 1
        assert len(title_bars[0].elements) == 2

    def test_toolbar_detection(self):
        """Cluster of elements below title bar → TOOLBAR."""
        elems = [
            _elem("e1", "Window Title", (0.0, 0.0, 1.0, 0.03), "text"),
            _elem("e2", "Back", (0.0, 0.05, 0.05, 0.09), "button", True),
            _elem("e3", "Forward", (0.06, 0.05, 0.11, 0.09), "button", True),
            _elem("e4", "URL", (0.12, 0.05, 0.9, 0.09), "input", True),
            _elem("e5", "Content", (0.2, 0.3, 0.8, 0.7), "text"),
        ]
        builder = SceneGraphBuilder()
        graph = builder.build(_parse_result(elems))

        assert graph.active_window is not None
        toolbars = [c for c in graph.active_window.children if c.region_type == RegionType.TOOLBAR]
        assert len(toolbars) == 1
        assert len(toolbars[0].elements) >= 2

    def test_sidebar_detection(self):
        """Elements in left 25% → SIDEBAR (if >= 3 elements)."""
        elems = [
            _elem("e0", "Title", (0.0, 0.0, 1.0, 0.03), "text"),
            _elem("e1", "Nav1", (0.01, 0.20, 0.15, 0.25), "icon", True),
            _elem("e2", "Nav2", (0.01, 0.30, 0.15, 0.35), "icon", True),
            _elem("e3", "Nav3", (0.01, 0.40, 0.15, 0.45), "icon", True),
            _elem("e4", "Content", (0.3, 0.3, 0.9, 0.8), "text"),
        ]
        builder = SceneGraphBuilder()
        graph = builder.build(_parse_result(elems))

        assert graph.active_window is not None
        sidebars = [c for c in graph.active_window.children if c.region_type == RegionType.SIDEBAR]
        assert len(sidebars) == 1
        assert len(sidebars[0].elements) == 3

    def test_form_detection(self):
        """Cluster of input elements → FORM."""
        elems = [
            _elem("e1", "Username", (0.3, 0.3, 0.7, 0.35), "input", True),
            _elem("e2", "Password", (0.3, 0.37, 0.7, 0.42), "input", True),
            _elem("e3", "Login label", (0.3, 0.28, 0.5, 0.3), "text"),
            _elem("e4", "Footer", (0.2, 0.9, 0.8, 0.94), "text"),
        ]
        builder = SceneGraphBuilder()
        graph = builder.build(_parse_result(elems))

        assert graph.active_window is not None
        forms = [c for c in graph.active_window.children if c.region_type == RegionType.FORM]
        assert len(forms) == 1
        # Form should include inputs + nearby label.
        assert len(forms[0].elements) >= 2

    def test_content_area_fallback(self):
        """Unassigned elements go to CONTENT_AREA."""
        elems = [
            _elem("e1", "Some content", (0.3, 0.4, 0.7, 0.6), "text"),
        ]
        builder = SceneGraphBuilder()
        graph = builder.build(_parse_result(elems))

        assert graph.active_window is not None
        content = [c for c in graph.active_window.children if c.region_type == RegionType.CONTENT_AREA]
        assert len(content) == 1

    def test_active_window_label(self):
        """Window label is set from active_window_title param."""
        elems = [_elem("e1", "X", (0.1, 0.1, 0.9, 0.9), "text")]
        builder = SceneGraphBuilder()
        graph = builder.build(_parse_result(elems), active_window_title="Firefox")

        assert graph.active_window is not None
        assert graph.active_window.label == "Firefox"

    def test_build_time_recorded(self):
        elems = [_elem("e1", "X", (0.1, 0.1, 0.9, 0.9), "text")]
        builder = SceneGraphBuilder()
        graph = builder.build(_parse_result(elems))
        assert graph.build_time_ms > 0


# ---------------------------------------------------------------------------
# SceneGraph — compact text output
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestCompactText:
    def test_empty_graph(self):
        graph = SceneGraph()
        assert graph.to_compact_text() == ""

    def test_simple_output(self):
        elems = [
            _elem("e1", "Back", (0.0, 0.05, 0.05, 0.09), "button", True),
            _elem("e2", "Content text", (0.2, 0.3, 0.8, 0.7), "text"),
        ]
        builder = SceneGraphBuilder()
        graph = builder.build(_parse_result(elems))
        text = graph.to_compact_text()

        assert "[WINDOW]" in text
        assert "Back" in text
        assert "Content text" in text

    def test_focused_marker(self):
        elems = [_elem("e1", "X", (0.1, 0.1, 0.9, 0.9), "text")]
        builder = SceneGraphBuilder()
        graph = builder.build(_parse_result(elems))
        text = graph.to_compact_text()
        assert "(focused)" in text

    def test_interactable_marker(self):
        elems = [_elem("e1", "Button", (0.3, 0.3, 0.7, 0.5), "button", True)]
        builder = SceneGraphBuilder()
        graph = builder.build(_parse_result(elems))
        text = graph.to_compact_text()
        assert "[INTERACTABLE]" in text

    def test_window_label_in_output(self):
        elems = [_elem("e1", "X", (0.1, 0.1, 0.9, 0.9), "text")]
        builder = SceneGraphBuilder()
        graph = builder.build(_parse_result(elems), active_window_title="Firefox")
        text = graph.to_compact_text()
        assert "Firefox" in text


# ---------------------------------------------------------------------------
# Geometry helpers
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestGeometryHelpers:
    def test_bounding_box(self):
        elems = [
            _elem("e1", "A", (0.1, 0.2, 0.3, 0.4)),
            _elem("e2", "B", (0.5, 0.1, 0.9, 0.8)),
        ]
        bbox = SceneGraphBuilder._bounding_box(elems)
        assert bbox == (0.1, 0.1, 0.9, 0.8)

    def test_bbox_overlap_true(self):
        assert SceneGraphBuilder._bbox_overlap(
            (0.0, 0.0, 0.5, 0.5),
            (0.3, 0.3, 0.8, 0.8),
        )

    def test_bbox_overlap_false(self):
        assert not SceneGraphBuilder._bbox_overlap(
            (0.0, 0.0, 0.2, 0.2),
            (0.5, 0.5, 0.8, 0.8),
        )

    def test_bbox_center(self):
        cx, cy = SceneGraphBuilder._bbox_center((0.0, 0.0, 1.0, 1.0))
        assert cx == 0.5
        assert cy == 0.5

    def test_distance(self):
        d = SceneGraphBuilder._distance((0.0, 0.0), (3.0, 4.0))
        assert d == pytest.approx(5.0)

    def test_cluster_elements(self):
        builder = SceneGraphBuilder()
        elems = [
            _elem("e1", "A", (0.1, 0.1, 0.2, 0.2)),
            _elem("e2", "B", (0.12, 0.12, 0.22, 0.22)),
            _elem("e3", "C", (0.8, 0.8, 0.9, 0.9)),
        ]
        clusters = builder._cluster_elements(elems, max_dist=0.1)
        assert len(clusters) == 2


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestDataClasses:
    def test_region_type_values(self):
        assert RegionType.WINDOW.value == "window"
        assert RegionType.TASKBAR.value == "taskbar"
        assert RegionType.FORM.value == "form"

    def test_screen_region_defaults(self):
        region = ScreenRegion(
            region_id="r001",
            region_type=RegionType.WINDOW,
            bbox=(0, 0, 1, 1),
        )
        assert region.elements == []
        assert region.children == []
        assert region.is_active is False
        assert region.depth == 0

    def test_scene_graph_defaults(self):
        graph = SceneGraph()
        assert graph.regions == []
        assert graph.active_window is None
        assert graph.element_count == 0

    def test_full_screen_layout(self):
        """Integration test: full desktop layout with all region types."""
        elems = [
            # Title bar
            _elem("e01", "App Menu", (0.0, 0.0, 0.08, 0.03), "text"),
            _elem("e02", "Close", (0.95, 0.0, 1.0, 0.03), "button", True),
            # Toolbar
            _elem("e03", "Back", (0.0, 0.04, 0.04, 0.08), "button", True),
            _elem("e04", "URL bar", (0.1, 0.04, 0.85, 0.08), "input", True),
            # Sidebar
            _elem("e05", "Home", (0.0, 0.12, 0.12, 0.17), "icon", True),
            _elem("e06", "Files", (0.0, 0.18, 0.12, 0.23), "icon", True),
            _elem("e07", "Settings", (0.0, 0.24, 0.12, 0.29), "icon", True),
            # Content
            _elem("e08", "Welcome", (0.2, 0.3, 0.8, 0.5), "text"),
            _elem("e09", "Search", (0.3, 0.55, 0.7, 0.6), "input", True),
            # Taskbar
            _elem("e10", "Activities", (0.0, 0.96, 0.08, 0.99), "icon", True),
            _elem("e11", "Clock", (0.4, 0.96, 0.6, 0.99), "text"),
        ]
        builder = SceneGraphBuilder()
        graph = builder.build(_parse_result(elems), active_window_title="Firefox")

        # All elements accounted for.
        assert graph.element_count == 11
        assert graph.region_count >= 4  # At least: window, title, toolbar, taskbar + more

        # Compact text renders without error.
        text = graph.to_compact_text()
        assert len(text) > 50
        assert "Firefox" in text
        assert "TASKBAR" in text
