"""Unit tests for the ApprovalGate coordination module."""

from __future__ import annotations

import asyncio
import time

import pytest

from llmos_bridge.orchestration.approval import (
    ApprovalDecision,
    ApprovalGate,
    ApprovalRequest,
    ApprovalResponse,
)


def _make_request(
    plan_id: str = "plan-1",
    action_id: str = "act-1",
    module: str = "filesystem",
    action_name: str = "delete_file",
) -> ApprovalRequest:
    return ApprovalRequest(
        plan_id=plan_id,
        action_id=action_id,
        module=module,
        action_name=action_name,
        params={"path": "/tmp/test.txt"},
        risk_level="high",
        description=f"Delete file via {module}.{action_name}",
    )


# ---------------------------------------------------------------------------
# Basic request/submit flow
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestApprovalGateBasic:
    @pytest.mark.asyncio
    async def test_approve_wakes_up_waiter(self) -> None:
        gate = ApprovalGate(default_timeout=5.0)
        request = _make_request()

        async def _approve_after_delay() -> None:
            await asyncio.sleep(0.05)
            gate.submit_decision(
                "plan-1", "act-1",
                ApprovalResponse(decision=ApprovalDecision.APPROVE, approved_by="user1"),
            )

        asyncio.create_task(_approve_after_delay())
        response = await gate.request_approval(request)

        assert response.decision == ApprovalDecision.APPROVE
        assert response.approved_by == "user1"
        assert gate.pending_count == 0

    @pytest.mark.asyncio
    async def test_reject_wakes_up_waiter(self) -> None:
        gate = ApprovalGate(default_timeout=5.0)
        request = _make_request()

        async def _reject() -> None:
            await asyncio.sleep(0.05)
            gate.submit_decision(
                "plan-1", "act-1",
                ApprovalResponse(decision=ApprovalDecision.REJECT, reason="too risky"),
            )

        asyncio.create_task(_reject())
        response = await gate.request_approval(request)

        assert response.decision == ApprovalDecision.REJECT
        assert response.reason == "too risky"

    @pytest.mark.asyncio
    async def test_skip_decision(self) -> None:
        gate = ApprovalGate(default_timeout=5.0)
        request = _make_request()

        async def _skip() -> None:
            await asyncio.sleep(0.05)
            gate.submit_decision(
                "plan-1", "act-1",
                ApprovalResponse(decision=ApprovalDecision.SKIP),
            )

        asyncio.create_task(_skip())
        response = await gate.request_approval(request)

        assert response.decision == ApprovalDecision.SKIP

    @pytest.mark.asyncio
    async def test_modify_returns_modified_params(self) -> None:
        gate = ApprovalGate(default_timeout=5.0)
        request = _make_request()
        new_params = {"path": "/tmp/safe.txt"}

        async def _modify() -> None:
            await asyncio.sleep(0.05)
            gate.submit_decision(
                "plan-1", "act-1",
                ApprovalResponse(
                    decision=ApprovalDecision.MODIFY,
                    modified_params=new_params,
                ),
            )

        asyncio.create_task(_modify())
        response = await gate.request_approval(request)

        assert response.decision == ApprovalDecision.MODIFY
        assert response.modified_params == new_params


# ---------------------------------------------------------------------------
# Timeout
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestApprovalGateTimeout:
    @pytest.mark.asyncio
    async def test_timeout_rejects_by_default(self) -> None:
        gate = ApprovalGate(default_timeout=0.1, default_timeout_behavior="reject")
        request = _make_request()

        response = await gate.request_approval(request)

        assert response.decision == ApprovalDecision.REJECT
        assert "timed out" in (response.reason or "")

    @pytest.mark.asyncio
    async def test_timeout_skips_when_configured(self) -> None:
        gate = ApprovalGate(default_timeout=0.1, default_timeout_behavior="skip")
        request = _make_request()

        response = await gate.request_approval(request)

        assert response.decision == ApprovalDecision.SKIP
        assert "timed out" in (response.reason or "")

    @pytest.mark.asyncio
    async def test_per_request_timeout_override(self) -> None:
        gate = ApprovalGate(default_timeout=10.0)
        request = _make_request()

        response = await gate.request_approval(request, timeout=0.1)

        assert response.decision == ApprovalDecision.REJECT

    @pytest.mark.asyncio
    async def test_per_request_timeout_behavior_override(self) -> None:
        gate = ApprovalGate(default_timeout=10.0, default_timeout_behavior="reject")
        request = _make_request()

        response = await gate.request_approval(
            request, timeout=0.1, timeout_behavior="skip"
        )

        assert response.decision == ApprovalDecision.SKIP


