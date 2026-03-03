#!/usr/bin/env python3
"""Compare OmniParser vs UltraVision on a real LinkedIn profile screenshot.

Opens Chrome, navigates to LinkedIn profile, captures screenshot,
runs BOTH vision backends on the same image, and prints a detailed
comparison: timing, element counts, detected types, OCR quality,
scene graph output.

Usage:
  conda activate omni
  cd ~/codes/LLMOS-Bridge
  PYTHONPATH=packages/llmos-bridge python examples/compare_vision_backends.py
"""

from __future__ import annotations

import asyncio
import io
import json
import os
import subprocess
import sys
import time
from pathlib import Path

# Add project to path.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "packages" / "llmos-bridge"))


def xdotool(*args: str) -> str:
    """Run an xdotool command."""
    try:
        result = subprocess.run(
            ["xdotool", *args],
            capture_output=True, text=True, timeout=10,
        )
        return result.stdout.strip()
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return ""


def capture_screenshot() -> bytes:
    """Capture primary monitor screenshot, return PNG bytes."""
    import mss

    with mss.mss() as s:
        monitor = s.monitors[1]
        img = s.grab(monitor)
        png_bytes = mss.tools.to_png(img.rgb, img.size)
    return png_bytes


def open_linkedin_profile():
    """Open Chrome to LinkedIn profile page."""
    print("\n[1] Opening LinkedIn profile in Chrome...")

    # Find Chrome by WM_CLASS (not title — title might match VS Code).
    chrome_wids = xdotool("search", "--class", "google-chrome")
    if chrome_wids:
        wid = chrome_wids.split("\n")[0]
        xdotool("windowactivate", "--sync", wid)
        time.sleep(1)
        print(f"    Chrome focused: {xdotool('getactivewindow', 'getwindowname')}")
    else:
        print("    Launching Chrome...")
        subprocess.Popen(
            ["google-chrome", "--new-window", "https://www.linkedin.com/in/me"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        time.sleep(8)
        active = xdotool("getactivewindow", "getwindowname")
        print(f"    Active window: {active}")
        return

    # Navigate to profile.
    xdotool("key", "ctrl+l")
    time.sleep(0.5)
    xdotool("key", "ctrl+a")
    time.sleep(0.2)
    xdotool("type", "--delay", "30", "https://www.linkedin.com/in/me")
    time.sleep(0.3)
    xdotool("key", "Return")
    print("    Navigating to linkedin.com/in/me...")
    time.sleep(8)

    active = xdotool("getactivewindow", "getwindowname")
    print(f"    Active window: {active}")


def setup_omniparser():
    """Create and configure OmniParserModule."""
    os.environ.setdefault("LLMOS_OMNIPARSER_MODEL_DIR", os.path.expanduser("~/.llmos/models/omniparser"))
    os.environ.setdefault("LLMOS_OMNIPARSER_DEVICE", "cuda")
    os.environ.setdefault("LLMOS_OMNIPARSER_BOX_THRESH", "0.05")
    os.environ.setdefault("LLMOS_OMNIPARSER_IOU_THRESH", "0.1")
    os.environ.setdefault("LLMOS_OMNIPARSER_AUTO_DOWNLOAD", "false")

    from llmos_bridge.modules.perception_vision.omniparser.module import OmniParserModule
    module = OmniParserModule()
    return module


def setup_ultravision():
    """Create and configure UltraVisionModule."""
    os.environ.setdefault("LLMOS_ULTRA_VISION_MODEL_DIR", os.path.expanduser("~/.llmos/models/ultra_vision"))
    os.environ.setdefault("LLMOS_ULTRA_VISION_DEVICE", "cuda")
    os.environ.setdefault("LLMOS_ULTRA_VISION_BOX_THRESH", "0.3")
    os.environ.setdefault("LLMOS_ULTRA_VISION_OCR_ENGINE", "easyocr")
    os.environ.setdefault("LLMOS_ULTRA_VISION_ENABLE_GROUNDING", "false")  # Skip UGround for this test
    os.environ.setdefault("LLMOS_ULTRA_VISION_AUTO_DOWNLOAD", "false")

    from llmos_bridge.modules.perception_vision.ultra.module import UltraVisionModule
    module = UltraVisionModule()
    return module


def print_separator(title: str):
    print(f"\n{'=' * 70}")
    print(f"  {title}")
    print(f"{'=' * 70}")


def print_result(result, backend_name: str, save_dir: str):
    """Print detailed analysis of a VisionParseResult."""
    from collections import Counter

    print(f"\n  --- {backend_name} ---")
    print(f"  Model ID:    {result.model_id}")
    print(f"  Parse time:  {result.parse_time_ms:.0f}ms")
    print(f"  Resolution:  {result.width}x{result.height}")
    print(f"  Elements:    {len(result.elements)}")

    # Element type breakdown.
    type_counts = Counter(e.element_type for e in result.elements)
    print(f"  Types:       {dict(type_counts)}")

    # Interactable elements.
    interactable = [e for e in result.elements if e.interactable]
    print(f"  Interactable: {len(interactable)}")

    # Confidence stats.
    if result.elements:
        confs = [e.confidence for e in result.elements]
        print(f"  Confidence:  min={min(confs):.2f}, max={max(confs):.2f}, avg={sum(confs)/len(confs):.2f}")

    # OCR text length.
    ocr_len = len(result.raw_ocr) if result.raw_ocr else 0
    print(f"  OCR text:    {ocr_len} chars")

    # Scene graph.
    if result.scene_graph_text:
        lines = result.scene_graph_text.strip().split("\n")
        print(f"  Scene graph: {len(lines)} lines")
        # Print first 10 lines.
        for line in lines[:10]:
            print(f"    {line}")
        if len(lines) > 10:
            print(f"    ... ({len(lines) - 10} more lines)")

    # Save labeled image.
    if result.labeled_image_b64:
        import base64
        img_path = os.path.join(save_dir, f"{backend_name.lower().replace(' ', '_')}_labeled.png")
        with open(img_path, "wb") as f:
            f.write(base64.b64decode(result.labeled_image_b64))
        print(f"  Labeled img: saved to {img_path}")

    # Save OCR text.
    ocr_path = os.path.join(save_dir, f"{backend_name.lower().replace(' ', '_')}_ocr.txt")
    with open(ocr_path, "w") as f:
        f.write(result.raw_ocr or "")
    print(f"  OCR dump:    saved to {ocr_path}")

    # Print first few elements.
    print(f"\n  Top 15 elements:")
    for e in result.elements[:15]:
        inter = " [INTERACTABLE]" if e.interactable else ""
        text = f' "{e.text}"' if e.text else ""
        print(f"    [{e.element_id}] {e.element_type:8s} conf={e.confidence:.2f}  label={e.label!r:30s}{text}{inter}")
    if len(result.elements) > 15:
        print(f"    ... ({len(result.elements) - 15} more elements)")


async def run_comparison():
    """Run the full comparison."""
    print_separator("LLMOS Vision Backend Comparison")
    print(f"  Date: {time.strftime('%Y-%m-%d %H:%M:%S')}")

    import torch
    print(f"  GPU: {torch.cuda.get_device_name(0)}")
    print(f"  VRAM: {torch.cuda.get_device_properties(0).total_memory / 1024**3:.1f}GB")

    # Create output directory.
    save_dir = os.path.expanduser("~/Bureau/vision_comparison")
    os.makedirs(save_dir, exist_ok=True)
    print(f"  Output: {save_dir}")

    # Step 1: Open LinkedIn.
    open_linkedin_profile()

    # Step 2: Capture screenshot.
    print("\n[2] Capturing screenshot...")
    screenshot_bytes = capture_screenshot()
    print(f"    Screenshot: {len(screenshot_bytes)} bytes ({len(screenshot_bytes)/1024:.0f}KB)")

    # Save raw screenshot.
    raw_path = os.path.join(save_dir, "screenshot_raw.png")
    with open(raw_path, "wb") as f:
        f.write(screenshot_bytes)
    print(f"    Saved: {raw_path}")

    # Step 3: Run OmniParser.
    print_separator("Running OmniParser (YOLO + Florence-2 + EasyOCR)")
    print("  Loading module...")
    try:
        omni = setup_omniparser()
        print("  Parsing screenshot...")
        t0 = time.perf_counter()
        omni_result = await omni.parse_screen(screenshot_bytes=screenshot_bytes)
        omni_time = (time.perf_counter() - t0) * 1000
        print(f"  Total wall time: {omni_time:.0f}ms")
        print_result(omni_result, "OmniParser", save_dir)
    except Exception as exc:
        print(f"  ERROR: {exc}")
        import traceback
        traceback.print_exc()
        omni_result = None
        omni_time = 0

    # Free GPU memory before loading UltraVision.
    print("\n  Clearing GPU memory...")
    try:
        # Force unload OmniParser's models.
        if hasattr(omni, '_api') and omni._api is not None:
            api = omni._api
            # OmniParser core holds YOLO + Florence-2 in GPU
            if hasattr(api, 'yolo_model'):
                del api.yolo_model
            if hasattr(api, 'caption_model'):
                del api.caption_model
            del omni._api
            omni._api = None
        import gc
        gc.collect()
        import torch
        torch.cuda.empty_cache()
        free_mb = torch.cuda.mem_get_info()[0] / 1024**2
        print(f"  Free VRAM: {free_mb:.0f}MB")
    except Exception as e:
        print(f"  Warning: {e}")

    # Step 4: Run UltraVision.
    print_separator("Running UltraVision (UI-DETR-1 + PaddleOCR)")
    print("  Loading module...")
    try:
        ultra = setup_ultravision()
        print("  Parsing screenshot...")
        t0 = time.perf_counter()
        ultra_result = await ultra.parse_screen(screenshot_bytes=screenshot_bytes)
        ultra_time = (time.perf_counter() - t0) * 1000
        print(f"  Total wall time: {ultra_time:.0f}ms")
        print_result(ultra_result, "UltraVision", save_dir)
    except Exception as exc:
        print(f"  ERROR: {exc}")
        import traceback
        traceback.print_exc()
        ultra_result = None
        ultra_time = 0

    # Step 5: Side-by-side comparison.
    if omni_result and ultra_result:
        print_separator("HEAD-TO-HEAD COMPARISON")

        from collections import Counter

        rows = [
            ("Metric", "OmniParser", "UltraVision", "Winner"),
            ("-" * 25, "-" * 20, "-" * 20, "-" * 12),
            (
                "Parse time",
                f"{omni_result.parse_time_ms:.0f}ms",
                f"{ultra_result.parse_time_ms:.0f}ms",
                "Ultra" if ultra_result.parse_time_ms < omni_result.parse_time_ms else "Omni",
            ),
            (
                "Total elements",
                str(len(omni_result.elements)),
                str(len(ultra_result.elements)),
                "-",
            ),
            (
                "Interactable",
                str(sum(1 for e in omni_result.elements if e.interactable)),
                str(sum(1 for e in ultra_result.elements if e.interactable)),
                "-",
            ),
            (
                "OCR text length",
                str(len(omni_result.raw_ocr or "")),
                str(len(ultra_result.raw_ocr or "")),
                "Ultra" if len(ultra_result.raw_ocr or "") > len(omni_result.raw_ocr or "") else "Omni",
            ),
            (
                "Scene graph lines",
                str(len((omni_result.scene_graph_text or "").split("\n"))),
                str(len((ultra_result.scene_graph_text or "").split("\n"))),
                "-",
            ),
        ]

        # Element types.
        omni_types = Counter(e.element_type for e in omni_result.elements)
        ultra_types = Counter(e.element_type for e in ultra_result.elements)
        all_types = sorted(set(list(omni_types.keys()) + list(ultra_types.keys())))
        for t in all_types:
            rows.append((
                f"  type: {t}",
                str(omni_types.get(t, 0)),
                str(ultra_types.get(t, 0)),
                "-",
            ))

        # Print table.
        for row in rows:
            print(f"  {row[0]:<25s}  {row[1]:<20s}  {row[2]:<20s}  {row[3]}")

        # Speed ratio.
        if omni_result.parse_time_ms > 0 and ultra_result.parse_time_ms > 0:
            ratio = omni_result.parse_time_ms / ultra_result.parse_time_ms
            if ratio > 1:
                print(f"\n  UltraVision is {ratio:.1f}x FASTER than OmniParser")
            else:
                print(f"\n  OmniParser is {1/ratio:.1f}x FASTER than UltraVision")

        # Save comparison report.
        report_path = os.path.join(save_dir, "comparison_report.txt")
        with open(report_path, "w") as f:
            f.write(f"LLMOS Vision Backend Comparison\n")
            f.write(f"Date: {time.strftime('%Y-%m-%d %H:%M:%S')}\n")
            f.write(f"Screenshot: {raw_path}\n\n")
            for row in rows:
                f.write(f"{row[0]:<25s}  {row[1]:<20s}  {row[2]:<20s}  {row[3]}\n")
            f.write(f"\n\nOmniParser Scene Graph:\n{omni_result.scene_graph_text or 'N/A'}\n")
            f.write(f"\n\nUltraVision Scene Graph:\n{ultra_result.scene_graph_text or 'N/A'}\n")
        print(f"\n  Report saved: {report_path}")

    print_separator("DONE")


if __name__ == "__main__":
    asyncio.run(run_comparison())
