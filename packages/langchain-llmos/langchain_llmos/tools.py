"""LangChain tool wrappers for LLMOS Bridge actions."""

from __future__ import annotations

import json
import uuid
from typing import Any, Type

from langchain_core.tools import BaseTool
from pydantic import BaseModel, create_model

from langchain_llmos.client import LLMOSClient


class LLMOSActionTool(BaseTool):
    """A LangChain tool that wraps a single LLMOS Bridge action.

    The tool constructs a minimal IML plan containing only this action
    and submits it synchronously (or asynchronously via ``_arun``).

    Attributes:
        module_id:    The LLMOS Bridge module (e.g. "filesystem").
        action_name:  The action to invoke (e.g. "read_file").
        client:       The LLMOSClient instance to use (sync).
        async_client: Optional AsyncLLMOSClient for ``_arun`` (shared, not created per call).
    """

    module_id: str
    action_name: str
    client: Any  # LLMOSClient — cannot use the type directly in Pydantic v2 field
    async_client: Any = None  # AsyncLLMOSClient — shared instance for async calls

    class Config:
        arbitrary_types_allowed = True

    def _run(self, **kwargs: Any) -> str:
        """Execute the action synchronously and return the result as JSON."""
        plan = self._build_plan(kwargs)
        result = self.client.submit_plan(plan, async_execution=False)
        return _extract_action_result(result)

    async def _arun(self, **kwargs: Any) -> str:
        """Execute the action asynchronously and return the result as JSON."""
        plan = self._build_plan(kwargs)

        if self.async_client is not None:
            result = await self.async_client.submit_plan(plan, async_execution=False)
        else:
            # Fallback: create a one-shot async client from sync client config
            import httpx

            base_url = str(self.client._http.base_url)
            headers = dict(self.client._http.headers)
            async with httpx.AsyncClient(
                base_url=base_url, headers=headers, timeout=300.0
            ) as http:
                resp = await http.post(
                    "/plans",
                    json={"plan": plan, "async_execution": False},
                )
                resp.raise_for_status()
                result = resp.json()

        return _extract_action_result(result)

    def _build_plan(self, params: dict[str, Any]) -> dict[str, Any]:
        # Strip None values injected by LangChain/Pydantic for optional fields
        # with default=None.  The server-side Pydantic models use their own
        # defaults (which may differ from None), so sending null would cause
        # a validation error.
        clean_params = {k: v for k, v in params.items() if v is not None}
        return {
            "plan_id": str(uuid.uuid4()),
            "protocol_version": "2.0",
            "description": f"LangChain: {self.module_id}.{self.action_name}",
            "actions": [
                {
                    "id": "action",
                    "action": self.action_name,
                    "module": self.module_id,
                    "params": clean_params,
                }
            ],
        }


def _extract_action_result(plan_result: dict[str, Any]) -> str:
    """Extract the action result from a completed plan response.

    Returns the first action's result as JSON, or the full plan response
    if no action results are found.  If the action is awaiting approval,
    returns a structured status message so the LLM can inform the user.
    """
    actions = plan_result.get("actions", [])
    if actions and len(actions) == 1:
        action = actions[0]
        # Handle approval-related statuses.
        action_status = action.get("status")
        if action_status == "awaiting_approval":
            return json.dumps({
                "status": "awaiting_approval",
                "message": "This action requires user approval before execution.",
                "plan_id": plan_result.get("plan_id"),
                "action_id": action.get("action_id"),
            }, default=str)
        if action.get("result") is not None:
            return json.dumps(action["result"], default=str)
        if action.get("error"):
            return json.dumps({"error": action["error"]}, default=str)
    return json.dumps(plan_result, default=str)


def _json_schema_to_pydantic(schema: dict[str, Any], model_name: str) -> Type[BaseModel]:
    """Create a Pydantic model from a JSONSchema dict for LangChain tool args."""
    properties = schema.get("properties", {})
    required = set(schema.get("required", []))

    type_map = {
        "string": (str, ...),
        "integer": (int, ...),
        "number": (float, ...),
        "boolean": (bool, ...),
        "object": (dict, ...),
        "array": (list, ...),
    }

    fields: dict[str, Any] = {}
    for field_name, field_schema in properties.items():
        json_type = field_schema.get("type", "string")
        py_type, _ = type_map.get(json_type, (Any, ...))
        if field_name in required:
            fields[field_name] = (py_type, ...)
        else:
            default = field_schema.get("default", None)
            fields[field_name] = (py_type | None, default)

    return create_model(model_name, **fields)