# ---------------------------------------------------------------------------
# Auto-approve (APPROVE_ALWAYS)
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestApprovalGateAutoApprove:
    @pytest.mark.asyncio
    async def test_approve_always_adds_to_auto_approve(self) -> None:
        gate = ApprovalGate(default_timeout=5.0)
        request = _make_request()

        async def _approve_always() -> None:
            await asyncio.sleep(0.05)
            gate.submit_decision(
                "plan-1", "act-1",
                ApprovalResponse(decision=ApprovalDecision.APPROVE_ALWAYS),
            )

        asyncio.create_task(_approve_always())
        response = await gate.request_approval(request)

        assert response.decision == ApprovalDecision.APPROVE_ALWAYS
        assert gate.is_auto_approved("filesystem", "delete_file")

    def test_is_auto_approved_false_by_default(self) -> None:
        gate = ApprovalGate()
        assert not gate.is_auto_approved("filesystem", "delete_file")

    def test_clear_auto_approvals(self) -> None:
        gate = ApprovalGate()
        gate._auto_approve.add("filesystem.delete_file")
        assert gate.is_auto_approved("filesystem", "delete_file")

        gate.clear_auto_approvals()
        assert not gate.is_auto_approved("filesystem", "delete_file")


# ---------------------------------------------------------------------------
# Pending query
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestApprovalGatePending:
    @pytest.mark.asyncio
    async def test_get_pending_lists_waiting_requests(self) -> None:
        gate = ApprovalGate(default_timeout=5.0)
        request = _make_request()

        # Start a request but don't resolve it yet.
        task = asyncio.create_task(gate.request_approval(request))
        await asyncio.sleep(0.02)  # Let the coroutine register

        pending = gate.get_pending()
        assert len(pending) == 1
        assert pending[0].action_id == "act-1"

        # Clean up
        gate.submit_decision("plan-1", "act-1", ApprovalResponse(decision=ApprovalDecision.REJECT))
        await task

    @pytest.mark.asyncio
    async def test_get_pending_filters_by_plan(self) -> None:
        gate = ApprovalGate(default_timeout=5.0)
        req1 = _make_request(plan_id="plan-1", action_id="a1")
        req2 = _make_request(plan_id="plan-2", action_id="a2")

        task1 = asyncio.create_task(gate.request_approval(req1))
        task2 = asyncio.create_task(gate.request_approval(req2))
        await asyncio.sleep(0.02)

        assert len(gate.get_pending(plan_id="plan-1")) == 1
        assert len(gate.get_pending(plan_id="plan-2")) == 1
        assert len(gate.get_pending()) == 2

        # Clean up
        gate.submit_decision("plan-1", "a1", ApprovalResponse(decision=ApprovalDecision.REJECT))
        gate.submit_decision("plan-2", "a2", ApprovalResponse(decision=ApprovalDecision.REJECT))
        await task1
        await task2

    def test_submit_for_nonexistent_returns_false(self) -> None:
        gate = ApprovalGate()
        result = gate.submit_decision(
            "nope", "nope",
            ApprovalResponse(decision=ApprovalDecision.APPROVE),
        )
        assert result is False


# ---------------------------------------------------------------------------
# Concurrent requests
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestApprovalGateConcurrent:
    @pytest.mark.asyncio
    async def test_multiple_concurrent_requests(self) -> None:
        gate = ApprovalGate(default_timeout=5.0)

        results: dict[str, ApprovalDecision] = {}

        async def _request(action_id: str) -> None:
            req = _make_request(action_id=action_id)
            resp = await gate.request_approval(req)
            results[action_id] = resp.decision

        async def _approve_all() -> None:
            await asyncio.sleep(0.05)
            gate.submit_decision("plan-1", "a1", ApprovalResponse(decision=ApprovalDecision.APPROVE))
            gate.submit_decision("plan-1", "a2", ApprovalResponse(decision=ApprovalDecision.REJECT))
            gate.submit_decision("plan-1", "a3", ApprovalResponse(decision=ApprovalDecision.SKIP))

        tasks = [
            asyncio.create_task(_request("a1")),
            asyncio.create_task(_request("a2")),
            asyncio.create_task(_request("a3")),
            asyncio.create_task(_approve_all()),
        ]
        await asyncio.gather(*tasks)

        assert results["a1"] == ApprovalDecision.APPROVE
        assert results["a2"] == ApprovalDecision.REJECT
        assert results["a3"] == ApprovalDecision.SKIP
        assert gate.pending_count == 0


# ---------------------------------------------------------------------------
# Serialization
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestApprovalSerialization:
    def test_request_to_dict(self) -> None:
        req = _make_request()
        d = req.to_dict()
        assert d["plan_id"] == "plan-1"
        assert d["action_id"] == "act-1"
        assert d["module"] == "filesystem"
        assert d["action"] == "delete_file"
        assert d["risk_level"] == "high"

    def test_response_to_dict(self) -> None:
        resp = ApprovalResponse(
            decision=ApprovalDecision.APPROVE,
            approved_by="admin",
            reason="looks safe",
        )
        d = resp.to_dict()
        assert d["decision"] == "approve"
        assert d["approved_by"] == "admin"
        assert d["reason"] == "looks safe"
        assert "timestamp" in d
