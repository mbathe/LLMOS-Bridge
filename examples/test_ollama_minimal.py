#!/usr/bin/env python3
"""Minimal Ollama + LLMOS Bridge E2E test.

Stripped-down test to validate tool calling with small local models.
Uses only 2 tools (read_screen, click_element) and a simple system prompt.

Usage:
  DISPLAY=:1 python examples/test_ollama_minimal.py
"""

from __future__ import annotations

import asyncio
import json
import sys
import time
import uuid

from langchain_llmos.client import AsyncLLMOSClient
from langchain_llmos.providers.openai_provider import OpenAICompatibleProvider


SYSTEM_PROMPT = """\
You are a desktop automation agent controlling a real Linux desktop.

You have these tools:
- read_screen: captures the screen and returns UI elements + scene_graph.
- click_element: clicks on a UI element by description.
- type_text: types PLAIN TEXT only (no special keys). Example: type_text("hello")
- key_press: press keyboard keys/shortcuts. Examples: key_press("enter"), key_press("ctrl+l"), key_press("pagedown"), key_press("tab"), key_press("ctrl+a")
- run_command: runs a shell command. For GUI apps, add '&' at end: 'nohup chrome &>/dev/null &'
- wait: pause for N seconds. Use after launching apps or loading web pages.

IMPORTANT RULES:
- To type text then press Enter: first call type_text("hello"), then call key_press("enter")
- To focus the URL bar in Chrome: call key_press("ctrl+l")
- To scroll down a page: call key_press("pagedown")
- NEVER put special keys inside type_text — use key_press instead.
- Be precise with click_element — use exact labels from the elements list.
- ALWAYS use tool calls — never output raw JSON text.
- The desktop language is French (Bureau = Desktop, Fichier = File, etc.)
"""

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "read_screen",
            "description": "Capture the screen and return detected UI elements with their positions and a hierarchical scene_graph_text showing regions like TOOLBAR, SIDEBAR, CONTENT_AREA",
            "parameters": {
                "type": "object",
                "properties": {},
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "click_element",
            "description": "Click on a UI element identified by description. The agent will find the best matching element and click its center.",
            "parameters": {
                "type": "object",
                "properties": {
                    "target_description": {
                        "type": "string",
                        "description": "Description of the element to click, e.g. 'Firefox icon' or 'search bar'",
                    }
                },
                "required": ["target_description"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "type_text",
            "description": "Type plain text using the keyboard. Do NOT include special keys here — use key_press for Enter, Tab, etc. Only pass the raw text you want typed.",
            "parameters": {
                "type": "object",
                "properties": {
                    "text": {
                        "type": "string",
                        "description": "Plain text to type (no special keys). Example: 'hello world' or 'https://google.com'",
                    }
                },
                "required": ["text"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "key_press",
            "description": "Press a keyboard key or shortcut. For single keys: 'enter', 'tab', 'escape', 'pagedown', 'pageup', 'backspace', 'delete'. For combos: 'ctrl+l' (focus URL bar), 'ctrl+a' (select all), 'ctrl+c' (copy), 'ctrl+v' (paste), 'alt+tab' (switch window).",
            "parameters": {
                "type": "object",
                "properties": {
                    "keys": {
                        "type": "string",
                        "description": "Key or combo to press. Examples: 'enter', 'tab', 'ctrl+l', 'ctrl+a', 'pagedown', 'alt+f4'",
                    }
                },
                "required": ["keys"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run_command",
            "description": "Run a shell command and return its output. IMPORTANT: For GUI apps (chrome, firefox, gedit), wrap with 'nohup ... &>/dev/null &' so they run in background. Example: 'nohup google-chrome-stable https://google.com &>/dev/null &'",
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {
                        "type": "string",
                        "description": "The shell command to execute",
                    }
                },
                "required": ["command"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "wait",
            "description": "Wait for a specified number of seconds. Use this after launching apps or loading pages.",
            "parameters": {
                "type": "object",
                "properties": {
                    "seconds": {
                        "type": "integer",
                        "description": "Number of seconds to wait (1-10)",
                    }
                },
                "required": ["seconds"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "task_complete",
            "description": "Call this when the task is finished. Provide a summary of what was accomplished.",
            "parameters": {
                "type": "object",
                "properties": {
                    "summary": {
                        "type": "string",
                        "description": "Summary of what was done and what was observed",
                    }
                },
                "required": ["summary"],
            },
        },
    },
]


_target_window_name: str | None = None  # Set to e.g. "Chrome" to auto-refocus


def _ensure_target_focused() -> None:
    """Refocus the target window — minimize VS Code first to prevent focus steal."""
    import subprocess, time
    if not _target_window_name:
        return
    try:
        # Minimize ALL VS Code windows (they steal focus from Claude Code terminal)
        vscode_wids = subprocess.run(
            ["xdotool", "search", "--name", "Visual Studio Code"],
            capture_output=True, text=True, timeout=5
        ).stdout.strip().split("\n")
        for wid in vscode_wids:
            if wid:
                subprocess.run(["xdotool", "windowminimize", wid],
                               capture_output=True, timeout=3)

        # Focus the target window
        wids = subprocess.run(
            ["xdotool", "search", "--name", _target_window_name],
            capture_output=True, text=True, timeout=5
        ).stdout.strip().split("\n")
        if wids and wids[0]:
            subprocess.run(["xdotool", "windowactivate", "--sync", wids[0]], timeout=5)
            time.sleep(1)  # Give X11 time to fully render
    except Exception:
        pass


async def execute_tool(daemon: AsyncLLMOSClient, name: str, args: dict,
                       *, ollama_model: str = "gpt-oss:20b") -> str:
    """Execute a tool call via the LLMOS daemon."""
    # Ensure target window is focused before visual actions
    if name in ("read_screen", "click_element", "type_text", "key_press"):
        _ensure_target_focused()

    if name == "read_screen":
        module, action = "computer_control", "read_screen"
        params = {"include_screenshot": False}
    elif name == "click_element":
        module, action = "computer_control", "click_element"
        params = args
    elif name == "type_text":
        module, action = "gui", "type_text"
        # Strip any accidental special keys from text
        text = args.get("text", "")
        import re
        text = re.sub(r'\{[A-Z_+]+\}', '', text)  # Remove {ENTER}, {CTRL+l}, etc.
        params = {"text": text}
    elif name == "key_press":
        module, action = "gui", "key_press"
        keys_str = args.get("keys", "enter")
        # Parse "ctrl+l" → ["ctrl", "l"], "enter" → ["enter"]
        keys_list = [k.strip().lower() for k in keys_str.split("+")]
        params = {"keys": keys_list}
    elif name == "run_command":
        module, action = "os_exec", "run_command"
        cmd = args.get("command", "echo ok")
        params = {"command": ["bash", "-c", cmd]}
    elif name == "wait":
        secs = min(int(args.get("seconds", 3)), 10)
        await asyncio.sleep(secs)
        return json.dumps({"waited": secs, "status": "ok"})
    else:
        return json.dumps({"error": f"Unknown tool: {name}"})

    plan = {
        "plan_id": str(uuid.uuid4()),
        "protocol_version": "2.0",
        "description": f"Agent: {module}.{action}",
        "actions": [
            {"id": "a1", "action": action, "module": module, "params": params}
        ],
    }
    result = await daemon.submit_plan(plan, async_execution=False)
    actions = result.get("actions", [])
    if actions:
        action_result = actions[0].get("result", {})
        # For read_screen: put scene_graph first, then compact elements
        if name == "read_screen":
            compact = {}
            if "scene_graph" in action_result:
                compact["scene_graph"] = action_result["scene_graph"][:2000]
            elements = action_result.get("elements", [])
            # Only include interactable + first few text elements
            interactable = [e for e in elements if e.get("interactable")]
            text_els = [e for e in elements if not e.get("interactable")][:10]
            compact["interactable_elements"] = [
                {"id": e["element_id"], "label": e["label"], "type": e["element_type"]}
                for e in interactable[:30]
            ]
            compact["text_elements"] = [
                {"id": e["element_id"], "label": e["label"]}
                for e in text_els
            ]
            compact["total_elements"] = len(elements)
            return json.dumps(compact, default=str)[:6000]
        # Truncate other results
        return json.dumps(action_result, default=str)[:4000]
    return json.dumps(result, default=str)[:4000]


async def main() -> int:
    import openai

    model = sys.argv[sys.argv.index("--model") + 1] if "--model" in sys.argv else "llama3.1"
    print(f"Testing Ollama ({model}) + LLMOS Bridge")
    print("=" * 60)

    daemon = AsyncLLMOSClient(base_url="http://127.0.0.1:40000", timeout=120.0)

    # Verify daemon health
    try:
        health = await daemon._http.get("/health")
        h = health.json()
        print(f"  Daemon: OK ({h['modules_loaded']} modules)")
    except Exception as e:
        print(f"  Daemon: FAILED ({e})")
        return 1

    client = openai.AsyncOpenAI(api_key="ollama", base_url="http://localhost:11434/v1")

    # Pre-load model with num_gpu split (GPU shared with OmniParser)
    num_gpu = int(sys.argv[sys.argv.index("--num-gpu") + 1]) if "--num-gpu" in sys.argv else None
    if num_gpu is not None:
        import httpx as _httpx
        print(f"  Loading {model} with num_gpu={num_gpu}...")
        async with _httpx.AsyncClient() as _c:
            r = await _c.post("http://localhost:11434/api/chat", json={
                "model": model,
                "messages": [{"role": "user", "content": "say ok"}],
                "options": {"num_gpu": num_gpu},
                "stream": False,
            }, timeout=120)
            d = r.json()
            if d.get("error"):
                print(f"  ERROR: {d['error']}")
                return 1
            print(f"  Model loaded: {d.get('message',{}).get('content','')[:30]}")

    # Get task: first positional arg that isn't --model or its value
    task = None
    skip_next = False
    for a in sys.argv[1:]:
        if skip_next:
            skip_next = False
            continue
        if a in ("--model", "--max-steps", "--focus-window", "--num-gpu"):
            skip_next = True
            continue
        task = a
        break
    task = task or "Read the screen and tell me what you see. Describe the desktop layout, open windows, and visible UI elements."

    # Auto-refocus a specific window (prevents VS Code from stealing focus)
    global _target_window_name
    if "--focus-window" in sys.argv:
        _target_window_name = sys.argv[sys.argv.index("--focus-window") + 1]
        print(f"  Auto-refocus: {_target_window_name}")

    print(f"  Task: {task[:120]}...")
    print()

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": task},
    ]

    max_steps = int(sys.argv[sys.argv.index("--max-steps") + 1]) if "--max-steps" in sys.argv else 25
    steps = []
    t0 = time.monotonic()

    for step in range(max_steps):
        print(f"\n--- Step {step + 1}/{max_steps} ---")

        response = await client.chat.completions.create(
            model=model,
            messages=messages,
            tools=TOOLS,
            tool_choice="auto",
            max_tokens=2048,
        )

        choice = response.choices[0]
        msg = choice.message

        # Collect tool calls — either from proper API or recovered from text
        tool_calls_to_exec = list(msg.tool_calls or [])

        # Fallback: detect tool calls in text output (common with small models)
        if not tool_calls_to_exec and msg.content:
            text_content = msg.content.strip()
            for pattern_start in ['{"name":', '{ "name":']:
                if pattern_start in text_content:
                    try:
                        json_str = text_content[text_content.index("{"):]
                        depth = 0
                        for ci, ch in enumerate(json_str):
                            if ch == "{":
                                depth += 1
                            elif ch == "}":
                                depth -= 1
                            if depth == 0:
                                json_str = json_str[: ci + 1]
                                break
                        parsed = json.loads(json_str)
                        tool_name = parsed.get("name", "")
                        tool_args = parsed.get("parameters", parsed.get("arguments", {}))
                        if tool_name in ("read_screen", "click_element", "type_text", "run_command", "task_complete"):
                            from dataclasses import dataclass

                            @dataclass
                            class FakeFunction:
                                name: str
                                arguments: str

                            @dataclass
                            class FakeToolCall:
                                id: str
                                type: str
                                function: FakeFunction

                            fake_tc = FakeToolCall(
                                id=f"call_text_{step}",
                                type="function",
                                function=FakeFunction(
                                    name=tool_name,
                                    arguments=json.dumps(tool_args),
                                ),
                            )
                            tool_calls_to_exec = [fake_tc]
                            print(f"  [recovered tool call from text: {tool_name}]")
                    except (json.JSONDecodeError, ValueError, KeyError):
                        pass
                    break

        # Check if model wants to use tools
        if tool_calls_to_exec:
            # Append assistant message (only first tool call for sequential execution)
            first_tc = tool_calls_to_exec[0]
            messages.append({
                "role": "assistant",
                "content": msg.content or "",
                "tool_calls": [
                    {
                        "id": first_tc.id,
                        "type": "function",
                        "function": {
                            "name": first_tc.function.name,
                            "arguments": first_tc.function.arguments,
                        },
                    }
                ],
            })

            # Only process the FIRST tool call to force sequential execution
            for tc in tool_calls_to_exec[:1]:
                try:
                    args = json.loads(tc.function.arguments)
                except (json.JSONDecodeError, TypeError):
                    args = {}

                print(f"  Tool: {tc.function.name}({json.dumps(args)[:100]})")

                # Handle task_complete as the exit signal
                if tc.function.name == "task_complete":
                    summary = args.get("summary", "Task complete")
                    print(f"\n  TASK COMPLETE: {summary[:500]}")
                    steps.append({
                        "tool": "task_complete",
                        "args": args,
                        "duration_s": 0,
                    })
                    messages.append({
                        "role": "tool",
                        "tool_call_id": tc.id,
                        "content": "Task marked as complete.",
                    })
                    # Break out of both loops
                    elapsed = time.monotonic() - t0
                    print()
                    print("=" * 60)
                    print(f"  Steps:    {len(steps)}")
                    print(f"  Duration: {elapsed:.1f}s")
                    for i, s in enumerate(steps):
                        print(f"  {i+1}. {s['tool']} — {s['duration_s']:.1f}s")
                    print("=" * 60)
                    await daemon.close()
                    await client.close()
                    return 0

                t_tool = time.monotonic()
                result_text = await execute_tool(daemon, tc.function.name, args,
                                                ollama_model=model)
                duration = time.monotonic() - t_tool

                print(f"  Result ({duration:.1f}s): {result_text[:200]}...")
                steps.append({
                    "tool": tc.function.name,
                    "args": args,
                    "duration_s": duration,
                })

                messages.append({
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": result_text,
                })
        else:
            # Model is done — final text response
            print(f"  Final response: {msg.content[:500]}")
            break

        if choice.finish_reason == "stop" and not tool_calls_to_exec:
            print(f"  Done (finish_reason=stop)")
            break

    elapsed = time.monotonic() - t0

    print()
    print("=" * 60)
    print(f"  Steps:    {len(steps)}")
    print(f"  Duration: {elapsed:.1f}s")
    for i, s in enumerate(steps):
        print(f"  {i+1}. {s['tool']} — {s['duration_s']:.1f}s")
    print("=" * 60)

    await daemon.close()
    await client.close()
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
