#!/usr/bin/env python3
"""LLMOS Bridge — Enhanced Perception E2E Test.

Tests the NEW perception & control features:
  - SceneGraph: hierarchical screen understanding (toolbar, sidebar, forms, etc.)
  - PerceptionCache: skip GPU parse when screen hasn't changed (~2ms vs ~4s)
  - SpeculativePrefetcher: background parse after actions (saves ~4s/iteration)
  - WindowTracker: detect & recover from context switches
  - TextInputEngine: multi-strategy keyboard input (clipboard-paste for AZERTY)

Task: Open Firefox, navigate to Wikipedia, search for a topic, and report results.
This exercises: screen reading (scene graph), clicking (prefetch), typing (input engine),
window tracking (context recovery), and multi-step planning.

YOUR MOUSE WILL MOVE. YOUR KEYBOARD WILL TYPE.

Usage:
  # Anthropic (rate limited)
  ANTHROPIC_API_KEY="sk-ant-..." python examples/test_enhanced_perception.py

  # Ollama (local, no rate limits!)
  python examples/test_enhanced_perception.py --provider ollama --model llama3.1

  # Custom task
  python examples/test_enhanced_perception.py --provider ollama --model llama3.1 --task "Open a terminal and run 'uname -a'"
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time


DEFAULT_TASK = """\
You are controlling a real Linux desktop with full GUI access.

Your goal: Open the Firefox web browser, navigate to https://en.wikipedia.org,
search for "Claude Shannon", and report the first paragraph of the article.

Follow these steps:

1. First, start tracking the active window so you can recover if focus is lost:
   Use window_tracker__start_tracking to track the current window.

2. Read the screen to see what's currently displayed.
   Pay attention to the scene graph structure — it shows you regions like
   [TOOLBAR], [SIDEBAR], [CONTENT_AREA], [TASKBAR], etc.

3. Open Firefox:
   - If Firefox is already visible, click on it
   - If not, look for it in the taskbar, or press Super key and type "firefox"

4. Once Firefox is open, track the Firefox window:
   Use window_tracker__start_tracking with title_pattern "Firefox"

