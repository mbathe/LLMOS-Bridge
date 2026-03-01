#!/usr/bin/env python3
"""LLMOS Bridge — Ollama Production Readiness Benchmark.

Evaluates whether a local LLM (via Ollama) can reliably drive the
LLMOS computer-use pipeline through 5 scenarios of increasing difficulty.

Each scenario is scored 0-100 based on:
  - Task completion (did it finish?)
  - Accuracy (did it produce correct results?)
  - Efficiency (how many steps / how much time?)
  - Recovery (did it handle errors gracefully?)

Usage:
  DISPLAY=:1 python examples/benchmark_ollama.py
  DISPLAY=:1 python examples/benchmark_ollama.py --model gpt-oss:20b
  DISPLAY=:1 python examples/benchmark_ollama.py --model llama3.1 --scenario 3
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

# ---------------------------------------------------------------------------
# Types
# ---------------------------------------------------------------------------


@dataclass
class FakeFunction:
    name: str
    arguments: str


@dataclass
class FakeToolCall:
    id: str
    type: str
    function: FakeFunction


@dataclass
class StepLog:
    tool: str
    args: dict
    result: str
    duration_s: float
    success: bool


@dataclass
class ScenarioResult:
    name: str
    score: int  # 0-100
    steps: list[StepLog] = field(default_factory=list)
    duration_s: float = 0.0
    details: str = ""
    passed: bool = False


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "read_screen",
            "description": "Capture the screen. Returns scene_graph (hierarchical layout) and elements list (interactable + text).",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "click_element",
            "description": "Click on a UI element by description. Uses fuzzy matching against visible elements.",
            "parameters": {
                "type": "object",
                "properties": {
                    "target_description": {
                        "type": "string",
                        "description": "Element to click (use exact labels from read_screen when possible)",
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
            "description": "Type text via keyboard. Special keys: {ENTER}, {TAB}, {ESCAPE}, {PAGEDOWN}, {PAGEUP}, {BACKSPACE}",
            "parameters": {
                "type": "object",
                "properties": {
                    "text": {"type": "string", "description": "Text to type"},
                },
                "required": ["text"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run_command",
            "description": "Execute a shell command and return stdout/stderr/return_code.",
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {"type": "string", "description": "Shell command to run"},
                },
                "required": ["command"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "task_complete",
            "description": "Signal task completion. MUST include an answer if the task asks a question.",
            "parameters": {
                "type": "object",
                "properties": {
                    "summary": {"type": "string", "description": "What was done"},
                    "answer": {"type": "string", "description": "Answer to the task question, if any"},
                },
                "required": ["summary"],
            },
        },
    },
]

SYSTEM_PROMPT = """\
You are a desktop automation agent controlling a real Linux desktop via tools.

Available tools: read_screen, click_element, type_text, run_command, task_complete.

