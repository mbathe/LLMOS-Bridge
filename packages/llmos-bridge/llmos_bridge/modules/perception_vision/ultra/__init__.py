"""UltraVision — GUI-trained perception engine.

Alternative vision module that uses models specifically trained on
GUI screenshots for element detection, OCR, and visual grounding.

Activate via config::

    vision:
      backend: "ultra"

Models:
  - UI-DETR-1 (racineai/UI-DETR-1): GUI element detection
  - PP-OCRv5 (PaddlePaddle): 106-language OCR
  - UGround-V1-2B (osunlp/UGround-V1-2B): Visual grounding
"""

from llmos_bridge.modules.perception_vision.ultra.module import UltraVisionModule

__all__ = ["UltraVisionModule"]
