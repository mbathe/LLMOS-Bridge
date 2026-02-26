#!/usr/bin/env python3
"""Test LLMOS Bridge with a real Claude LLM via LangChain.

This script starts a real daemon (via TestClient), generates LangChain tools,
connects Claude, and has it execute real tasks through the tool-calling chain.

Usage:
    ANTHROPIC_API_KEY=sk-... python scripts/test_with_llm.py
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import threading
import time
from pathlib import Path

# Ensure packages are importable
sys.path.insert(0, str(Path(__file__).parent.parent / "packages" / "llmos-bridge"))
sys.path.insert(0, str(Path(__file__).parent.parent / "packages" / "langchain-llmos"))


def main() -> None:
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        print("ERROR: Set ANTHROPIC_API_KEY environment variable")
        sys.exit(1)

    # -------------------------------------------------------------------------
    # Step 1: Start the daemon (in-process via TestClient)
    # -------------------------------------------------------------------------
    print("=" * 70)
    print("LLMOS Bridge — Real LLM Integration Test")
    print("=" * 70)
    print()

    from fastapi.testclient import TestClient

    from llmos_bridge.api.server import create_app
    from llmos_bridge.config import Settings

    tmp_dir = Path(tempfile.mkdtemp(prefix="llmos_test_"))
    settings = Settings(
        memory={
            "state_db_path": str(tmp_dir / "state.db"),
            "vector_db_path": str(tmp_dir / "vector"),
        },
        logging={"level": "warning", "format": "console", "audit_file": None},
        modules={"enabled": ["filesystem", "os_exec"]},
        security={"require_approval_for": []},
    )
    app = create_app(settings=settings)

    with TestClient(app, raise_server_exceptions=True) as tc:
        print("[1/5] Daemon started (in-process)")

        # Check health
        health = tc.get("/health").json()
        print(f"  Status: {health['status']}")
        print(f"  Modules loaded: {health['modules_loaded']}")
        print()

        # ---------------------------------------------------------------------
        # Step 2: Wire the LangChain SDK to the daemon
        # ---------------------------------------------------------------------
        from langchain_llmos.client import LLMOSClient
        from langchain_llmos.toolkit import LLMOSToolkit

        toolkit = LLMOSToolkit.__new__(LLMOSToolkit)
        toolkit._base_url = str(tc.base_url)
        toolkit._api_token = None
        toolkit._timeout = 30.0
        toolkit._manifests = None
        toolkit._system_prompt = None
        toolkit._async_client = None

        client = LLMOSClient.__new__(LLMOSClient)
        client._http = tc
        client._base_url = str(tc.base_url)
        client._api_token = None
        client._timeout = 30.0
        toolkit._client = client

        tools = toolkit.get_tools(max_permission="local_worker")
        system_prompt = toolkit.get_system_prompt()

        print(f"[2/5] SDK connected — {len(tools)} tools available:")
        for t in tools:
            print(f"  - {t.name}")
        print(f"  System prompt: {len(system_prompt)} chars")
        print()

        # ---------------------------------------------------------------------
        # Step 3: Create the Claude agent
        # ---------------------------------------------------------------------
        from langchain_anthropic import ChatAnthropic
        from langchain_core.messages import HumanMessage, SystemMessage

        llm = ChatAnthropic(
            model="claude-sonnet-4-20250514",
            api_key=api_key,
            max_tokens=1024,
        )
        llm_with_tools = llm.bind_tools(tools)

        print("[3/5] Claude connected (claude-sonnet-4-20250514)")
        print()

        # ---------------------------------------------------------------------
        # Step 4: Test 1 — Simple file read
        # ---------------------------------------------------------------------
        print("-" * 70)
        print("TEST 1: Ask Claude to read /etc/hostname")
        print("-" * 70)

        messages = [
            SystemMessage(content=system_prompt),
            HumanMessage(content="Read the file /etc/hostname and tell me what it contains."),
        ]

        response = llm_with_tools.invoke(messages)
        print(f"  Claude response type: {type(response).__name__}")
        print(f"  Content: {response.content[:200] if response.content else '(no text)'}")

        if response.tool_calls:
            print(f"  Tool calls: {len(response.tool_calls)}")
            for tc_item in response.tool_calls:
                print(f"    - {tc_item['name']}({tc_item['args']})")

                # Execute the tool
                tool = next((t for t in tools if t.name == tc_item["name"]), None)
                if tool:
                    result = tool.invoke(tc_item["args"])
                    print(f"    Result: {result[:200]}")

                    # Feed result back to Claude
                    from langchain_core.messages import ToolMessage

                    messages.append(response)
                    messages.append(
                        ToolMessage(content=result, tool_call_id=tc_item["id"])
                    )

            # Get Claude's final response
            final = llm_with_tools.invoke(messages)
            print(f"\n  Claude's final answer: {final.content[:300]}")
        else:
            print("  (No tool calls — Claude answered directly)")

        print()

        # ---------------------------------------------------------------------
        # Step 5: Test 2 — Write + Read chain
        # ---------------------------------------------------------------------
        print("-" * 70)
        print("TEST 2: Ask Claude to create a file and read it back")
        print("-" * 70)

        test_file = str(tmp_dir / "claude_created.txt")
        messages2 = [
            SystemMessage(content=system_prompt),
            HumanMessage(
                content=f"Write the text 'Hello from LLMOS Bridge!' to the file {test_file}, "
                f"then read it back to confirm it was written correctly."
            ),
        ]

        # Agentic loop — let Claude call tools until it stops
        MAX_TURNS = 6
        for turn in range(MAX_TURNS):
            response = llm_with_tools.invoke(messages2)
            messages2.append(response)

            if not response.tool_calls:
                print(f"\n  Claude's final answer (turn {turn + 1}):")
                print(f"  {response.content[:400]}")
                break

            print(f"  Turn {turn + 1}: {len(response.tool_calls)} tool call(s)")
            for tc_item in response.tool_calls:
                print(f"    - {tc_item['name']}({json.dumps(tc_item['args'])[:120]})")
                tool = next((t for t in tools if t.name == tc_item["name"]), None)
                if tool:
                    try:
                        result = tool.invoke(tc_item["args"])
                        print(f"      Result: {result[:150]}")
                    except Exception as exc:
                        result = json.dumps({"error": str(exc)})
                        print(f"      Error: {exc}")
                    from langchain_core.messages import ToolMessage

                    messages2.append(
                        ToolMessage(content=result, tool_call_id=tc_item["id"])
                    )
        else:
            print(f"  (Stopped after {MAX_TURNS} turns)")

        # Verify the file was actually created
        print()
        if Path(test_file).exists():
            content = Path(test_file).read_text()
            print(f"  FILE VERIFICATION: {test_file}")
            print(f"  Content: '{content}'")
            print(f"  STATUS: {'PASS' if 'Hello from LLMOS Bridge' in content else 'FAIL'}")
        else:
            print(f"  FILE VERIFICATION: {test_file} — NOT FOUND (FAIL)")

        print()

        # ---------------------------------------------------------------------
        # Step 6: Test 3 — Run a command
        # ---------------------------------------------------------------------
        print("-" * 70)
        print("TEST 3: Ask Claude to run a command")
        print("-" * 70)

        messages3 = [
            SystemMessage(content=system_prompt),
            HumanMessage(content="Run the command 'uname -a' and tell me about the system."),
        ]

        for turn in range(MAX_TURNS):
            response = llm_with_tools.invoke(messages3)
            messages3.append(response)

            if not response.tool_calls:
                print(f"\n  Claude's final answer (turn {turn + 1}):")
                print(f"  {response.content[:400]}")
                break

            for tc_item in response.tool_calls:
                print(f"  Turn {turn + 1}: {tc_item['name']}({json.dumps(tc_item['args'])[:120]})")
                tool = next((t for t in tools if t.name == tc_item["name"]), None)
                if tool:
                    try:
                        result = tool.invoke(tc_item["args"])
                        print(f"    Result: {result[:200]}")
                    except Exception as exc:
                        result = json.dumps({"error": str(exc)})
                        print(f"    Error: {exc}")
                    from langchain_core.messages import ToolMessage

                    messages3.append(
                        ToolMessage(content=result, tool_call_id=tc_item["id"])
                    )

        print()

    # -------------------------------------------------------------------------
    # Step 7: Test 4 — Approval flow with Claude
    # -------------------------------------------------------------------------
    print("-" * 70)
    print("TEST 4: Approval required — Claude triggers run_command, auto-approved in background")
    print("-" * 70)

    # Create a NEW daemon instance with approval requirements.
    approval_settings = Settings(
        memory={
            "state_db_path": str(tmp_dir / "approval_state.db"),
            "vector_db_path": str(tmp_dir / "approval_vector"),
        },
        logging={"level": "warning", "format": "console", "audit_file": None},
        modules={"enabled": ["filesystem", "os_exec"]},
        security={
            "require_approval_for": ["os_exec.run_command"],
            "approval_timeout_seconds": 30,
        },
    )
    approval_app = create_app(settings=approval_settings)

    with TestClient(approval_app, raise_server_exceptions=False) as atc:
        # Wire SDK to approval daemon.
        approval_toolkit = LLMOSToolkit.__new__(LLMOSToolkit)
        approval_toolkit._base_url = str(atc.base_url)
        approval_toolkit._api_token = None
        approval_toolkit._timeout = 30.0
        approval_toolkit._manifests = None
        approval_toolkit._system_prompt = None
        approval_toolkit._async_client = None

        approval_client = LLMOSClient.__new__(LLMOSClient)
        approval_client._http = atc
        approval_client._base_url = str(atc.base_url)
        approval_client._api_token = None
        approval_client._timeout = 30.0
        approval_toolkit._client = approval_client

        approval_tools = approval_toolkit.get_tools(max_permission="local_worker")
        approval_prompt = approval_toolkit.get_system_prompt()
        llm_approval = llm.bind_tools(approval_tools)

        print(f"  Approval daemon started — {len(approval_tools)} tools")

        # Background thread that auto-approves after detecting pending approval.
        approval_log = []

        def auto_approver():
            """Poll for pending approvals and approve them."""
            for _ in range(200):
                time.sleep(0.1)
                try:
                    resp = atc.get("/plans")
                    if resp.status_code != 200:
                        continue
                    plans = resp.json().get("plans", [])
                    for p in plans:
                        pid = p["plan_id"]
                        pending_resp = atc.get(f"/plans/{pid}/pending-approvals")
                        if pending_resp.status_code == 200:
                            pending = pending_resp.json()
                            for req in pending:
                                approve_resp = atc.post(
                                    f"/plans/{pid}/actions/{req['action_id']}/approve",
                                    json={
                                        "decision": "approve",
                                        "approved_by": "auto_approver",
                                        "reason": "Auto-approved by test script",
                                    },
                                )
                                approval_log.append({
                                    "plan_id": pid,
                                    "action_id": req["action_id"],
                                    "status": approve_resp.status_code,
                                })
                                print(f"  [AUTO-APPROVER] Approved {pid}/{req['action_id']}")
                                return
                except Exception as exc:
                    approval_log.append({"error": str(exc)})

        approver_thread = threading.Thread(target=auto_approver, daemon=True)
        approver_thread.start()

        messages4 = [
            SystemMessage(content=approval_prompt),
            HumanMessage(content="Run the command 'whoami' and tell me the result."),
        ]

        for turn in range(MAX_TURNS):
            response = llm_approval.invoke(messages4)
            messages4.append(response)

            if not response.tool_calls:
                print(f"\n  Claude's final answer (turn {turn + 1}):")
                print(f"  {response.content[:400]}")
                break

            for tc_item in response.tool_calls:
                print(f"  Turn {turn + 1}: {tc_item['name']}({json.dumps(tc_item['args'])[:120]})")
                tool = next((t for t in approval_tools if t.name == tc_item["name"]), None)
                if tool:
                    try:
                        result = tool.invoke(tc_item["args"])
                        parsed = json.loads(result)
                        if parsed.get("status") == "awaiting_approval":
                            print(f"    AWAITING APPROVAL: {parsed.get('message')}")
                        else:
                            print(f"      Result: {result[:200]}")
                    except Exception as exc:
                        result = json.dumps({"error": str(exc)})
                        print(f"      Error: {exc}")
                    from langchain_core.messages import ToolMessage

                    messages4.append(
                        ToolMessage(content=result, tool_call_id=tc_item["id"])
                    )

        approver_thread.join(timeout=5)
        print(f"\n  Auto-approver log: {approval_log}")
        print(f"  STATUS: {'PASS' if approval_log and approval_log[0].get('status') == 200 else 'CHECK'}")

        print()
        print("=" * 70)
        print("ALL TESTS COMPLETE (including approval flow)")
        print("=" * 70)


if __name__ == "__main__":
    main()
