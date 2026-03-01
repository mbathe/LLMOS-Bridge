"""Scene Graph — Hierarchical spatial perception for GUI understanding.

Converts a flat ``list[VisionElement]`` from OmniParser into a hierarchical
tree of ``ScreenRegion`` objects, giving the LLM structural context:

  - "Submit button is inside the login form"
  - "URL bar is in the toolbar"
  - "Search input is in the content area"

The ``SceneGraphBuilder`` is pure CPU geometry — adds ~5-15ms after OmniParser's
GPU pipeline.  No heavy dependencies.

Output format (compact text for LLM consumption)::

    [WINDOW: Firefox] (focused)
      [TOOLBAR]
        button: "Back" | button: "Forward" | input: "URL bar"
      [CONTENT_AREA]
        text: "Welcome to Google"
        input: "Search"
    [TASKBAR]
      icon: "Activities"
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum

from llmos_bridge.modules.perception_vision.base import VisionElement, VisionParseResult


class RegionType(str, Enum):
    """Semantic region type detected on screen."""

    WINDOW = "window"
    TITLE_BAR = "title_bar"
    MENU_BAR = "menu_bar"
    TOOLBAR = "toolbar"
    SIDEBAR = "sidebar"
    CONTENT_AREA = "content_area"
    STATUS_BAR = "status_bar"
    TASKBAR = "taskbar"
    DIALOG = "dialog"
    FORM = "form"
    UNKNOWN = "unknown"


@dataclass
class ScreenRegion:
    """A spatial region containing UI elements."""

    region_id: str
    region_type: RegionType
    bbox: tuple[float, float, float, float]  # normalised (x1, y1, x2, y2)
    elements: list[VisionElement] = field(default_factory=list)
    children: list[ScreenRegion] = field(default_factory=list)
    label: str | None = None
    is_active: bool = False
    depth: int = 0


@dataclass
class SceneGraph:
    """Hierarchical scene representation."""

    regions: list[ScreenRegion] = field(default_factory=list)
    active_window: ScreenRegion | None = None
    element_count: int = 0
    region_count: int = 0
    build_time_ms: float = 0.0
    unassigned_elements: list[VisionElement] = field(default_factory=list)

    def to_compact_text(self, max_elements_per_region: int = 30) -> str:
        """Render the scene graph as compact text for LLM consumption.

        Format::

            [WINDOW: Firefox] (focused)
              [TOOLBAR]
                button: "Back" | button: "Forward"
              [CONTENT_AREA]
                text: "Welcome"
                input: "Search"
        """
        lines: list[str] = []
        for region in self.regions:
            self._render_region(region, lines, indent=0, max_elem=max_elements_per_region)

        if self.unassigned_elements:
            lines.append("[UNASSIGNED]")
            for elem in self.unassigned_elements[:max_elements_per_region]:
                tag = "**" if elem.interactable else ""
                lines.append(f"  {elem.element_type}: \"{elem.label}\"{tag}")

        return "\n".join(lines)

    def _render_region(
        self,
        region: ScreenRegion,
        lines: list[str],
        indent: int,
        max_elem: int,
    ) -> None:
        prefix = "  " * indent
        label_part = f": {region.label}" if region.label else ""
        active_part = " (focused)" if region.is_active else ""
        lines.append(f"{prefix}[{region.region_type.value.upper()}{label_part}]{active_part}")

        # Render child regions first.
        for child in region.children:
            self._render_region(child, lines, indent + 1, max_elem)

        # Render elements in this region (not in child regions).
        child_elem_ids = set()
        for child in region.children:
            child_elem_ids.update(e.element_id for e in child.elements)

        own_elements = [e for e in region.elements if e.element_id not in child_elem_ids]

        # Group interactable elements on one line, text on separate lines.
        interactable = [e for e in own_elements if e.interactable]
        text_only = [e for e in own_elements if not e.interactable]

        child_prefix = "  " * (indent + 1)

        if interactable:
            for elem in interactable[:max_elem]:
                lines.append(f"{child_prefix}{elem.element_type}: \"{elem.label}\" [INTERACTABLE]")

        for elem in text_only[:max_elem]:
            lines.append(f"{child_prefix}{elem.element_type}: \"{elem.label}\"")


class SceneGraphBuilder:
    """Build a hierarchical scene graph from flat VisionElement lists.

    Pure CPU geometry — no ML models required. Adds ~5-15ms.

    Heuristics:
      - Bottom 5% of screen → TASKBAR
      - Top 3% of a large container → TITLE_BAR
      - Cluster of inputs close together → FORM
      - Left 15-25% with mostly icons/nav → SIDEBAR
      - Remaining center area → CONTENT_AREA
    """

    def __init__(
        self,
        taskbar_threshold: float = 0.95,
        title_bar_height: float = 0.04,
        sidebar_max_width: float = 0.25,
        form_input_cluster_dist: float = 0.08,
    ) -> None:
        self._taskbar_threshold = taskbar_threshold
        self._title_bar_height = title_bar_height
        self._sidebar_max_width = sidebar_max_width
        self._form_input_cluster_dist = form_input_cluster_dist

    def build(
        self,
        parse_result: VisionParseResult,
        active_window_title: str | None = None,
    ) -> SceneGraph:
        """Build scene graph from a VisionParseResult."""
        t0 = time.perf_counter()
        elements = list(parse_result.elements)

        if not elements:
            return SceneGraph(build_time_ms=(time.perf_counter() - t0) * 1000)

        regions: list[ScreenRegion] = []
        assigned: set[str] = set()
        region_counter = 0

        # 1. Detect taskbar (bottom strip).
        taskbar_elems = [
            e for e in elements
            if e.bbox[1] >= self._taskbar_threshold  # y1 in bottom 5%
        ]
        if taskbar_elems:
            region_counter += 1
            taskbar = ScreenRegion(
                region_id=f"r{region_counter:03d}",
                region_type=RegionType.TASKBAR,
                bbox=self._bounding_box(taskbar_elems),
                elements=taskbar_elems,
            )
            regions.append(taskbar)
            assigned.update(e.element_id for e in taskbar_elems)

        # 2. Main window area (everything above taskbar).
        main_elems = [e for e in elements if e.element_id not in assigned]
        if not main_elems:
            elapsed = (time.perf_counter() - t0) * 1000
            return SceneGraph(
                regions=regions,
                element_count=len(elements),
                region_count=len(regions),
                build_time_ms=elapsed,
            )

        # Create a main window region.
        region_counter += 1
        window_bbox = self._bounding_box(main_elems)
        window = ScreenRegion(
            region_id=f"r{region_counter:03d}",
            region_type=RegionType.WINDOW,
            bbox=window_bbox,
            elements=main_elems,
            is_active=True,
            label=active_window_title,
        )

        # 3. Detect title bar (top strip of window).
        window_y1 = window_bbox[1]
        title_bar_limit = window_y1 + self._title_bar_height
        title_elems = [
            e for e in main_elems
            if e.bbox[1] < title_bar_limit and e.bbox[3] < title_bar_limit + 0.02
        ]
        if title_elems:
            region_counter += 1
            title_bar = ScreenRegion(
                region_id=f"r{region_counter:03d}",
                region_type=RegionType.TITLE_BAR,
                bbox=self._bounding_box(title_elems),
                elements=title_elems,
                depth=1,
            )
            window.children.append(title_bar)
            assigned.update(e.element_id for e in title_elems)

        # 4. Detect toolbar (strip below title bar, above content).
        remaining = [e for e in main_elems if e.element_id not in assigned]
        if remaining:
            toolbar_limit = title_bar_limit + 0.06
            toolbar_elems = [
                e for e in remaining
                if e.bbox[1] < toolbar_limit
                and e.bbox[3] < toolbar_limit + 0.04
            ]
            if len(toolbar_elems) >= 2:
                region_counter += 1
                toolbar = ScreenRegion(
                    region_id=f"r{region_counter:03d}",
                    region_type=RegionType.TOOLBAR,
                    bbox=self._bounding_box(toolbar_elems),
                    elements=toolbar_elems,
                    depth=1,
                )
                window.children.append(toolbar)
                assigned.update(e.element_id for e in toolbar_elems)

        # 5. Detect sidebar (left narrow strip).
        remaining = [e for e in main_elems if e.element_id not in assigned]
        if remaining:
            sidebar_elems = [
                e for e in remaining
                if e.bbox[2] <= self._sidebar_max_width  # x2 within left 25%
            ]
            if len(sidebar_elems) >= 3:
                region_counter += 1
                sidebar = ScreenRegion(
                    region_id=f"r{region_counter:03d}",
                    region_type=RegionType.SIDEBAR,
                    bbox=self._bounding_box(sidebar_elems),
                    elements=sidebar_elems,
                    depth=1,
                )
                window.children.append(sidebar)
                assigned.update(e.element_id for e in sidebar_elems)

        # 6. Detect forms (clusters of input elements).
        remaining = [e for e in main_elems if e.element_id not in assigned]
        input_elems = [
            e for e in remaining
            if e.element_type in ("input", "checkbox", "select")
            or "input" in e.label.lower()
            or "field" in e.label.lower()
        ]
        if len(input_elems) >= 2:
            form_clusters = self._cluster_elements(
                input_elems, self._form_input_cluster_dist
            )
            for cluster in form_clusters:
                if len(cluster) >= 2:
                    # Also include nearby text labels.
                    form_bbox = self._bounding_box(cluster)
                    expanded_bbox = (
                        max(0.0, form_bbox[0] - 0.02),
                        max(0.0, form_bbox[1] - 0.02),
                        min(1.0, form_bbox[2] + 0.02),
                        min(1.0, form_bbox[3] + 0.02),
                    )
                    nearby_text = [
                        e for e in remaining
                        if e.element_id not in assigned
                        and self._bbox_overlap(e.bbox, expanded_bbox)
                    ]
                    all_form = list({e.element_id: e for e in cluster + nearby_text}.values())
                    region_counter += 1
                    form = ScreenRegion(
                        region_id=f"r{region_counter:03d}",
                        region_type=RegionType.FORM,
                        bbox=self._bounding_box(all_form),
                        elements=all_form,
                        depth=1,
                    )
                    window.children.append(form)
                    assigned.update(e.element_id for e in all_form)

        # 7. Status bar (bottom strip of window, above taskbar).
        remaining = [e for e in main_elems if e.element_id not in assigned]
        if remaining:
            window_y2 = window_bbox[3]
            status_threshold = window_y2 - 0.04
            status_elems = [
                e for e in remaining
                if e.bbox[1] >= status_threshold
            ]
            if status_elems:
                region_counter += 1
                status_bar = ScreenRegion(
                    region_id=f"r{region_counter:03d}",
                    region_type=RegionType.STATUS_BAR,
                    bbox=self._bounding_box(status_elems),
                    elements=status_elems,
                    depth=1,
                )
                window.children.append(status_bar)
                assigned.update(e.element_id for e in status_elems)

        # 8. Everything else → CONTENT_AREA.
        remaining = [e for e in main_elems if e.element_id not in assigned]
        if remaining:
            region_counter += 1
            content = ScreenRegion(
                region_id=f"r{region_counter:03d}",
                region_type=RegionType.CONTENT_AREA,
                bbox=self._bounding_box(remaining),
                elements=remaining,
                depth=1,
            )
            window.children.append(content)
            assigned.update(e.element_id for e in remaining)

        regions.insert(0, window)

        # Collect unassigned (should be empty after our pass).
        unassigned = [e for e in elements if e.element_id not in assigned]

        elapsed = (time.perf_counter() - t0) * 1000
        return SceneGraph(
            regions=regions,
            active_window=window,
            element_count=len(elements),
            region_count=region_counter,
            build_time_ms=elapsed,
            unassigned_elements=unassigned,
        )

    # ------------------------------------------------------------------
    # Geometry helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _bounding_box(elements: list[VisionElement]) -> tuple[float, float, float, float]:
        """Compute the encompassing bbox of a list of elements."""
        x1 = min(e.bbox[0] for e in elements)
        y1 = min(e.bbox[1] for e in elements)
        x2 = max(e.bbox[2] for e in elements)
        y2 = max(e.bbox[3] for e in elements)
        return (x1, y1, x2, y2)

    @staticmethod
    def _bbox_overlap(
        a: tuple[float, float, float, float],
        b: tuple[float, float, float, float],
    ) -> bool:
        """Check if two bboxes overlap."""
        return not (a[2] < b[0] or a[0] > b[2] or a[3] < b[1] or a[1] > b[3])

    @staticmethod
    def _bbox_center(bbox: tuple[float, float, float, float]) -> tuple[float, float]:
        return ((bbox[0] + bbox[2]) / 2, (bbox[1] + bbox[3]) / 2)

    @staticmethod
    def _distance(a: tuple[float, float], b: tuple[float, float]) -> float:
        return ((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2) ** 0.5

    def _cluster_elements(
        self, elements: list[VisionElement], max_dist: float
    ) -> list[list[VisionElement]]:
        """Simple greedy clustering based on center distance."""
        if not elements:
            return []

        used = [False] * len(elements)
        clusters: list[list[VisionElement]] = []

        for i, elem in enumerate(elements):
            if used[i]:
                continue
            cluster = [elem]
            used[i] = True
            center_i = self._bbox_center(elem.bbox)

            for j in range(i + 1, len(elements)):
                if used[j]:
                    continue
                center_j = self._bbox_center(elements[j].bbox)
                if self._distance(center_i, center_j) <= max_dist:
                    cluster.append(elements[j])
                    used[j] = True

            clusters.append(cluster)

        return clusters