Rules:
1. ALWAYS call read_screen first to see the current state.
2. After each action, call read_screen to verify the result changed.
3. Use exact element labels from read_screen results when clicking.
4. When the task is done, call task_complete with summary AND answer (if asked).
5. If an action fails, try an alternative approach.
6. Be efficient — minimize unnecessary steps.
"""


# ---------------------------------------------------------------------------
# Tool execution
# ---------------------------------------------------------------------------

async def execute_tool(daemon, name: str, args: dict) -> str:
    """Execute a tool via the LLMOS daemon."""
    from langchain_llmos.client import AsyncLLMOSClient

    # For run_command: use shell via bash -c for proper pipe/redirect support
    cmd_str = args.get("command", "echo ok")
    mapping = {
        "read_screen": ("computer_control", "read_screen", {"include_screenshot": False}),
        "click_element": ("computer_control", "click_element", args),
        "type_text": ("gui", "type_text", {"text": args.get("text", "")}),
        "run_command": ("os_exec", "run_command", {"command": ["bash", "-c", cmd_str]}),
    }

    if name not in mapping:
        return json.dumps({"error": f"Unknown tool: {name}"})

    module, action, params = mapping[name]

    plan = {
        "plan_id": str(uuid.uuid4()),
        "protocol_version": "2.0",
        "description": f"Bench: {module}.{action}",
        "actions": [{"id": "a1", "action": action, "module": module, "params": params}],
    }

    result = await daemon.submit_plan(plan, async_execution=False)
    actions = result.get("actions", [])
    if not actions:
        return json.dumps(result, default=str)[:4000]

    action_result = actions[0].get("result", {})

    if name == "read_screen":
        compact: dict[str, Any] = {}
        if "scene_graph" in action_result:
            compact["scene_graph"] = action_result["scene_graph"][:2500]
        elements = action_result.get("elements", [])
        interactable = [e for e in elements if e.get("interactable")]
        text_els = [e for e in elements if not e.get("interactable")][:15]
        compact["interactable_elements"] = [
            {"id": e["element_id"], "label": e["label"], "type": e["element_type"]}
            for e in interactable[:40]
        ]
        compact["text_elements"] = [
            {"id": e["element_id"], "label": e["label"]}
            for e in text_els
        ]
        compact["total_elements"] = len(elements)
        return json.dumps(compact, default=str)[:8000]

    return json.dumps(action_result, default=str)[:4000]


# ---------------------------------------------------------------------------
# Agent loop (single scenario)
# ---------------------------------------------------------------------------

async def run_agent(
    client,
    daemon,
    model: str,
    task: str,
    max_steps: int = 12,
    verbose: bool = True,
) -> tuple[list[StepLog], str | None, float]:
    """Run the agent loop. Returns (steps, final_answer, duration_s)."""
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": task},
    ]
    steps: list[StepLog] = []
    final_answer: str | None = None
    t0 = time.monotonic()

    for step_idx in range(max_steps):
        if verbose:
            print(f"    Step {step_idx + 1}/{max_steps}", end="", flush=True)

        try:
            response = await client.chat.completions.create(
                model=model,
                messages=messages,
                tools=TOOLS,
                tool_choice="auto",
                max_tokens=2048,
                timeout=60,
            )
        except Exception as exc:
            if verbose:
                print(f" — LLM error: {exc}")
            break

        choice = response.choices[0]
        msg = choice.message

        # Collect tool calls (native or recovered from text)
        tool_calls = list(msg.tool_calls or [])
        if not tool_calls and msg.content:
            recovered = _recover_tool_call_from_text(msg.content, step_idx)
            if recovered:
                tool_calls = [recovered]
                if verbose:
                    print(f" [recovered: {recovered.function.name}]", end="")

        if not tool_calls:
            if verbose:
                text = (msg.content or "")[:120]
                print(f" — text: {text}")
            break

        # Process only first tool call (sequential)
        tc = tool_calls[0]
        try:
            args = json.loads(tc.function.arguments)
        except (json.JSONDecodeError, TypeError):
            args = {}

        messages.append({
            "role": "assistant",
            "content": msg.content or "",
            "tool_calls": [{
                "id": tc.id, "type": "function",
                "function": {"name": tc.function.name, "arguments": tc.function.arguments},
            }],
        })

        # Handle task_complete
        if tc.function.name == "task_complete":
            final_answer = args.get("answer") or args.get("summary", "")
            if verbose:
                print(f" — COMPLETE: {final_answer[:120]}")
            steps.append(StepLog("task_complete", args, "", 0, True))
            break

        # Execute tool
        t_tool = time.monotonic()
        result_text = await execute_tool(daemon, tc.function.name, args)
        dur = time.monotonic() - t_tool
        success = "error" not in result_text.lower()[:200]

        if verbose:
            status = "OK" if success else "ERR"
            brief = result_text[:100].replace("\n", " ")
            print(f" — {tc.function.name} [{status}] ({dur:.1f}s) {brief}...")

        steps.append(StepLog(tc.function.name, args, result_text, dur, success))
        messages.append({"role": "tool", "tool_call_id": tc.id, "content": result_text})

    return steps, final_answer, time.monotonic() - t0


def _recover_tool_call_from_text(text: str, step_idx: int) -> FakeToolCall | None:
    """Parse tool call from text output (small model fallback).

    Handles:
    - Raw JSON: {"name": "tool", "parameters": {...}}
    - Markdown code blocks: ```json\n{...}\n```
    - Mixed text + JSON
    """
    text = text.strip()
    valid_tools = {"read_screen", "click_element", "type_text", "run_command", "task_complete"}

    # Strip markdown code block wrappers
    cleaned = re.sub(r"```(?:json)?\s*\n?", "", text).strip()

    # Try to find JSON with "name" key
    for pattern in [r'\{[^{}]*"name"\s*:', r'\{\s*"name"\s*:']:
        match = re.search(pattern, cleaned)
        if not match:
            continue
        try:
            json_start = match.start()
            json_str = cleaned[json_start:]
            # Balance braces
            depth = 0
            end = 0
            for i, ch in enumerate(json_str):
                if ch == "{":
                    depth += 1
                elif ch == "}":
                    depth -= 1
                if depth == 0:
                    end = i + 1
                    break
            if end == 0:
                continue
            json_str = json_str[:end]
            parsed = json.loads(json_str)
            name = parsed.get("name", "")
            args = parsed.get("parameters", parsed.get("arguments", {}))
            if name in valid_tools:
                return FakeToolCall(
                    id=f"call_text_{step_idx}",
                    type="function",
                    function=FakeFunction(name=name, arguments=json.dumps(args)),
                )
        except (json.JSONDecodeError, ValueError):
            continue

    return None


# ---------------------------------------------------------------------------
# Scenarios
# ---------------------------------------------------------------------------

async def scenario_1_observation(client, daemon, model: str, verbose: bool) -> ScenarioResult:
    """S1: Screen observation + accurate reporting.

    Task: read the screen and correctly identify the active window + key elements.
    Scoring: correct window title, correct element count range, scene graph mentioned.
    """
    # First, get ground truth
    import subprocess
    gt_title = subprocess.run(
        ["xdotool", "getactivewindow", "getwindowname"],
        capture_output=True, text=True, env={**os.environ, "DISPLAY": ":1"},
    ).stdout.strip()

    task = (
        "Read the screen. Tell me:\n"
        "1. What is the title of the active/focused window?\n"
        "2. How many interactable elements are visible?\n"
        "3. What regions does the scene graph show (e.g. TOOLBAR, SIDEBAR, TASKBAR)?\n"
        "Call task_complete with your answers."
    )

    steps, answer, duration = await run_agent(client, daemon, model, task, max_steps=5, verbose=verbose)

    # Score
    score = 0
    details = []

    if answer:
        answer_lower = answer.lower()
        # Check window title recognition
        title_words = gt_title.lower().split()[:3]
        title_match = sum(1 for w in title_words if w in answer_lower)
        if title_match >= 1:
            score += 35
            details.append(f"Window title: partial match ({title_match}/{len(title_words)} words)")
        if title_match >= 2:
            score += 15
            details.append("Window title: good match")

        # Check element count mentioned
        if any(c.isdigit() for c in answer):
            score += 20
            details.append("Element count: mentioned")

        # Check scene graph regions
        regions = ["toolbar", "sidebar", "taskbar", "title_bar", "window", "content"]
        found_regions = [r for r in regions if r in answer_lower]
        if found_regions:
            score += 20
            details.append(f"Regions: {', '.join(found_regions)}")

        # Bonus: called task_complete properly
        if any(s.tool == "task_complete" for s in steps):
            score += 10
            details.append("Proper task_complete call")
    else:
        details.append("No answer produced")

    # Efficiency penalty
    tool_steps = [s for s in steps if s.tool != "task_complete"]
    if len(tool_steps) > 3:
        score = max(0, score - 10)
        details.append(f"Efficiency penalty: {len(tool_steps)} steps (expected ≤3)")

    return ScenarioResult(
        name="S1: Screen Observation",
        score=min(100, score),
        steps=steps,
        duration_s=duration,
        details="; ".join(details),
        passed=score >= 50,
    )


async def scenario_2_file_creation(client, daemon, model: str, verbose: bool) -> ScenarioResult:
    """S2: Create a file via shell command and verify it.

    Task: create a file with specific content, then verify it exists and has correct content.
    """
    test_file = "/tmp/llmos_bench_test.txt"
    expected_content = "Hello from LLMOS Bridge benchmark"

    # Clean up
    try:
        os.remove(test_file)
    except FileNotFoundError:
        pass

    task = (
        f"Create the file {test_file} with the exact content: '{expected_content}'\n"
        f"Then verify the file exists and read its content.\n"
        "Call task_complete with the file content as the answer."
    )

    steps, answer, duration = await run_agent(client, daemon, model, task, max_steps=8, verbose=verbose)

    score = 0
    details = []

    # Check if file was actually created
    if os.path.exists(test_file):
        score += 30
        details.append("File created")
        actual = open(test_file).read().strip()
        if expected_content in actual:
            score += 30
            details.append("Correct content")
        else:
            details.append(f"Wrong content: '{actual[:60]}'")
    else:
        details.append("File NOT created")

    # Check if answer contains the content
    if answer and expected_content.lower()[:20] in (answer or "").lower():
        score += 20
        details.append("Answer contains correct content")

    # Check task_complete called
    if any(s.tool == "task_complete" for s in steps):
        score += 10
        details.append("Proper task_complete")

    # Efficiency
    tool_steps = [s for s in steps if s.tool != "task_complete"]
    if len(tool_steps) <= 4:
        score += 10
        details.append(f"Efficient: {len(tool_steps)} steps")

    # Clean up
    try:
        os.remove(test_file)
    except FileNotFoundError:
        pass

    return ScenarioResult(
        name="S2: File Creation + Verify",
        score=min(100, score),
        steps=steps,
        duration_s=duration,
        details="; ".join(details),
        passed=score >= 50,
    )


async def scenario_3_system_info(client, daemon, model: str, verbose: bool) -> ScenarioResult:
    """S3: Gather system information from multiple commands.

    Task: run several commands to collect system info and report a structured summary.
    Tests: multi-step command execution, information synthesis.
    """
    task = (
        "Gather system information by running these commands:\n"
        "1. 'hostname' to get the machine name\n"
        "2. 'uname -r' to get the kernel version\n"
        "3. 'nproc' to get the number of CPU cores\n"
        "4. 'free -h | head -2' to get memory info\n"
        "Call task_complete with answer containing: hostname, kernel version, CPU cores, and total RAM."
    )

    steps, answer, duration = await run_agent(client, daemon, model, task, max_steps=10, verbose=verbose)

    # Get ground truth
    import subprocess
    gt = {
        "hostname": subprocess.run(["hostname"], capture_output=True, text=True).stdout.strip(),
        "kernel": subprocess.run(["uname", "-r"], capture_output=True, text=True).stdout.strip(),
        "nproc": subprocess.run(["nproc"], capture_output=True, text=True).stdout.strip(),
    }

    score = 0
    details = []

    # Check commands were run
    run_steps = [s for s in steps if s.tool == "run_command"]
    if len(run_steps) >= 3:
        score += 20
        details.append(f"Ran {len(run_steps)} commands")
    elif len(run_steps) >= 1:
        score += 10
        details.append(f"Ran only {len(run_steps)} commands")

    if answer:
        answer_lower = answer.lower()
        # Check each piece of info in answer
        if gt["hostname"].lower() in answer_lower:
            score += 20
            details.append(f"Hostname correct: {gt['hostname']}")

        # Kernel version (at least major.minor)
        kernel_short = ".".join(gt["kernel"].split(".")[:2])
        if kernel_short in answer:
            score += 20
            details.append(f"Kernel correct: {kernel_short}")

        if gt["nproc"] in answer:
            score += 15
            details.append(f"CPU cores correct: {gt['nproc']}")

        # Memory (look for "14G" or "15G" or similar)
        if any(m in answer for m in ["14G", "15G", "14g", "15g", "14 G", "15 G"]):
            score += 15
            details.append("RAM info present")

        if any(s.tool == "task_complete" for s in steps):
            score += 10
            details.append("Proper task_complete")
    else:
        details.append("No answer produced")

    return ScenarioResult(
        name="S3: System Info Gathering",
        score=min(100, score),
        steps=steps,
        duration_s=duration,
        details="; ".join(details),
        passed=score >= 50,
    )


async def scenario_4_gui_interaction(client, daemon, model: str, verbose: bool) -> ScenarioResult:
    """S4: GUI interaction — click taskbar, identify window elements.

    Task: read screen, identify the taskbar, click on a specific app, verify it opened.
    Tests: scene graph understanding, element resolution, GUI control.
    """
    task = (
        "Read the screen. Look at the scene_graph for the [TASKBAR] region at the bottom.\n"
        "List the application icons visible in the taskbar.\n"
        "Then click on the 'Files' or 'File Manager' icon in the taskbar (or any file manager).\n"
        "If you can't find it, try clicking 'TERMINAL' in the taskbar instead.\n"
        "Read the screen again to verify something opened.\n"
        "Call task_complete with:\n"
        "- answer: the list of taskbar applications you found\n"
        "- summary: what you clicked and whether it opened successfully"
    )

    steps, answer, duration = await run_agent(client, daemon, model, task, max_steps=10, verbose=verbose)

    score = 0
    details = []

    # Check read_screen was called
    read_steps = [s for s in steps if s.tool == "read_screen"]
    if len(read_steps) >= 1:
        score += 15
        details.append(f"Read screen: {len(read_steps)}x")

    # Check click was attempted
    click_steps = [s for s in steps if s.tool == "click_element"]
    if click_steps:
        score += 15
        details.append(f"Click attempted: {len(click_steps)}x")
        if any(s.success for s in click_steps):
            score += 20
            details.append("Click succeeded")

    # Check verification (read after click)
    if len(read_steps) >= 2:
        score += 15
        details.append("Verified after click")

    if answer:
        answer_lower = answer.lower()
        # Check if taskbar apps were listed
        common_apps = ["terminal", "firefox", "files", "file manager", "nautilus", "text editor"]
        found = [a for a in common_apps if a in answer_lower]
        if found:
            score += 15
            details.append(f"Apps identified: {', '.join(found)}")
        else:
            details.append("No common apps identified in answer")

    if any(s.tool == "task_complete" for s in steps):
        score += 10
        details.append("Proper task_complete")

    # Efficiency
    tool_steps = [s for s in steps if s.tool != "task_complete"]
    if len(tool_steps) <= 5:
        score += 10
        details.append(f"Efficient: {len(tool_steps)} steps")

    return ScenarioResult(
        name="S4: GUI Interaction",
        score=min(100, score),
        steps=steps,
        duration_s=duration,
        details="; ".join(details),
        passed=score >= 40,
    )


async def scenario_5_scripting(client, daemon, model: str, verbose: bool) -> ScenarioResult:
    """S5: Write and execute a script.

    Task: create a Python script, run it, and report the output.
    Tests: multi-line file creation, script execution, output parsing.
    """
    script_path = "/tmp/llmos_bench_script.py"
    try:
        os.remove(script_path)
    except FileNotFoundError:
        pass

    task = (
        f"Create a Python script at {script_path} that:\n"
        "1. Computes the first 10 Fibonacci numbers\n"
        "2. Prints them as a comma-separated list\n"
        "Then run the script with 'python3' and report the output.\n"
        "Call task_complete with the Fibonacci numbers as the answer."
    )

    steps, answer, duration = await run_agent(client, daemon, model, task, max_steps=10, verbose=verbose)

    expected_fibs = "0, 1, 1, 2, 3, 5, 8, 13, 21, 34"
    alt_fibs = "1, 1, 2, 3, 5, 8, 13, 21, 34, 55"  # starting from 1

    score = 0
    details = []

    # Check script was created
    if os.path.exists(script_path):
        score += 20
        details.append("Script created")
        content = open(script_path).read()
        if "fibonacci" in content.lower() or "fib" in content.lower():
            score += 10
            details.append("Script mentions fibonacci")
    else:
        details.append("Script NOT created")

    # Check if script was run
    run_steps = [s for s in steps if s.tool == "run_command"]
    script_run = any("python" in str(s.args) and "bench_script" in str(s.args) for s in run_steps)
    if script_run:
        score += 15
        details.append("Script executed")

    # Check answer for Fibonacci numbers
    if answer:
        fib_nums = ["0", "1", "1", "2", "3", "5", "8", "13", "21", "34"]
        alt_nums = ["1", "1", "2", "3", "5", "8", "13", "21", "34", "55"]
        found = sum(1 for n in fib_nums if n in answer)
        found_alt = sum(1 for n in alt_nums if n in answer)
        best = max(found, found_alt)
        if best >= 8:
            score += 35
            details.append(f"Fibonacci correct: {best}/10 numbers match")
        elif best >= 5:
            score += 20
            details.append(f"Fibonacci partial: {best}/10 numbers match")
        else:
            details.append(f"Fibonacci wrong: only {best}/10 match")

    if any(s.tool == "task_complete" for s in steps):
        score += 10
        details.append("Proper task_complete")

    # Efficiency
    tool_steps = [s for s in steps if s.tool != "task_complete"]
    if len(tool_steps) <= 5:
        score += 10
        details.append(f"Efficient: {len(tool_steps)} steps")

    try:
        os.remove(script_path)
    except FileNotFoundError:
        pass

    return ScenarioResult(
        name="S5: Script Creation + Execution",
        score=min(100, score),
        steps=steps,
        duration_s=duration,
        details="; ".join(details),
        passed=score >= 50,
    )


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

SCENARIOS = {
    1: ("Screen Observation", scenario_1_observation),
    2: ("File Creation + Verify", scenario_2_file_creation),
    3: ("System Info Gathering", scenario_3_system_info),
    4: ("GUI Interaction", scenario_4_gui_interaction),
    5: ("Script Creation + Execution", scenario_5_scripting),
}


async def main() -> int:
    parser = argparse.ArgumentParser(description="LLMOS Ollama Production Benchmark")
    parser.add_argument("--model", default="llama3.1", help="Ollama model name")
    parser.add_argument("--scenario", type=int, default=0, help="Run specific scenario (1-5, 0=all)")
    parser.add_argument("--quiet", action="store_true", help="Less output")
    parser.add_argument("--daemon-url", default="http://127.0.0.1:40000")
    args = parser.parse_args()

    import openai
    from langchain_llmos.client import AsyncLLMOSClient

    model = args.model
    verbose = not args.quiet

    print()
    print("=" * 70)
    print("  LLMOS Bridge — Ollama Production Readiness Benchmark")
    print("=" * 70)
    print(f"  Model:    {model}")
    print(f"  Daemon:   {args.daemon_url}")
    print(f"  Scenarios: {args.scenario or 'all (1-5)'}")
    print("=" * 70)
    print()

    # Verify daemon
    daemon = AsyncLLMOSClient(base_url=args.daemon_url, timeout=120.0)
    try:
        h = (await daemon._http.get("/health")).json()
        print(f"  Daemon: OK ({h['modules_loaded']} modules)")
    except Exception as e:
        print(f"  Daemon: FAILED ({e})")
        return 1

    # Verify Ollama
    client = openai.AsyncOpenAI(api_key="ollama", base_url="http://localhost:11434/v1")
    try:
        test_resp = await client.chat.completions.create(
            model=model, messages=[{"role": "user", "content": "Say OK"}], max_tokens=5,
        )
        print(f"  Ollama:  OK ({model})")
    except Exception as e:
        print(f"  Ollama:  FAILED ({e})")
        return 1

    print()

    # Run scenarios
    if args.scenario:
        to_run = {args.scenario: SCENARIOS[args.scenario]}
    else:
        to_run = SCENARIOS

    results: list[ScenarioResult] = []
    total_t0 = time.monotonic()

    for num, (name, fn) in to_run.items():
        print(f"{'─' * 70}")
        print(f"  Scenario {num}: {name}")
        print(f"{'─' * 70}")

        try:
            result = await fn(client, daemon, model, verbose)
        except Exception as exc:
            import traceback
            traceback.print_exc()
            result = ScenarioResult(
                name=f"S{num}: {name}", score=0, duration_s=0,
                details=f"Exception: {exc}", passed=False,
            )

        results.append(result)
        status = "PASS" if result.passed else "FAIL"
        print(f"\n  Result: [{status}] Score: {result.score}/100 — {result.duration_s:.1f}s")
        print(f"  Details: {result.details}")
        print()

    total_duration = time.monotonic() - total_t0

    # Summary
    print()
    print("=" * 70)
    print("  BENCHMARK RESULTS")
    print("=" * 70)
    print()
    print(f"  Model: {model}")
    print(f"  Total duration: {total_duration:.1f}s")
    print()

    total_score = 0
    passed = 0
    for r in results:
        status = "PASS" if r.passed else "FAIL"
        step_count = len([s for s in r.steps if s.tool != "task_complete"])
        print(f"  [{status}] {r.name:<35s} {r.score:3d}/100  {r.duration_s:6.1f}s  {step_count} steps")
        total_score += r.score
        if r.passed:
            passed += 1

    max_score = len(results) * 100
    pct = total_score / max_score * 100 if max_score > 0 else 0

    print()
    print(f"  Total: {total_score}/{max_score} ({pct:.0f}%)  —  {passed}/{len(results)} scenarios passed")
    print()

    # Production readiness assessment
    if pct >= 80:
        verdict = "PRODUCTION READY — Excellent performance across all scenarios"
    elif pct >= 60:
        verdict = "CONDITIONALLY READY — Good for simple tasks, needs supervision for complex ones"
    elif pct >= 40:
        verdict = "NOT READY — Can handle basic tasks but unreliable for production use"
    else:
        verdict = "NOT SUITABLE — Insufficient capability for computer use tasks"

    print(f"  Verdict: {verdict}")
    print("=" * 70)

    await daemon.close()
    await client.close()
    return 0 if pct >= 40 else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
