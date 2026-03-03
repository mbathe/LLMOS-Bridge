"""Visual perception module layer.

Provides structured GUI understanding for LLMOS Bridge actions.
The default implementation uses OmniParser (Microsoft) to parse
screenshots into semantic UI elements.

Available backends:
    - ``OmniParserModule``: YOLO v8 + Florence-2 + EasyOCR (default)
    - ``UltraVisionModule``: UI-DETR-1 + PP-OCRv5 + UGround (GUI-trained)

Quick start::

    from llmos_bridge.modules.perception_vision import OmniParserModule

    registry.register(OmniParserModule)
    result = await module.execute("parse_screen", {"screenshot_path": "/tmp/screen.png"})

Swap the default:
    Any module that subclasses ``BaseVisionModule`` and is registered
    as ``"vision"`` will be used by the perception pipeline automatically.
    Set ``vision.backend = "ultra"`` in config to use UltraVision.
"""

from llmos_bridge.modules.perception_vision.base import (
    BaseVisionModule,
    VisionElement,
    VisionParseResult,
)
from llmos_bridge.modules.perception_vision.omniparser.module import OmniParserModule
from llmos_bridge.modules.perception_vision.ultra.module import UltraVisionModule

__all__ = [
    "BaseVisionModule",
    "VisionElement",
    "VisionParseResult",
    "OmniParserModule",
    "UltraVisionModule",
]
