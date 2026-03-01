#!/usr/bin/env python3
"""LLMOS Bridge — Real Computer Use E2E Test.

This script tests the FULL computer use pipeline:
  1. OmniParser reads the screen (YOLO + Florence-2 + EasyOCR on GPU)
  2. Claude sees the annotated screenshot + UI elements
  3. Claude decides what to click/type
  4. PyAutoGUI moves the real mouse and types real keys
  5. Claude verifies the result by reading the screen again

YOUR MOUSE WILL MOVE. YOUR KEYBOARD WILL TYPE. This is a real test.

Prerequisites:
  - LLMOS Bridge daemon running with vision + gui + computer_control + os_exec modules
  - ANTHROPIC_API_KEY environment variable set
  - Display available (not headless)

Usage:
  # Default task (open file manager, create folder, verify)
  ANTHROPIC_API_KEY="sk-ant-..." python examples/real_computer_use_test.py

  # Custom task
  ANTHROPIC_API_KEY="sk-ant-..." python examples/real_computer_use_test.py \\
      --task "Open Firefox and navigate to example.com"

  # Verbose with custom max steps
  ANTHROPIC_API_KEY="sk-ant-..." python examples/real_computer_use_test.py \\
      --verbose --max-steps 20
"""

from __future__ import annotations

import argparse
import asyncio
import os
import shutil
import sys
import time

# ── Default task ────────────────────────────────────────────────────────
# This is a multi-step task that exercises the full pipeline:
# read screen → click UI → type text → verify via command
DEFAULT_TASK = """\
You are controlling a real Linux desktop. Follow these steps EXACTLY:

1. First, read the screen to understand what is currently displayed.

2. Open a terminal emulator. You can:
   - Click on a terminal icon in the taskbar/dock if visible
   - Or use the keyboard shortcut Ctrl+Alt+T to open a terminal

3. Once the terminal is open, type this command and press Enter:
     mkdir -p /tmp/llmos_test_folder && echo "LLMOS_SUCCESS" > /tmp/llmos_test_folder/test.txt

4. Verify the file was created by running:
     cat /tmp/llmos_test_folder/test.txt

5. Report back what the output of the cat command shows.

IMPORTANT: Wait for each action to complete before proceeding.
Read the screen after each major action to verify the result.
"""


async def main() -> int:
    parser = argparse.ArgumentParser(
        description="LLMOS Bridge — Real Computer Use E2E Test"
    )
    parser.add_argument(
        "--task", type=str, default=None,
        help="Custom task for the agent (default: open terminal + create folder)",
    )
    parser.add_argument(
        "--max-steps", type=int, default=20,
        help="Maximum agent steps (default: 20)",
    )
    parser.add_argument(
        "--daemon-url", type=str, default="http://127.0.0.1:40000",
        help="LLMOS Bridge daemon URL (default: http://127.0.0.1:40000)",
    )
    parser.add_argument(
        "--verbose", action="store_true", default=True,
        help="Verbose step-by-step logging (default: True)",
    )
    parser.add_argument(
        "--no-cleanup", action="store_true",
        help="Skip cleanup of test artifacts after the test",
    )
    args = parser.parse_args()

    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        print("ERROR: ANTHROPIC_API_KEY not set.")
        print("  export ANTHROPIC_API_KEY='sk-ant-...'")
        return 1

    try:
        from langchain_llmos import ComputerUseAgent
    except ImportError:
        print("ERROR: langchain-llmos not installed.")
        print("  pip install -e packages/langchain-llmos[anthropic]")
        return 1

    task = args.task or DEFAULT_TASK

    print("=" * 70)
    print("LLMOS Bridge — Real Computer Use E2E Test")
    print("=" * 70)
    print()
    print(f"  Provider:   Anthropic Claude Sonnet 4")
    print(f"  Daemon:     {args.daemon_url}")
    print(f"  Max steps:  {args.max_steps}")
    print()
    print("  WARNING: Your mouse WILL move and your keyboard WILL type.")
    print("           Move your mouse to a corner to abort (PyAutoGUI failsafe).")
    print()
    print(f"  Task: {task[:200]}{'...' if len(task) > 200 else ''}")
    print("=" * 70)
    print()

    agent = ComputerUseAgent(
        provider="anthropic",
        api_key=api_key,
        daemon_url=args.daemon_url,
        allowed_modules=["computer_control", "gui", "os_exec", "filesystem", "vision"],
        max_steps=args.max_steps,
        verbose=args.verbose,
    )

    t0 = time.monotonic()
    try:
        async with agent:
            result = await agent.run(task)
    except KeyboardInterrupt:
        print("\n\nAborted by user.")
        return 130
    except Exception as exc:
        print(f"\n\nERROR: {exc}")
        import traceback
        traceback.print_exc()
        return 1

    elapsed = time.monotonic() - t0

    print()
    print("=" * 70)
    print("RESULT")
    print("=" * 70)
    print(f"  Success:    {result.success}")
    print(f"  Steps:      {len(result.steps)}")
    print(f"  Duration:   {elapsed:.1f}s ({result.total_duration_ms:.0f}ms)")
    print()

    if result.steps:
        print("--- Step Log ---")
        for i, step in enumerate(result.steps):
            has_error = "error" in str(step.tool_output).lower()
            status = "ERR" if has_error else "OK "
            tool_name = step.tool_name
            duration = f"{step.duration_ms:.0f}ms"
            # Show a brief summary of the tool input
            brief = ""
            if isinstance(step.tool_input, dict):
                if "command" in step.tool_input:
                    brief = f' cmd={step.tool_input["command"][:60]}'
                elif "query" in step.tool_input:
                    brief = f' query="{step.tool_input["query"][:40]}"'
                elif "text" in step.tool_input:
                    brief = f' text="{step.tool_input["text"][:40]}"'
                elif "x" in step.tool_input and "y" in step.tool_input:
                    brief = f' ({step.tool_input["x"]}, {step.tool_input["y"]})'
            print(f"  {i + 1:2d}. [{status}] {tool_name:<30s} {duration:>8s}{brief}")
        print()

    print("--- Agent Output ---")
    print(result.output)
    print()

    # Verification: check if the test artifacts were created
    if args.task is None:
        test_file = "/tmp/llmos_test_folder/test.txt"
        if os.path.exists(test_file):
            with open(test_file) as f:
                content = f.read().strip()
            if content == "LLMOS_SUCCESS":
                print("VERIFICATION: Test file created and contains correct content")
            else:
                print(f"VERIFICATION WARNING: File exists but content is '{content}'")
        else:
            print("VERIFICATION WARNING: Test file was not created at /tmp/llmos_test_folder/test.txt")

    if result.success:
        print("\nTEST PASSED")
    else:
        print("\nTEST FAILED")

    # Cleanup
    if not args.no_cleanup and args.task is None:
        test_dir = "/tmp/llmos_test_folder"
        if os.path.exists(test_dir):
            shutil.rmtree(test_dir)
            print(f"Cleaned up {test_dir}")

    return 0 if result.success else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
