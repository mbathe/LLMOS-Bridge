"""Unit tests — SoMRenderer (Set-of-Marks overlay)."""

from __future__ import annotations

import base64
from unittest.mock import MagicMock, patch

import pytest

from llmos_bridge.modules.perception_vision.base import VisionElement
from llmos_bridge.modules.perception_vision.ultra.som import (
    COLOR_MAP,
    DEFAULT_COLOR,
    SoMRenderer,
)


def _make_element(
    element_id: str = "e0",
    label: str = "Submit",
    element_type: str = "button",
    bbox: tuple[float, float, float, float] = (0.1, 0.1, 0.3, 0.15),
) -> VisionElement:
    return VisionElement(
        element_id=element_id,
        label=label,
        element_type=element_type,
        bbox=bbox,
        confidence=0.9,
        text=label,
        interactable=True,
    )


@pytest.fixture
def renderer() -> SoMRenderer:
    return SoMRenderer()


@pytest.fixture
def mock_image() -> MagicMock:
    """Create a mock PIL Image."""
    img = MagicMock()
    img.size = (1920, 1080)
    img.copy.return_value = img
    img.convert.return_value = img
    return img


# ---------------------------------------------------------------------------
# Color map tests
# ---------------------------------------------------------------------------

@pytest.mark.unit
class TestColorMap:
    def test_all_types_have_colors(self) -> None:
        for element_type in ("button", "input", "text", "icon", "link", "checkbox"):
            assert element_type in COLOR_MAP

    def test_default_color_exists(self) -> None:
        assert DEFAULT_COLOR == (200, 200, 200)

    def test_colors_are_rgb_tuples(self) -> None:
        for name, color in COLOR_MAP.items():
            assert isinstance(color, tuple)
            assert len(color) == 3
            for c in color:
                assert 0 <= c <= 255


# ---------------------------------------------------------------------------
# Renderer tests
# ---------------------------------------------------------------------------

@pytest.mark.unit
class TestSoMRenderer:
    def test_render_returns_image(self, renderer: SoMRenderer) -> None:
        """Test that render works with a real PIL Image (small test image)."""
        try:
            from PIL import Image
        except ImportError:
            pytest.skip("PIL not available")

        img = Image.new("RGB", (200, 100), color=(255, 255, 255))
        elements = [_make_element()]
        result = renderer.render(img, elements)
        assert result.size == (200, 100)

    def test_render_empty_elements(self, renderer: SoMRenderer) -> None:
        try:
            from PIL import Image
        except ImportError:
            pytest.skip("PIL not available")

        img = Image.new("RGB", (200, 100), color=(255, 255, 255))
        result = renderer.render(img, [])
        assert result.size == (200, 100)

    def test_render_multiple_elements(self, renderer: SoMRenderer) -> None:
        try:
            from PIL import Image
        except ImportError:
            pytest.skip("PIL not available")

        img = Image.new("RGB", (400, 300), color=(255, 255, 255))
        elements = [
            _make_element("e0", "OK", "button", (0.1, 0.1, 0.3, 0.2)),
            _make_element("e1", "Search", "input", (0.4, 0.1, 0.9, 0.2)),
            _make_element("e2", "Home", "icon", (0.05, 0.05, 0.1, 0.1)),
        ]
        result = renderer.render(img, elements)
        assert result.size == (400, 300)

    def test_render_to_base64(self, renderer: SoMRenderer) -> None:
        try:
            from PIL import Image
        except ImportError:
            pytest.skip("PIL not available")

        img = Image.new("RGB", (200, 100), color=(255, 255, 255))
        elements = [_make_element()]
        b64 = renderer.render_to_base64(img, elements)
        assert isinstance(b64, str)
        # Should be valid base64.
        decoded = base64.b64decode(b64)
        assert len(decoded) > 0

    def test_render_to_base64_is_png(self, renderer: SoMRenderer) -> None:
        try:
            from PIL import Image
        except ImportError:
            pytest.skip("PIL not available")

        img = Image.new("RGB", (100, 100), color=(0, 0, 0))
        b64 = renderer.render_to_base64(img, [_make_element()])
        decoded = base64.b64decode(b64)
        # PNG magic bytes.
        assert decoded[:4] == b"\x89PNG"

    def test_custom_colors(self) -> None:
        try:
            from PIL import Image
        except ImportError:
            pytest.skip("PIL not available")

        custom = {"button": (255, 0, 0)}
        renderer = SoMRenderer(colors=custom)
        img = Image.new("RGB", (200, 100), color=(255, 255, 255))
        # Should not raise.
        result = renderer.render(img, [_make_element()])
        assert result.size == (200, 100)

    def test_custom_line_width(self) -> None:
        renderer = SoMRenderer(line_width=4)
        assert renderer._line_width == 4

    def test_custom_font_size(self) -> None:
        renderer = SoMRenderer(font_size=16)
        assert renderer._font_size == 16

    def test_render_preserves_original(self, renderer: SoMRenderer) -> None:
        """Ensure the original image is not modified."""
        try:
            from PIL import Image
        except ImportError:
            pytest.skip("PIL not available")

        img = Image.new("RGB", (200, 100), color=(128, 128, 128))
        original_pixel = img.getpixel((0, 0))
        renderer.render(img, [_make_element()])
        assert img.getpixel((0, 0)) == original_pixel