5. Navigate to Wikipedia:
   - Click on the URL bar (it's in the TOOLBAR region)
   - Type "https://en.wikipedia.org" and press Enter

6. Wait for Wikipedia to load, then read the screen.

7. Search for "Claude Shannon":
   - Find the search input on Wikipedia
   - Type "Claude Shannon" and press Enter

8. Read the screen and report what you see in the article's first paragraph.

IMPORTANT:
- Use read_screen to observe after each major action
- Use the scene graph to identify which region contains the element you need
- If you notice the focus shifted to another window, use window_tracker__recover_focus
- Wait for pages to load before reading
"""


def _format_duration(ms: float) -> str:
    if ms < 1000:
        return f"{ms:.0f}ms"
    return f"{ms / 1000:.1f}s"


async def main() -> int:
    parser = argparse.ArgumentParser(
        description="LLMOS Bridge — Enhanced Perception E2E Test"
    )
    parser.add_argument("--task", type=str, default=None)
    parser.add_argument("--max-steps", type=int, default=30)
    parser.add_argument("--daemon-url", type=str, default="http://127.0.0.1:40000")
    parser.add_argument("--verbose", action="store_true", default=True)
    parser.add_argument("--model", type=str, default=None, help="Model name override")
    parser.add_argument(
        "--provider", type=str, default="anthropic",
        help="LLM provider: anthropic, openai, ollama, mistral, gemini",
    )
    parser.add_argument(
        "--no-vision", action="store_true",
        help="Disable vision (don't send screenshots to LLM)",
    )
    parser.add_argument(
        "--legacy-loop", action="store_true",
        help="Use legacy 1-action-at-a-time loop (better for smaller models)",
    )
    args = parser.parse_args()

    # API key: required for cloud providers, optional for Ollama.
    api_key = os.environ.get("ANTHROPIC_API_KEY", "") or os.environ.get("OPENAI_API_KEY", "")
    if args.provider == "ollama":
        api_key = api_key or "ollama"  # Ollama doesn't need a key.
    elif not api_key:
        env_var = {
            "anthropic": "ANTHROPIC_API_KEY",
            "openai": "OPENAI_API_KEY",
            "mistral": "MISTRAL_API_KEY",
            "gemini": "GOOGLE_API_KEY",
        }.get(args.provider, "API_KEY")
        print(f"ERROR: {env_var} not set (required for provider '{args.provider}').")
        return 1

    try:
        from langchain_llmos import ComputerUseAgent
    except ImportError:
        print("ERROR: langchain-llmos not installed.")
        print("  pip install -e packages/langchain-llmos[anthropic]")
        return 1

    task = args.task or DEFAULT_TASK

    # Determine vision support.
    supports_vision = not args.no_vision
    if args.provider == "ollama" and args.model and "vision" not in (args.model or ""):
        supports_vision = False  # Text-only Ollama models.

    print("=" * 70)
    print("LLMOS Bridge — Enhanced Perception E2E Test")
    print("=" * 70)
    print()
    print(f"  Provider:   {args.provider}")
    print(f"  Model:      {args.model or 'default'}")
    print(f"  Vision:     {'Yes' if supports_vision else 'No (text-only, OmniParser handles vision)'}")
    print(f"  Daemon:     {args.daemon_url}")
    print(f"  Max steps:  {args.max_steps}")
    print()
    print("  New features being tested:")
    print("    - SceneGraph (hierarchical perception)")
    print("    - PerceptionCache (skip redundant GPU parses)")
    print("    - SpeculativePrefetcher (background parse after actions)")
    print("    - WindowTracker (context switch detection & recovery)")
    print("    - TextInputEngine (multi-strategy keyboard input)")
    print()
    print("  WARNING: Your mouse WILL move and your keyboard WILL type.")
    print("           Move mouse to screen corner to abort (PyAutoGUI failsafe).")
    print()
    print(f"  Task: {task[:200]}{'...' if len(task) > 200 else ''}")
    print("=" * 70)
    print()

    agent_kwargs = dict(
        provider=args.provider,
        api_key=api_key,
        daemon_url=args.daemon_url,
        allowed_modules=[
            "computer_control", "gui", "os_exec", "filesystem",
            "window_tracker",
        ],
        max_steps=args.max_steps,
        verbose=args.verbose,
        supports_vision=supports_vision,
    )
    if args.model:
        agent_kwargs["model"] = args.model

    agent = ComputerUseAgent(**agent_kwargs)

    use_reactive = not args.legacy_loop

    t0 = time.monotonic()
    try:
        async with agent:
            result = await agent.run(task, use_reactive_loop=use_reactive)
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

    # Categorize steps.
    perception_steps = 0
    action_steps = 0
    window_steps = 0
    total_perception_ms = 0.0
    total_action_ms = 0.0
    scene_graph_seen = False

    if result.steps:
        print("--- Step Log ---")
        for i, step in enumerate(result.steps):
            has_error = "error" in str(step.tool_output).lower()
            status = "ERR" if has_error else "OK "
            tool_name = step.tool_name
            duration = _format_duration(step.duration_ms)

            # Categorize.
            if "read_screen" in tool_name or "capture" in tool_name:
                perception_steps += 1
                total_perception_ms += step.duration_ms
            elif "window_tracker" in tool_name:
                window_steps += 1
            else:
                action_steps += 1
                total_action_ms += step.duration_ms

            # Check if scene_graph was in the output.
            if isinstance(step.tool_output, dict) and "scene_graph" in step.tool_output:
                scene_graph_seen = True

            # Brief summary.
            brief = ""
            if isinstance(step.tool_input, dict):
                if "command" in step.tool_input:
                    brief = f' cmd={step.tool_input["command"][:60]}'
                elif "target_description" in step.tool_input:
                    brief = f' target="{step.tool_input["target_description"][:40]}"'
                elif "text" in step.tool_input:
                    brief = f' text="{step.tool_input["text"][:40]}"'
                elif "title_pattern" in step.tool_input:
                    brief = f' track="{step.tool_input["title_pattern"]}"'

            print(f"  {i + 1:2d}. [{status}] {tool_name:<40s} {duration:>8s}{brief}")
        print()

    # Feature usage summary.
    print("--- Feature Usage ---")
    print(f"  Perception steps:  {perception_steps} ({_format_duration(total_perception_ms)} total)")
    print(f"  Action steps:      {action_steps} ({_format_duration(total_action_ms)} total)")
    print(f"  Window tracker:    {window_steps} calls")
    print(f"  Scene graph seen:  {'Yes' if scene_graph_seen else 'No'}")
    if perception_steps > 0:
        avg_perception = total_perception_ms / perception_steps
        print(f"  Avg perception:    {_format_duration(avg_perception)}")
    print()

    print("--- Agent Output ---")
    print(result.output[:2000])
    print()

    if result.success:
        print("TEST PASSED")
    else:
        print("TEST FAILED")

    return 0 if result.success else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
