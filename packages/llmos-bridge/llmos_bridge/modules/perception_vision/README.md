# Visual Perception Module (OmniParser)

Visual perception module powered by Microsoft OmniParser v2. Combines YOLO v8
detection, Florence-2 captioning, and OCR to parse GUI screenshots into
structured lists of interactable elements with bounding boxes and labels.

## Overview

The Perception Vision module is the default visual understanding backend for
LLMOS Bridge. It converts raw screenshots into structured semantic data that
LLMs can reason about and act upon. The module wraps Microsoft's open-source
OmniParser v2, which combines three pre-trained components:

1. **YOLO v8** -- Fine-tuned icon/button detection on 67K screenshots
2. **Florence-2** -- Fine-tuned icon captioning (describes what each icon is)
3. **PaddleOCR / EasyOCR** -- Text extraction from the screen

Additional features built on top of OmniParser:
- **Scene Graph** -- Hierarchical spatial organization of detected elements
  into regions (window, toolbar, sidebar, form, taskbar) with compact text output.
- **Perception Cache** -- MD5-based LRU+TTL cache that avoids re-parsing
  identical screenshots (~2ms cache hit vs ~4s GPU parse).
- **Speculative Prefetcher** -- Background parsing after each action to
  pre-populate the cache for the next iteration.

The module is registered as `MODULE_ID = "vision"` and can be replaced by any
`BaseVisionModule` subclass registered with the same ID.

## Actions

| Action | Description | Risk | Permission |
|--------|-------------|------|------------|
| `parse_screen` | Parse a screenshot into structured UI elements | Low | `screen_capture` |
| `capture_and_parse` | Capture the current screen and parse it | Low | `screen_capture` |
| `find_element` | Find a UI element by label or type and return its pixel coordinates | Low | `screen_capture` |
| `get_screen_text` | Extract all visible text from the screen via OCR | Low | `screen_capture` |

## Quick Start

```yaml
actions:
  - id: parse-current-screen
    module: vision
    action: capture_and_parse
    params:
      monitor: 0

  - id: find-submit-button
    module: vision
    action: find_element
    depends_on: [parse-current-screen]
    params:
      query: "Submit"
      element_type: button
```

## Requirements

Core (required):
- `Pillow` -- Image loading and manipulation
- `mss` -- Cross-platform screen capture

Full OmniParser pipeline (optional, install with `pip install llmos-bridge[vision]`):
- `torch` -- PyTorch for model inference
- `ultralytics` -- YOLO v8 detection model
- `easyocr` or `paddleocr` -- Text recognition
- `transformers` -- Florence-2 captioning model
- `huggingface-hub` -- Automatic weight download

Fallback: When heavy dependencies are missing, the module degrades gracefully
to a PIL + pytesseract OCR-only path (no bounding boxes, text extraction only).

Model weights are auto-downloaded from HuggingFace (`microsoft/OmniParser-v2.0`)
on first use. Override the model directory with `LLMOS_OMNIPARSER_MODEL_DIR`.

## Configuration

Environment variables:

| Variable | Default | Description |
|----------|---------|-------------|
| `LLMOS_OMNIPARSER_MODEL_DIR` | `~/.llmos/models/omniparser` | Path to model weights directory |
| `LLMOS_OMNIPARSER_DEVICE` | `auto` | torch device: `cpu`, `cuda`, `mps` |
| `LLMOS_OMNIPARSER_BOX_THRESH` | `0.05` | Detection confidence threshold [0-1] |
| `LLMOS_OMNIPARSER_IOU_THRESH` | `0.1` | NMS IoU threshold [0-1] |
| `LLMOS_OMNIPARSER_CAPTION_MODEL` | `florence2` | Caption model: `florence2` or `blip2` |
| `LLMOS_OMNIPARSER_USE_PADDLEOCR` | `true` | Use PaddleOCR instead of EasyOCR |
| `LLMOS_OMNIPARSER_AUTO_DOWNLOAD` | `true` | Auto-download weights from HuggingFace |

Vision config in LLMOS Bridge settings:

| Setting | Default | Description |
|---------|---------|-------------|
| `vision.cache_max_entries` | `5` | Maximum cached parse results (0 = disabled) |
| `vision.cache_ttl_seconds` | `2.0` | Cache entry time-to-live |
| `vision.speculative_prefetch` | `true` | Enable background pre-parsing after actions |

## Platform Support

| Platform | Status |
|----------|--------|
| Linux | Supported |
| macOS | Supported |
| Windows | Supported |

## Architecture

```
perception_vision/
  base.py              BaseVisionModule ABC, VisionElement, VisionParseResult
  cache.py             PerceptionCache (LRU+TTL), SpeculativePrefetcher
  scene_graph.py       SceneGraphBuilder, ScreenRegion, RegionType, SceneGraph
  params.py            Re-exports from protocol/params/perception_vision
  omniparser/
    module.py          OmniParserModule (default backend, MODULE_ID="vision")
    core.py            Bundled OmniParser API wrapper
    utils.py           OmniParser utility functions
    box_annotator.py   Bounding box visualization
```

## Related Modules

- **computer_control** -- Click, type, and interact at coordinates returned by `find_element`.
- **window_tracker** -- Track which window is focused before capturing the screen.
- **gui** -- GUI automation actions that use perception data for intelligent interaction.
- **recording** -- Record perception-guided workflows for replay.
