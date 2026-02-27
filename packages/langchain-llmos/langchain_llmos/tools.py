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


def _format_security_rejection(rejection: dict[str, Any]) -> dict[str, Any]:
    """Format a security rejection into a structured response for the LLM."""
    source = rejection.get("source", "unknown")

    # Build a human-readable threat summary.
    if source == "scanner_pipeline":
        threat_types = rejection.get("threat_types", [])
        patterns = rejection.get("matched_patterns", [])
        summary_parts = []
        if threat_types:
            summary_parts.append(f"Detected threats: {', '.join(threat_types)}")
        if patterns:
            shown = patterns[:5]
            suffix = f" (+{len(patterns) - 5} more)" if len(patterns) > 5 else ""
            summary_parts.append(f"Matched patterns: {', '.join(shown)}{suffix}")
        threat_summary = ". ".join(summary_parts) if summary_parts else "Plan flagged by scanner pipeline."
    elif source == "intent_verifier":
        reasoning = rejection.get("reasoning", "")
        threats = rejection.get("threats", [])
        if reasoning:
            threat_summary = reasoning
        elif threats:
            descs = [t.get("description", t.get("type", "")) for t in threats]
            threat_summary = "; ".join(d for d in descs if d)
        else:
            threat_summary = "Plan flagged by intent verification."
    else:
        threat_summary = "Plan rejected by security layer."

    result: dict[str, Any] = {
        "status": "security_rejected",
        "source": source,
        "verdict": rejection.get("verdict", "reject"),
        "threat_summary": threat_summary,
        "recommendations": rejection.get("recommendations", []),
        "guidance": (
            "Explain to the user in plain language why their request was flagged. "
            "Do NOT repeat the flagged content. Suggest how they can rephrase or "
            "restructure the request to avoid triggering security scanners."
        ),
    }
    risk_score = rejection.get("risk_score")
    if risk_score is not None:
        result["risk_score"] = risk_score
    risk_level = rejection.get("risk_level")
    if risk_level is not None:
        result["risk_level"] = risk_level
    clarification = rejection.get("clarification_needed")
    if clarification is not None:
        result["clarification_needed"] = clarification
        result["guidance"] = (
            "The security layer needs clarification about the user's intent. "
            "Ask the user to clarify their request based on the clarification details."
        )
    return result


# Maximum characters for a single action result returned to the LLM.
# Results beyond this size are truncated to avoid blowing up the context window.
_MAX_RESULT_CHARS = 50_000


def _truncate_result(text: str, max_chars: int = _MAX_RESULT_CHARS) -> str:
    """Truncate *text* if it exceeds *max_chars*, appending an indicator."""
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + f"\n... [TRUNCATED — {len(text) - max_chars:,} chars omitted]"


def _extract_action_result(plan_result: dict[str, Any]) -> str:
    """Extract the action result from a completed plan response.

    Returns the first action's result as JSON, or the full plan response
    if no action results are found.  If the action is awaiting approval,
    returns a structured status message so the LLM can inform the user.

    Includes structured alternatives (Negotiation Protocol) when an action
    fails, so the LLM can propose recovery actions.
    """
    # Plan-level rejection: scanner pipeline or intent verifier.
    plan_status = plan_result.get("status")
    rejection = plan_result.get("rejection_details")
    if plan_status == "failed" and rejection:
        return json.dumps(_format_security_rejection(rejection), default=str)

    actions = plan_result.get("actions", [])

    # Failed plan with no actions = pre-execution failure (no rejection details).
    if plan_status == "failed" and not actions:
        return json.dumps(
            {"status": "failed", "error": plan_result.get("message", "Plan execution failed")},
            default=str,
        )

    if actions and len(actions) == 1:
        action = actions[0]
        # Handle approval-related statuses.
        action_status = action.get("status")
        if action_status == "awaiting_approval":
            resp: dict[str, Any] = {
                "status": "awaiting_approval",
                "message": "This action requires user approval before execution.",
                "plan_id": plan_result.get("plan_id"),
                "action_id": action.get("action_id"),
            }
            # Include clarification options if the approval supports them.
            if action.get("clarification_options"):
                resp["clarification_options"] = action["clarification_options"]
            return json.dumps(resp, default=str)
        if action.get("result") is not None:
            return _truncate_result(json.dumps(action["result"], default=str))
        if action.get("error"):
            error_msg = str(action["error"])
            error_resp: dict[str, Any] = {"error": error_msg}

            # Permission error: provide structured recovery guidance
            if "PermissionNotGrantedError" in error_msg or "permission" in error_msg.lower():
                error_resp["status"] = "permission_denied"
                error_resp["recovery"] = {
                    "action": "request_permission",
                    "module": "security",
                    "guidance": (
                        "The required OS-level permission has not been granted. "
                        "Use the security module's 'request_permission' action to "
                        "request it before retrying this action."
                    ),
                }
            # Rate limit error: provide retry guidance
            elif "RateLimitExceededError" in error_msg or "rate limit" in error_msg.lower():
                error_resp["status"] = "rate_limited"
                error_resp["recovery"] = {
                    "guidance": (
                        "This action has been rate-limited. Wait a moment "
                        "before retrying."
                    ),
                }
            # Intent verification rejection
            elif "SuspiciousIntentError" in error_msg or "IntentVerification" in error_msg:
                error_resp["status"] = "intent_rejected"
                error_resp["recovery"] = {
                    "guidance": (
                        "The plan was flagged by the security analysis layer. "
                        "Review the threat details and either modify the plan to "
                        "address the concerns or request the user to adjust permissions."
                    ),
                }

            # Negotiation Protocol: include structured alternatives for the LLM.
            alternatives = action.get("alternatives", [])
            if alternatives:
                error_resp["alternatives"] = alternatives
            return _truncate_result(json.dumps(error_resp, default=str))
    return _truncate_result(json.dumps(plan_result, default=str))


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
