"""UltraVision backend interfaces and implementations."""

from llmos_bridge.modules.perception_vision.ultra.backends.detector import (
    BaseDetector,
    DetectionOutput,
    DetectionResult,
)
from llmos_bridge.modules.perception_vision.ultra.backends.grounder import (
    BaseGrounder,
    GroundingResult,
)
from llmos_bridge.modules.perception_vision.ultra.backends.ocr import (
    BaseOCR,
    OCRBox,
    OCROutput,
)

__all__ = [
    "BaseDetector",
    "DetectionOutput",
    "DetectionResult",
    "BaseGrounder",
    "GroundingResult",
    "BaseOCR",
    "OCRBox",
    "OCROutput",
]
