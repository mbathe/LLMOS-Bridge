"""Visual perception module layer.

Provides structured GUI understanding for LLMOS Bridge actions.
The default implementation uses OmniParser (Microsoft) to parse
screenshots into semantic UI elements.

Quick start::

    from llmos_bridge.modules.perception_vision import OmniParserModule

    registry.register(OmniParserModule)
    result = await module.execute("parse_screen", {"screenshot_path": "/tmp/screen.png"})

Swap the default:
    Any module that subclasses ``BaseVisionModule`` and is registered
    as ``"vision"`` will be used by the perception pipeline automatically.
"""

from llmos_bridge.modules.perception_vision.base import (
    BaseVisionModule,
    VisionElement,
    VisionParseResult,
)
from llmos_bridge.modules.perception_vision.omniparser.module import OmniParserModule

__all__ = [
    "BaseVisionModule",
    "VisionElement",
    "VisionParseResult",
    "OmniParserModule",
]
