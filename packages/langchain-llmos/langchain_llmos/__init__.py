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
"""

__version__ = "0.1.0"

from langchain_llmos.client import AsyncLLMOSClient, LLMOSClient
from langchain_llmos.toolkit import LLMOSToolkit
from langchain_llmos.tools import LLMOSActionTool

__all__ = ["LLMOSToolkit", "LLMOSClient", "AsyncLLMOSClient", "LLMOSActionTool"]
