#!/usr/bin/env python3
"""LinkedIn Profile E2E Test.

Opens Chrome on LinkedIn profile, reads profile info via OCR,
and saves a summary to the Desktop.

Uses:
- xdotool for GUI control (keyboard shortcuts, mouse)
- LLMOS daemon for click_element (OmniParser-based element matching)
- pytesseract for full-page OCR (reads web page text)
- Ollama gpt-oss:20b for reasoning about profile content

Usage:
  DISPLAY=:1 python examples/linkedin_profile_test.py
"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import time

import httpx


OLLAMA_BASE = "http://localhost:11434"
DAEMON_BASE = "http://127.0.0.1:40000"
MODEL = "gpt-oss:20b"


def xdotool(*args: str) -> str:
    """Run an xdotool command."""
    try:
        result = subprocess.run(
            ["xdotool", *args],
            capture_output=True, text=True, timeout=10
        )
        return result.stdout.strip()
    except subprocess.TimeoutExpired:
        return ""


def screenshot_ocr() -> str:
    """Take a screenshot and OCR it to extract all text."""
    import mss
    import pytesseract
    from PIL import Image

    with mss.mss() as s:
        img = s.grab(s.monitors[1])
        mss.tools.to_png(img.rgb, img.size, output="/tmp/linkedin_ocr.png")

    pil_img = Image.open("/tmp/linkedin_ocr.png")
    text = pytesseract.image_to_string(pil_img, lang="fra+eng")
    return text


NUM_GPU = 10  # Share GPU with OmniParser (24 layers total, 10 on GPU, 14 on CPU)


async def ollama_chat(messages: list[dict]) -> str:
    """Send a chat completion to Ollama and return the response text."""
    async with httpx.AsyncClient() as c:
        resp = await c.post(
            f"{OLLAMA_BASE}/api/chat",
            json={
                "model": MODEL,
                "messages": messages,
                "options": {"num_gpu": NUM_GPU},
                "stream": False,
            },
            timeout=300,
        )
        data = resp.json()
        if data.get("error"):
            print(f"    LLM error: {data['error']}")
            return ""
        return data.get("message", {}).get("content", "")


async def main() -> int:
    print("=" * 60)
    print("LinkedIn Profile E2E Test")
    print(f"Model: {MODEL}")
    print("=" * 60)
    t0 = time.monotonic()

    # Step 0: Verify services
    print("\n[0] Checking services...")
    async with httpx.AsyncClient() as c:
        try:
            h = (await c.get(f"{DAEMON_BASE}/health", timeout=5)).json()
            print(f"    Daemon: OK ({h['modules_loaded']} modules)")
        except Exception as e:
            print(f"    Daemon: FAILED ({e})")
            return 1

        try:
            tags = (await c.get(f"{OLLAMA_BASE}/api/tags", timeout=5)).json()
            models = [m["name"] for m in tags.get("models", [])]
            print(f"    Ollama: OK ({', '.join(models)})")
            if MODEL not in models:
                print(f"    ERROR: {MODEL} not found!")
                return 1
        except Exception as e:
            print(f"    Ollama: FAILED ({e})")
            return 1

    active = xdotool("getactivewindow", "getwindowname")
    print(f"    Active window: {active}")

    # Step 1: Ensure Chrome is on LinkedIn profile
    print("\n[1] Navigating to LinkedIn profile...")
    # Minimize VS Code first
    vscode_wids = xdotool("search", "--name", "Visual Studio Code")
    for wid in (vscode_wids.split("\n") if vscode_wids else []):
        if wid:
            xdotool("windowminimize", wid)

    # Focus Chrome
    chrome_wids = xdotool("search", "--name", "Chrome")
    if chrome_wids:
        wid = chrome_wids.split("\n")[0]
        xdotool("windowactivate", wid)
        time.sleep(1)
        print(f"    Chrome focused: {xdotool('getactivewindow', 'getwindowname')}")
    else:
        print("    Chrome not found, launching...")
        subprocess.Popen(
            ["google-chrome-stable", "--new-window", "https://www.linkedin.com/in/me"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        time.sleep(6)

    # Navigate to profile via Ctrl+L
    xdotool("key", "ctrl+l")
    time.sleep(0.5)
    xdotool("key", "ctrl+a")
    time.sleep(0.2)
    xdotool("type", "--delay", "30", "https://www.linkedin.com/in/me")
    time.sleep(0.3)
    xdotool("key", "Return")
    print("    URL typed: linkedin.com/in/me")
    print("    Waiting for page to load...")
    time.sleep(6)

    print(f"    Active window: {xdotool('getactivewindow', 'getwindowname')}")

    # Step 2: Take screenshot and OCR
    print("\n[2] Capturing profile page (OCR)...")
    ocr_text_1 = screenshot_ocr()
    print(f"    OCR captured: {len(ocr_text_1)} chars")
    print(f"    First 200 chars: {ocr_text_1[:200]}")

    # Step 3: Scroll down and capture more
    print("\n[3] Scrolling down for more content...")
    xdotool("key", "Page_Down")
    time.sleep(2)
    ocr_text_2 = screenshot_ocr()
    print(f"    OCR captured: {len(ocr_text_2)} chars")

    # Scroll down once more
    xdotool("key", "Page_Down")
    time.sleep(2)
    ocr_text_3 = screenshot_ocr()
    print(f"    OCR captured: {len(ocr_text_3)} chars")

    # Combine all OCR text
    full_ocr = f"=== PAGE 1 (top) ===\n{ocr_text_1}\n\n=== PAGE 2 (middle) ===\n{ocr_text_2}\n\n=== PAGE 3 (bottom) ===\n{ocr_text_3}"

    # Step 4: Send to LLM to create a structured summary
    print("\n[4] Asking LLM to summarize profile...")
    summary_prompt = f"""You are reading OCR text extracted from a LinkedIn profile page.
