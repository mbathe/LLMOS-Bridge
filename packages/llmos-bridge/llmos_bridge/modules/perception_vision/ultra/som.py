"""Set-of-Marks (SoM) overlay renderer.

Draws numbered, colour-coded bounding boxes on screenshots so that
LLMs can reference elements by their SoM index number.  Each element
type gets a distinct colour for quick visual identification.

Uses PIL only — no OpenCV dependency.
"""

from __future__ import annotations

import base64
import io
from typing import Any

from llmos_bridge.modules.perception_vision.base import VisionElement


# Colours per element type: (R, G, B).
COLOR_MAP: dict[str, tuple[int, int, int]] = {
    "button": (0, 150, 255),     # Blue
    "input": (0, 200, 0),        # Green
    "text": (180, 180, 180),     # Gray
    "icon": (255, 165, 0),       # Orange
    "link": (148, 103, 189),     # Purple
    "checkbox": (255, 255, 0),   # Yellow
    "other": (200, 200, 200),    # Light gray
}

DEFAULT_COLOR = (200, 200, 200)


class SoMRenderer:
    """Draw Set-of-Marks numbered overlays on screenshots.

    Each element gets:
      - A colour-coded bounding box outline (2px)
      - A numbered label at the top-left corner
      - A semi-transparent fill (optional)
    """

    def __init__(
        self,
        colors: dict[str, tuple[int, int, int]] | None = None,
        line_width: int = 2,
        font_size: int = 12,
        fill_alpha: int = 30,
    ) -> None:
        self._colors = colors or COLOR_MAP
        self._line_width = line_width
        self._font_size = font_size
        self._fill_alpha = fill_alpha

    def render(self, image: Any, elements: list[VisionElement]) -> Any:
        """Draw SoM overlay on a copy of the image.

        Args:
            image: PIL Image (RGB).
            elements: List of VisionElement with normalised bboxes.

        Returns:
            New PIL Image with overlays drawn.
        """
        from PIL import Image, ImageDraw, ImageFont  # noqa: PLC0415

        img = image.copy().convert("RGBA")
        overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
        draw = ImageDraw.Draw(overlay)
        w, h = img.size

        # Try to load a monospace font.
        font = self._get_font()

        for idx, elem in enumerate(elements):
            x1, y1, x2, y2 = elem.bbox
            px1, py1, px2, py2 = int(x1 * w), int(y1 * h), int(x2 * w), int(y2 * h)

            color = self._colors.get(elem.element_type, DEFAULT_COLOR)

            # Semi-transparent fill.
            fill_color = (*color, self._fill_alpha)
            draw.rectangle([px1, py1, px2, py2], fill=fill_color)

            # Outline.
            outline_color = (*color, 200)
            draw.rectangle(
                [px1, py1, px2, py2],
                outline=outline_color,
                width=self._line_width,
            )

            # Numbered label.
            label = str(idx)
            label_w = len(label) * 8 + 6
            label_h = 16
            label_x = max(0, px1)
            label_y = max(0, py1 - label_h)

            # Label background.
            draw.rectangle(
                [label_x, label_y, label_x + label_w, label_y + label_h],
                fill=(*color, 220),
            )
            # Label text.
            draw.text(
                (label_x + 3, label_y + 1),
                label,
                fill=(255, 255, 255, 255),
                font=font,
            )

        # Composite.
        result = Image.alpha_composite(img, overlay)
        return result.convert("RGB")

    def render_to_base64(self, image: Any, elements: list[VisionElement]) -> str:
        """Render overlay and return as base64-encoded PNG string."""
        rendered = self.render(image, elements)
        buf = io.BytesIO()
        rendered.save(buf, format="PNG")
        return base64.b64encode(buf.getvalue()).decode()

    def _get_font(self) -> Any:
        """Try to load a font; fall back to PIL default."""
        from PIL import ImageFont  # noqa: PLC0415

        for font_path in [
            "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",
            "/usr/share/fonts/truetype/liberation/LiberationMono-Regular.ttf",
            "/usr/share/fonts/TTF/DejaVuSansMono.ttf",
        ]:
            try:
                return ImageFont.truetype(font_path, self._font_size)
            except (OSError, IOError):
                continue
        return ImageFont.load_default()
