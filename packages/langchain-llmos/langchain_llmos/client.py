"""LLMOS Bridge HTTP client for the LangChain SDK.

Provides both synchronous (``LLMOSClient``) and asynchronous
(``AsyncLLMOSClient``) wrappers around the daemon REST API.
"""

from __future__ import annotations

from typing import Any

import httpx


class LLMOSClient:
    """Synchronous HTTP client for the LLMOS Bridge daemon.

    Usage::

        client = LLMOSClient(base_url="http://127.0.0.1:40000")
        modules = client.list_modules()
        manifest = client.get_module_manifest("filesystem")
        result = client.submit_plan(plan_dict, async_execution=False)
        prompt = client.get_system_prompt()
    """

    def __init__(
        self,
        base_url: str = "http://127.0.0.1:40000",
        api_token: str | None = None,
        timeout: float = 30.0,
    ) -> None:
        headers: dict[str, str] = {}
        if api_token:
            headers["X-LLMOS-Token"] = api_token

        self._base_url = base_url
        self._api_token = api_token
        self._timeout = timeout
        self._http = httpx.Client(
            base_url=base_url,
            headers=headers,
            timeout=timeout,
        )

    def health(self) -> dict[str, Any]:
        resp = self._http.get("/health")
        resp.raise_for_status()
        return resp.json()  # type: ignore[no-any-return]

    def list_modules(self) -> list[dict[str, Any]]:
        resp = self._http.get("/modules")
        resp.raise_for_status()
        return resp.json()  # type: ignore[no-any-return]

    def get_module_manifest(self, module_id: str) -> dict[str, Any]:
        resp = self._http.get(f"/modules/{module_id}")
        resp.raise_for_status()
        return resp.json()  # type: ignore[no-any-return]

    def submit_plan(
        self, plan: dict[str, Any], async_execution: bool = True
    ) -> dict[str, Any]:
        resp = self._http.post(
            "/plans",
            json={"plan": plan, "async_execution": async_execution},
            timeout=300.0 if not async_execution else 30.0,
        )
        resp.raise_for_status()
        return resp.json()  # type: ignore[no-any-return]

    def get_plan(self, plan_id: str) -> dict[str, Any]:
        resp = self._http.get(f"/plans/{plan_id}")
        resp.raise_for_status()
        return resp.json()  # type: ignore[no-any-return]

    def get_context(
        self,
        *,
        include_schemas: bool = True,
        include_examples: bool = True,
        max_actions_per_module: int | None = None,
        format: str = "full",
    ) -> dict[str, Any] | str:
        """Fetch the LLM system prompt / context from the daemon.

        Args:
            include_schemas:  Include full parameter schemas.
            include_examples: Include few-shot IML examples.
            max_actions_per_module: Limit actions shown per module.
            format: ``"full"`` returns JSON with metadata,
                    ``"prompt"`` returns raw text.

        Returns:
            JSON dict (format=full) or plain text string (format=prompt).
        """
        params: dict[str, Any] = {
            "include_schemas": include_schemas,
            "include_examples": include_examples,
            "format": format,
        }
        if max_actions_per_module is not None:
            params["max_actions_per_module"] = max_actions_per_module

        resp = self._http.get("/context", params=params)
        resp.raise_for_status()

        if format == "prompt":
            return resp.text
        return resp.json()  # type: ignore[no-any-return]

    def get_system_prompt(self, **kwargs: Any) -> str:
        """Convenience: fetch the system prompt as plain text.

        Accepts the same keyword arguments as ``get_context()``
        (except ``format`` which is forced to ``"prompt"``).
        """
        kwargs["format"] = "prompt"
        result = self.get_context(**kwargs)
        assert isinstance(result, str)
        return result

    def approve_action(
        self,
        plan_id: str,
        action_id: str,
        decision: str = "approve",
        reason: str | None = None,
        modified_params: dict[str, Any] | None = None,
        approved_by: str | None = None,
    ) -> dict[str, Any]:
        """Submit an approval decision for an action awaiting approval."""
        body: dict[str, Any] = {"decision": decision}
        if reason is not None:
            body["reason"] = reason
        if modified_params is not None:
            body["modified_params"] = modified_params
        if approved_by is not None:
            body["approved_by"] = approved_by
        resp = self._http.post(
            f"/plans/{plan_id}/actions/{action_id}/approve",
            json=body,
        )
        resp.raise_for_status()
        return resp.json()  # type: ignore[no-any-return]

    def get_pending_approvals(self, plan_id: str) -> list[dict[str, Any]]:
        """List pending approval requests for a plan."""
        resp = self._http.get(f"/plans/{plan_id}/pending-approvals")
        resp.raise_for_status()
        return resp.json()  # type: ignore[no-any-return]

    def submit_plan_group(
        self,
        plans: list[dict[str, Any]],
        group_id: str | None = None,
        max_concurrent: int = 10,
        timeout: int = 300,
    ) -> dict[str, Any]:
        """Submit multiple plans for parallel execution."""
        body: dict[str, Any] = {
            "plans": plans,
            "max_concurrent": max_concurrent,
            "timeout": timeout,
        }
        if group_id:
            body["group_id"] = group_id
        resp = self._http.post(
            "/plan-groups",
            json=body,
            timeout=float(timeout) + 10.0,
        )
        resp.raise_for_status()
        return resp.json()  # type: ignore[no-any-return]

    # ------------------------------------------------------------------
    # Intent Verifier endpoints
    # ------------------------------------------------------------------

    def get_intent_verifier_status(self) -> dict[str, Any]:
        """Get the current intent verifier status and configuration."""
        resp = self._http.get("/intent-verifier/status")
        resp.raise_for_status()
        return resp.json()  # type: ignore[no-any-return]

    def verify_plan_preview(self, plan: dict[str, Any]) -> dict[str, Any]:
        """Verify an IML plan without executing it (dry-run)."""
        resp = self._http.post("/intent-verifier/verify", json={"plan": plan})
        resp.raise_for_status()
        return resp.json()  # type: ignore[no-any-return]

    def get_threat_categories(self) -> list[dict[str, Any]]:
        """List all threat categories (built-in + custom)."""
        resp = self._http.get("/intent-verifier/categories")
        resp.raise_for_status()
        return resp.json()  # type: ignore[no-any-return]

    def register_threat_category(self, category: dict[str, Any]) -> dict[str, Any]:
        """Register a custom threat category at runtime."""
        resp = self._http.post("/intent-verifier/categories", json=category)
        resp.raise_for_status()
        return resp.json()  # type: ignore[no-any-return]

    def remove_threat_category(self, category_id: str) -> dict[str, Any]:
        """Remove a custom threat category."""
        resp = self._http.delete(f"/intent-verifier/categories/{category_id}")
        resp.raise_for_status()
        return resp.json()  # type: ignore[no-any-return]

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> "LLMOSClient":
        return self

    def __exit__(self, *_: Any) -> None:
        self.close()


