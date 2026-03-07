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



    # Connect to LLMOS Bridge
    from langgraph.prebuilt import create_react_agent
    from langchain_anthropic import ChatAnthropic
    from langchain_llmos import LLMOSToolkit
    import os
    
    with LLMOSClient(base_url=args.base_url, api_token=args.api_token) as client:

        context = client.get_context()
        prompt = context["system_prompt"]
        toolkit = LLMOSToolkit()
        from langchain_ollama import ChatOllama

        from langgraph.prebuilt import create_react_agent
        from langchain_openai import ChatOpenAI
        from langchain_llmos import LLMOSToolkit
        import os

        toolkit = LLMOSToolkit()
        all_tools = toolkit.get_tools()
        tools = [t for t in all_tools if t.name.startswith(("os_exec", "filesystem"))]

        llm = ChatOpenAI(
            model="deepseek/deepseek-chat-v3-0324:free",
            api_key=os.environ.get("OPENROUTER_API_KEY"),
            base_url="https://openrouter.ai/api/v1"
        )

        agent = create_react_agent(llm, tools, prompt="You are a helpful assistant. Use the available tools directly, never write code or JSON.")

        result = agent.invoke({"messages": [("human", "Create a file at /tmp/test.txt with content 'hello from llama'")]})
        print(result["messages"][-1].content)


if __name__ == "__main__":
    main()
