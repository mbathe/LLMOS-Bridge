"""LangChain toolkit for LLMOS Bridge.

Connects to the daemon, fetches the Capability Manifest for all loaded
modules, and auto-generates one LangChain BaseTool per action.

Usage::

    from langchain_llmos import LLMOSToolkit

    toolkit = LLMOSToolkit()
    tools = toolkit.get_tools()

    # Get the system prompt for the LLM:
    system_prompt = toolkit.get_system_prompt()

    # Or filter by module:
    fs_tools = toolkit.get_tools(modules=["filesystem"])

    # Or filter by permission level:
    safe_tools = toolkit.get_tools(max_permission="local_worker")
"""

from __future__ import annotations

from typing import Any

from langchain_core.tools import BaseTool

from langchain_llmos.client import AsyncLLMOSClient, LLMOSClient
from langchain_llmos.tools import LLMOSActionTool, _json_schema_to_pydantic

_PERMISSION_ORDER = ["readonly", "local_worker", "power_user", "unrestricted"]


class LLMOSToolkit:
    """Auto-generates LangChain tools from the LLMOS Bridge Capability Manifest.

    This is the main entry point for integrating LLMOS Bridge with LangChain.
    It connects to the daemon, discovers available modules, and generates
    LangChain-compatible tools + system prompts automatically.

    Args:
        base_url:       Daemon URL (default: http://127.0.0.1:40000).
        api_token:      Optional API token.
        timeout:        HTTP timeout in seconds.
    """

    def __init__(
        self,
        base_url: str = "http://127.0.0.1:40000",
        api_token: str | None = None,
        timeout: float = 30.0,
    ) -> None:
        self._client = LLMOSClient(base_url=base_url, api_token=api_token, timeout=timeout)
        self._async_client: AsyncLLMOSClient | None = None
        self._base_url = base_url
        self._api_token = api_token
        self._timeout = timeout
        self._manifests: list[dict[str, Any]] | None = None
        self._system_prompt: str | None = None

    def get_tools(
        self,
        modules: list[str] | None = None,
        max_permission: str = "local_worker",
    ) -> list[BaseTool]:
        """Return a list of LangChain tools for all matching actions.

        Args:
            modules:        Only include these module IDs. All modules if None.
            max_permission: Maximum permission level to include. Actions
                            requiring higher permissions are excluded.

        Returns:
            List of LangChain BaseTool instances, one per action.
        """
        manifests = self._load_manifests()
        tools: list[BaseTool] = []

        max_level = _PERMISSION_ORDER.index(max_permission) if max_permission in _PERMISSION_ORDER else 1

        for manifest in manifests:
            module_id = manifest["module_id"]
            if modules and module_id not in modules:
                continue

            for action in manifest.get("actions", []):
                action_name = action["name"]
                perm = action.get("permission_required", "local_worker")
                action_level = _PERMISSION_ORDER.index(perm) if perm in _PERMISSION_ORDER else 1
                if action_level > max_level:
                    continue

                tool = self._make_tool(module_id, action)
                tools.append(tool)

        return tools

    def get_system_prompt(self, **kwargs: Any) -> str:
        """Fetch the LLM system prompt from the daemon.

        The prompt is dynamically generated from the currently loaded modules,
        permission profile, IML v2 rules, and examples. It is designed to be
        injected as the system message for an LLM.

        Keyword args are forwarded to ``LLMOSClient.get_system_prompt()``:
            include_schemas, include_examples, max_actions_per_module.

        The result is cached — call ``refresh()`` to clear the cache.
        """
        if self._system_prompt is None:
            self._system_prompt = self._client.get_system_prompt(**kwargs)
        return self._system_prompt

    def get_context(self, **kwargs: Any) -> dict[str, Any] | str:
        """Fetch the full context (JSON with metadata) from the daemon.

        Keyword args are forwarded to ``LLMOSClient.get_context()``.
        """
        return self._client.get_context(**kwargs)

    def _make_tool(self, module_id: str, action: dict[str, Any]) -> BaseTool:
        params_schema = action.get("params_schema", {})
        args_schema = _json_schema_to_pydantic(
            params_schema,
            model_name=f"{module_id}_{action['name']}_args",
        )

        return LLMOSActionTool(
            name=f"{module_id}__{action['name']}",
            description=(
                f"[{module_id}] {action['description']} "
                f"(permission: {action.get('permission_required', 'local_worker')})"
            ),
            args_schema=args_schema,
            module_id=module_id,
            action_name=action["name"],
            client=self._client,
            async_client=self._get_async_client(),
        )

    def _get_async_client(self) -> AsyncLLMOSClient:
        """Lazily create a shared AsyncLLMOSClient for async tool calls."""
        if self._async_client is None:
            self._async_client = AsyncLLMOSClient(
                base_url=self._base_url,
                api_token=self._api_token,
                timeout=self._timeout,
            )
        return self._async_client

    def _load_manifests(self) -> list[dict[str, Any]]:
        if self._manifests is None:
            available = self._client.list_modules()
            self._manifests = []
            for m in available:
                if m.get("available"):
                    try:
                        manifest = self._client.get_module_manifest(m["module_id"])
                        self._manifests.append(manifest)
                    except Exception:
                        pass
        return self._manifests

    def execute_parallel(
        self,
        actions: list[dict[str, Any]],
        max_concurrent: int = 10,
        timeout: int = 300,
        group_id: str | None = None,
    ) -> dict[str, Any]:
        """Execute multiple actions in parallel via PlanGroup.

        Each action dict is wrapped into a single-action IML plan, then
        all plans are submitted as a group for parallel execution.

        Args:
            actions:        List of action dicts, each with keys:
                            ``module``, ``action``, ``params``, and optionally ``id``.
            max_concurrent: Maximum number of plans running concurrently.
            timeout:        Total timeout for the group (seconds).
            group_id:       Optional group identifier.

        Returns:
            Plan group response dict with ``status``, ``summary``, ``results``,
            ``errors``, ``duration``.
        """
        import uuid

        plans: list[dict[str, Any]] = []
        for i, action in enumerate(actions):
            action_id = action.get("id", f"a{i}")
            plan_id = f"parallel-{uuid.uuid4().hex[:8]}-{i}"
            plans.append({
                "plan_id": plan_id,
                "protocol_version": "2.0",
                "description": f"{action.get('module', '?')}.{action.get('action', '?')}",
                "actions": [
                    {
                        "id": action_id,
                        "module": action["module"],
                        "action": action["action"],
                        "params": action.get("params", {}),
                    }
                ],
            })

        return self._client.submit_plan_group(
            plans=plans,
            group_id=group_id,
            max_concurrent=max_concurrent,
            timeout=timeout,
        )

    # ------------------------------------------------------------------
    # Intent Verifier convenience methods
    # ------------------------------------------------------------------

    def get_intent_verifier_status(self) -> dict[str, Any]:
        """Check if intent verification is active and its configuration."""
        return self._client.get_intent_verifier_status()

    def verify_plan_preview(self, plan: dict[str, Any]) -> dict[str, Any]:
        """Preview verification result without executing the plan."""
        return self._client.verify_plan_preview(plan)

    def get_threat_categories(self) -> list[dict[str, Any]]:
        """List all registered threat categories."""
        return self._client.get_threat_categories()

    def refresh(self) -> None:
        """Force a refresh of the module manifest and system prompt cache."""
        self._manifests = None
        self._system_prompt = None

    def close(self) -> None:
        """Close all HTTP clients."""
        self._client.close()
        if self._async_client is not None:
            import asyncio
            try:
                loop = asyncio.get_running_loop()
                loop.create_task(self._async_client.close())
            except RuntimeError:
                asyncio.run(self._async_client.close())

    def __enter__(self) -> "LLMOSToolkit":
        return self

    def __exit__(self, *_: Any) -> None:
        self.close()