class AsyncLLMOSClient:
    """Asynchronous HTTP client for the LLMOS Bridge daemon.

    Usage::

        async with AsyncLLMOSClient() as client:
            modules = await client.list_modules()
            result = await client.submit_plan(plan, async_execution=False)
            prompt = await client.get_system_prompt()
    """

    def __init__(
        self,
        base_url: str = "http://127.0.0.1:40000",
        api_token: str | None = None,
        timeout: float = 30.0,
    ) -> None:
        headers: dict[str, str] = {}
        if api_token:
            headers["X-LLMOS-Token"] = api_token

        self._http = httpx.AsyncClient(
            base_url=base_url,
            headers=headers,
            timeout=timeout,
        )

    async def health(self) -> dict[str, Any]:
        resp = await self._http.get("/health")
        resp.raise_for_status()
        return resp.json()  # type: ignore[no-any-return]

    async def list_modules(self) -> list[dict[str, Any]]:
        resp = await self._http.get("/modules")
        resp.raise_for_status()
        return resp.json()  # type: ignore[no-any-return]

    async def get_module_manifest(self, module_id: str) -> dict[str, Any]:
        resp = await self._http.get(f"/modules/{module_id}")
        resp.raise_for_status()
        return resp.json()  # type: ignore[no-any-return]

    async def submit_plan(
        self, plan: dict[str, Any], async_execution: bool = True
    ) -> dict[str, Any]:
        resp = await self._http.post(
            "/plans",
            json={"plan": plan, "async_execution": async_execution},
            timeout=300.0 if not async_execution else 30.0,
        )
        resp.raise_for_status()
        return resp.json()  # type: ignore[no-any-return]

    async def get_plan(self, plan_id: str) -> dict[str, Any]:
        resp = await self._http.get(f"/plans/{plan_id}")
        resp.raise_for_status()
        return resp.json()  # type: ignore[no-any-return]

    async def get_context(
        self,
        *,
        include_schemas: bool = True,
        include_examples: bool = True,
        max_actions_per_module: int | None = None,
        format: str = "full",
    ) -> dict[str, Any] | str:
        """Fetch the LLM system prompt / context from the daemon."""
        params: dict[str, Any] = {
            "include_schemas": include_schemas,
            "include_examples": include_examples,
            "format": format,
        }
        if max_actions_per_module is not None:
            params["max_actions_per_module"] = max_actions_per_module

        resp = await self._http.get("/context", params=params)
        resp.raise_for_status()

        if format == "prompt":
            return resp.text
        return resp.json()  # type: ignore[no-any-return]

    async def get_system_prompt(self, **kwargs: Any) -> str:
        """Convenience: fetch the system prompt as plain text."""
        kwargs["format"] = "prompt"
        result = await self.get_context(**kwargs)
        assert isinstance(result, str)
        return result

    async def approve_action(
        self,
        plan_id: str,
        action_id: str,
        decision: str = "approve",
        reason: str | None = None,
        modified_params: dict[str, Any] | None = None,
        approved_by: str | None = None,
    ) -> dict[str, Any]:
        """Submit an approval decision for an action awaiting approval."""
        body: dict[str, Any] = {"decision": decision}
        if reason is not None:
            body["reason"] = reason
        if modified_params is not None:
            body["modified_params"] = modified_params
        if approved_by is not None:
            body["approved_by"] = approved_by
        resp = await self._http.post(
            f"/plans/{plan_id}/actions/{action_id}/approve",
            json=body,
        )
        resp.raise_for_status()
        return resp.json()  # type: ignore[no-any-return]

    async def get_pending_approvals(self, plan_id: str) -> list[dict[str, Any]]:
        """List pending approval requests for a plan."""
        resp = await self._http.get(f"/plans/{plan_id}/pending-approvals")
        resp.raise_for_status()
        return resp.json()  # type: ignore[no-any-return]

    async def submit_plan_group(
        self,
        plans: list[dict[str, Any]],
        group_id: str | None = None,
        max_concurrent: int = 10,
        timeout: int = 300,
    ) -> dict[str, Any]:
        """Submit multiple plans for parallel execution."""
        body: dict[str, Any] = {
            "plans": plans,
            "max_concurrent": max_concurrent,
            "timeout": timeout,
        }
        if group_id:
            body["group_id"] = group_id
        resp = await self._http.post(
            "/plan-groups",
            json=body,
            timeout=float(timeout) + 10.0,
        )
        resp.raise_for_status()
        return resp.json()  # type: ignore[no-any-return]

    # ------------------------------------------------------------------
    # Intent Verifier endpoints
    # ------------------------------------------------------------------

    async def get_intent_verifier_status(self) -> dict[str, Any]:
        """Get the current intent verifier status and configuration."""
        resp = await self._http.get("/intent-verifier/status")
        resp.raise_for_status()
        return resp.json()  # type: ignore[no-any-return]

    async def verify_plan_preview(self, plan: dict[str, Any]) -> dict[str, Any]:
        """Verify an IML plan without executing it (dry-run)."""
        resp = await self._http.post("/intent-verifier/verify", json={"plan": plan})
        resp.raise_for_status()
        return resp.json()  # type: ignore[no-any-return]

    async def get_threat_categories(self) -> list[dict[str, Any]]:
        """List all threat categories (built-in + custom)."""
        resp = await self._http.get("/intent-verifier/categories")
        resp.raise_for_status()
        return resp.json()  # type: ignore[no-any-return]

    async def register_threat_category(self, category: dict[str, Any]) -> dict[str, Any]:
        """Register a custom threat category at runtime."""
        resp = await self._http.post("/intent-verifier/categories", json=category)
        resp.raise_for_status()
        return resp.json()  # type: ignore[no-any-return]

    async def remove_threat_category(self, category_id: str) -> dict[str, Any]:
        """Remove a custom threat category."""
        resp = await self._http.delete(f"/intent-verifier/categories/{category_id}")
        resp.raise_for_status()
        return resp.json()  # type: ignore[no-any-return]

    async def close(self) -> None:
        await self._http.aclose()

    async def __aenter__(self) -> "AsyncLLMOSClient":
        return self

    async def __aexit__(self, *_: Any) -> None:
        await self.close()
