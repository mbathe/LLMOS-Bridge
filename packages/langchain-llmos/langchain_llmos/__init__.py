"""langchain-llmos — LangChain integration for LLMOS Bridge.

Quick start::

    from langchain_llmos import LLMOSToolkit

    toolkit = LLMOSToolkit()          # Connects to local daemon on :40000
    tools = toolkit.get_tools()       # Returns list[BaseTool] for all modules
    system_prompt = toolkit.get_system_prompt()  # Dynamic system prompt

    # Use with any LangChain agent:
    from langchain.agents import AgentExecutor, create_tool_calling_agent
    agent = create_tool_calling_agent(llm, tools, prompt)
    executor = AgentExecutor(agent=agent, tools=tools)

Computer Use Agent (multi-provider)::

    from langchain_llmos import ComputerUseAgent

    # Anthropic (default)
    agent = ComputerUseAgent(provider="anthropic")
    # OpenAI
    agent = ComputerUseAgent(provider="openai", api_key="sk-...")
    # Ollama (local, free)
    agent = ComputerUseAgent(provider="ollama", model="llama3.2")

    result = await agent.run("Open the file manager")
"""

__version__ = "0.1.0"

from langchain_llmos.client import AsyncLLMOSClient, LLMOSClient
from langchain_llmos.toolkit import LLMOSToolkit
from langchain_llmos.tools import LLMOSActionTool

__all__ = [
    "LLMOSToolkit",
    "LLMOSClient",
    "AsyncLLMOSClient",
    "LLMOSActionTool",
]

# ComputerUseAgent and provider types (require optional SDK packages).
from langchain_llmos.agent import AgentResult, ComputerUseAgent, StepRecord

__all__ += ["ComputerUseAgent", "AgentResult", "StepRecord"]

try:
    from langchain_llmos.providers import (
        AgentLLMProvider,
        AnthropicProvider,
        OpenAICompatibleProvider,
        build_agent_provider,
    )

    __all__ += [
        "AgentLLMProvider",
        "AnthropicProvider",
        "OpenAICompatibleProvider",
        "build_agent_provider",
    ]
except ImportError:
    pass
