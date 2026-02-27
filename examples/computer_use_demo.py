#!/usr/bin/env python3
"""LLMOS Bridge — Computer Use Demo (multi-provider).

This script demonstrates autonomous computer control:
  1. Connect to the LLMOS Bridge daemon
  2. Create a ComputerUseAgent powered by any LLM
  3. Give the LLM a task (e.g. "Open the file manager and create a folder")
  4. Watch it perceive the screen, decide, act, and verify — like a human

Supported providers:
  - anthropic  (Claude — requires ANTHROPIC_API_KEY)
  - openai     (GPT-4o — requires OPENAI_API_KEY)
  - ollama     (local models — free, no API key)
  - mistral    (Mistral Large — requires MISTRAL_API_KEY)

Prerequisites:
  - LLMOS Bridge daemon running: ``llmos-bridge serve``
  - OmniParser v2 cloned + weights downloaded (auto-downloads on first use)
  - PyAutoGUI installed + display available
  - Provider SDK installed: ``pip install langchain-llmos[anthropic]``
    or ``pip install langchain-llmos[openai]``

Usage:
  # Anthropic (default)
  python examples/computer_use_demo.py --task "Open the calculator"

  # OpenAI
  python examples/computer_use_demo.py --provider openai --task "Describe the screen"

  # Ollama (local, free)
  python examples/computer_use_demo.py --provider ollama --model llama3.2 --task "Read the screen"

  # Mistral
  python examples/computer_use_demo.py --provider mistral --task "Open the file manager"
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys

_ENV_KEY_MAP = {
    "anthropic": "ANTHROPIC_API_KEY",
    "openai": "OPENAI_API_KEY",
    "mistral": "MISTRAL_API_KEY",
}


async def main() -> int:
    parser = argparse.ArgumentParser(
        description="LLMOS Bridge Computer Use Demo — LLM controls your computer",
    )
    parser.add_argument(
        "--provider",
        default="anthropic",
        choices=["anthropic", "openai", "ollama", "mistral"],
        help="LLM provider (default: anthropic)",
    )
    parser.add_argument(
        "--task",
        default="Read the screen and describe what you see. List the main UI elements visible.",
        help="Task for the LLM to perform",
    )
    parser.add_argument(
        "--daemon-url",
        default="http://127.0.0.1:40000",
        help="LLMOS Bridge daemon URL (default: http://127.0.0.1:40000)",
    )
    parser.add_argument(
        "--model",
        default=None,
        help="Model name (provider-specific default if omitted)",
    )
    parser.add_argument(
        "--api-key",
        default=None,
        help="API key (falls back to env var per provider)",
    )
    parser.add_argument(
        "--max-steps",
        type=int,
        default=20,
        help="Maximum agent steps (default: 20)",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Print step-by-step progress",
    )
    args = parser.parse_args()

    # Check for API key (not needed for Ollama).
    env_key_name = _ENV_KEY_MAP.get(args.provider)
    if env_key_name and not args.api_key and not os.environ.get(env_key_name):
        print(f"ERROR: {env_key_name} environment variable not set.")
        print(f"  export {env_key_name}='your-key-here'")
        print(f"  or use: --api-key <key>")
        return 1

    # Import here to give a clear error if not installed.
    try:
        from langchain_llmos import ComputerUseAgent
    except ImportError:
        print(f"ERROR: langchain-llmos not installed.")
        print(f"  pip install langchain-llmos[{args.provider}]")
        return 1

    # Build agent kwargs.
    agent_kwargs: dict = {
        "provider": args.provider,
        "daemon_url": args.daemon_url,
        "max_steps": args.max_steps,
        "verbose": args.verbose,
    }
    if args.model:
        agent_kwargs["model"] = args.model
    if args.api_key:
        agent_kwargs["api_key"] = args.api_key

    model_display = args.model or f"(default for {args.provider})"

    print("=" * 60)
    print("LLMOS Bridge — Computer Use Demo")
    print("=" * 60)
    print(f"  Provider: {args.provider}")
    print(f"  Model:    {model_display}")
    print(f"  Daemon:   {args.daemon_url}")
    print(f"  Max steps: {args.max_steps}")
    print(f"  Task:     {args.task}")
    print("=" * 60)
    print()

    try:
        async with ComputerUseAgent(**agent_kwargs) as agent:
            result = await agent.run(args.task)
    except ImportError as exc:
        print(f"ERROR: Missing dependency: {exc}")
        print(f"  pip install langchain-llmos[{args.provider}]")
        return 1

    print()
    print("=" * 60)
    print("RESULT")
    print("=" * 60)
    print(f"  Success:  {result.success}")
    print(f"  Steps:    {len(result.steps)}")
    print(f"  Duration: {result.total_duration_ms:.0f}ms")
    print()
    print("--- Output ---")
    print(result.output)
    print()

    if result.steps:
        print("--- Step Summary ---")
        for i, step in enumerate(result.steps):
            status = "OK" if "error" not in str(step.tool_output) else "ERR"
            print(f"  {i + 1}. [{status}] {step.tool_name} ({step.duration_ms:.0f}ms)")
        print()

    return 0 if result.success else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