The text may be noisy due to OCR errors. Extract and organize the following information:

- Full Name
- Headline/Title
- Location
- About/Summary section
- Work Experience (company, role, dates)
- Education (school, degree, dates)
- Skills
- Any other notable sections (certifications, languages, etc.)

Write the summary in a clean, readable format. If some info is not visible, write "Not visible on captured pages".

Here is the OCR text from 3 screenshots of the profile page:

{full_ocr[:8000]}

Write a clean profile summary in French:"""

    messages = [
        {"role": "user", "content": summary_prompt}
    ]
    summary = await ollama_chat(messages)
    print(f"    Summary generated: {len(summary)} chars")
    print(f"    Preview: {summary[:300]}...")

    # Step 5: Save to Desktop
    print("\n[5] Saving to /home/paul/Bureau/profil_linkedin.txt...")
    output_path = "/home/paul/Bureau/profil_linkedin.txt"
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(f"=== Résumé du Profil LinkedIn ===\n")
        f.write(f"Généré le: {time.strftime('%Y-%m-%d %H:%M:%S')}\n")
        f.write(f"Par: LLMOS Bridge + {MODEL}\n")
        f.write(f"{'=' * 40}\n\n")
        f.write(summary)
        f.write(f"\n\n{'=' * 40}\n")
        f.write(f"=== Texte OCR brut (page 1) ===\n")
        f.write(ocr_text_1[:2000])

    print(f"    File saved: {output_path}")
    print(f"    Size: {os.path.getsize(output_path)} bytes")

    # Step 6: Verify
    print("\n[6] Verification...")
    with open(output_path, "r") as f:
        content = f.read()
    print(f"    Content preview:\n    {'    '.join(content[:500].splitlines(True))}")

    elapsed = time.monotonic() - t0
    print(f"\n{'=' * 60}")
    print(f"  DONE in {elapsed:.1f}s")
    print(f"  File: {output_path}")
    print(f"  Summary: {len(summary)} chars")
    print(f"{'=' * 60}")

    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
