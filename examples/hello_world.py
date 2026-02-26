#!/usr/bin/env python3
"""LLMOS Bridge — Hello World example.

This script demonstrates the full end-to-end integration:

  1. Connect to the LLMOS Bridge daemon
  2. Fetch the dynamic system prompt
  3. Discover available tools (modules → LangChain BaseTool)
  4. Execute a simple IML plan (read a file)
  5. Display results

Prerequisites:
  - The LLMOS Bridge daemon must be running: ``llmos-bridge serve``
  - Python packages: ``pip install langchain-llmos``

Usage:
  python examples/hello_world.py
  python examples/hello_world.py --base-url http://192.168.1.10:40000
"""

from __future__ import annotations

import argparse
import json
import sys
import uuid


def main() -> None:
    parser = argparse.ArgumentParser(description="LLMOS Bridge Hello World")
    parser.add_argument(
        "--base-url",
        default="http://127.0.0.1:40000",
        help="LLMOS Bridge daemon URL (default: http://127.0.0.1:40000)",
    )
    parser.add_argument(
        "--api-token",
        default=None,
        help="API token if the daemon requires authentication",
    )
    args = parser.parse_args()

    from langchain_llmos import LLMOSClient, LLMOSToolkit

    # -----------------------------------------------------------------------
    # Step 1: Connect and check health
    # -----------------------------------------------------------------------
    print(f"Connecting to LLMOS Bridge at {args.base_url}...")

    with LLMOSClient(base_url=args.base_url, api_token=args.api_token) as client:
        health = client.health()
        print(f"  Status:   {health['status']}")
        print(f"  Version:  {health['version']}")
        print(f"  Protocol: {health['protocol_version']}")
        print(f"  Modules:  {health['modules_loaded']} loaded")
        print()

        # -------------------------------------------------------------------
        # Step 2: Fetch the system prompt
        # -------------------------------------------------------------------
        print("Fetching system prompt...")
        context = client.get_context()
        prompt = context["system_prompt"]
        print(f"  System prompt: {len(prompt)} characters")
        print(f"  Modules: {[m['module_id'] for m in context['modules']]}")
        print(f"  Total actions: {context['total_actions']}")
        print()

        # Show first 5 lines of the prompt
        print("  --- System prompt preview ---")
        for line in prompt.split("\n")[:5]:
            print(f"  | {line}")
        print("  | ...")
        print()

        # -------------------------------------------------------------------
        # Step 3: Discover tools via the Toolkit
        # -------------------------------------------------------------------
        print("Generating LangChain tools...")

    # Use the toolkit (separate context manager to demo lifecycle)
    with LLMOSToolkit(base_url=args.base_url, api_token=args.api_token) as toolkit:
        tools = toolkit.get_tools()
        print(f"  Generated {len(tools)} tools:")
        for tool in tools[:10]:
            print(f"    - {tool.name}")
        if len(tools) > 10:
            print(f"    ... and {len(tools) - 10} more")
        print()

        # -------------------------------------------------------------------
        # Step 4: Execute a simple plan
        # -------------------------------------------------------------------
        print("Executing a simple IML plan (read /etc/hostname)...")
        plan = {
            "plan_id": str(uuid.uuid4()),
            "protocol_version": "2.0",
            "description": "Hello World: read the hostname",
            "actions": [
                {
                    "id": "read_hostname",
                    "module": "filesystem",
                    "action": "read_file",
                    "params": {"path": "/etc/hostname"},
                }
            ],
        }

        read_tool = next(
            (t for t in tools if t.name == "filesystem__read_file"),
            None,
        )
        if read_tool is None:
            print("  ERROR: filesystem__read_file tool not found!")
            print("  Make sure the 'filesystem' module is enabled in the daemon.")
            sys.exit(1)

        result = read_tool.invoke({"path": "/etc/hostname"})
        print(f"  Result: {result}")
        print()

        # -------------------------------------------------------------------
        # Step 5: Show LangChain agent integration pattern
        # -------------------------------------------------------------------
        print("=" * 60)
        print("LangChain Agent integration pattern:")
        print("=" * 60)
        print("""
    from langchain_llmos import LLMOSToolkit
    from langchain_openai import ChatOpenAI
    from langchain.agents import AgentExecutor, create_tool_calling_agent
    from langchain_core.prompts import ChatPromptTemplate

    # Connect to LLMOS Bridge
    toolkit = LLMOSToolkit()
    tools = toolkit.get_tools()
    system_prompt = toolkit.get_system_prompt()

    # Create LangChain agent
    llm = ChatOpenAI(model="gpt-4o")
    prompt = ChatPromptTemplate.from_messages([
        ("system", system_prompt),
        ("human", "{input}"),
        ("placeholder", "{agent_scratchpad}"),
    ])
    agent = create_tool_calling_agent(llm, tools, prompt)
    executor = AgentExecutor(agent=agent, tools=tools, verbose=True)

    # Run!
    result = executor.invoke({"input": "List the files in /tmp"})
    print(result["output"])
""")
        print("Done!")


if __name__ == "__main__":
    main()
