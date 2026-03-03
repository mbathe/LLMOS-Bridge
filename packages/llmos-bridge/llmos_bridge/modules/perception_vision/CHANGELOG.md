# Changelog -- Visual Perception Module (OmniParser)

## [2.0.0] -- 2026-02-20

### Added
- Full Microsoft OmniParser v2 integration with bundled core code.
- `parse_screen` -- Parse screenshots into structured VisionElement lists with bounding boxes and confidence scores.
- `capture_and_parse` -- Capture the current screen via mss and parse in one action.
- `find_element` -- Find UI elements by label substring and optional element type filter, returning pixel coordinates.
- `get_screen_text` -- Extract all visible text from the screen using OCR.
- Lazy model loading -- weights are loaded on first action call, not at module init.
- Automatic weight download from HuggingFace (`microsoft/OmniParser-v2.0`) on first use.
- Graceful degradation to PIL + pytesseract OCR-only when heavy dependencies are missing.
- Scene graph builder -- hierarchical spatial organization of detected elements into regions.
- PerceptionCache -- MD5-based LRU+TTL cache (~2ms hit vs ~4s GPU parse).
- SpeculativePrefetcher -- background pre-parsing after each action.
- Configurable device (auto-detect CUDA/MPS/CPU), box threshold, IoU threshold, caption model.
- BaseVisionModule ABC for swappable vision backends.
- Security: `@requires_permission(Permission.SCREEN_CAPTURE)` on all actions.

## [1.0.0] -- 2026-02-01

### Added
- Initial release with BaseVisionModule contract.
- Basic parse_screen and capture_and_parse actions.
- VisionElement and VisionParseResult Pydantic models.
