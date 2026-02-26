"""Shadow Recorder — WorkflowReplayer.

Converts a WorkflowRecording into a single re-playable IMLPlan by merging
all captured plans into a sequential chain.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from llmos_bridge.logging import get_logger

if TYPE_CHECKING:
    from llmos_bridge.recording.models import WorkflowRecording

log = get_logger(__name__)


class WorkflowReplayer:
    """Generates a single IMLPlan that replays a WorkflowRecording."""

    def generate(self, recording: "WorkflowRecording") -> dict[str, Any]:
        """Merge all recorded plans into one sequential IMLPlan.

        Each plan's actions are prefixed so IDs don't collide.
        The first wave of plan N is chained after the last action of plan N-1.
        """
        all_actions: list[dict[str, Any]] = []
        prev_last_action_id: str | None = None

        for recorded in recording.plans:
            plan_actions = list(recorded.plan_data.get("actions", []))
            if not plan_actions:
                continue

            prefix = f"p{recorded.sequence}"

            # Build ID mapping: original → prefixed
            id_map: dict[str, str] = {
                act.get("id", f"act{i}"): f"{prefix}_{act.get('id', f'act{i}')}"
                for i, act in enumerate(plan_actions)
            }

            for act in plan_actions:
                original_id = act.get("id", "")
                new_act: dict[str, Any] = dict(act)
                new_act["id"] = id_map.get(original_id, f"{prefix}_{original_id}")

                # Remap depends_on to prefixed IDs
                if new_act.get("depends_on"):
                    new_act["depends_on"] = [
                        id_map.get(dep, f"{prefix}_{dep}")
                        for dep in new_act["depends_on"]
                    ]

                # Chain: actions with no original dependencies get chained to
                # the last action of the previous plan.
                original_deps = act.get("depends_on") or []
                if prev_last_action_id is not None and not original_deps:
                    new_act["depends_on"] = [prev_last_action_id]

                all_actions.append(new_act)

            # Track the last action ID in this plan for chaining
            last_original_id = plan_actions[-1].get("id", "")
            prev_last_action_id = id_map.get(last_original_id)

        return {
            "plan_id": f"replay-{recording.recording_id}",
            "protocol_version": "2.0",
            "description": f"Replay of '{recording.title}'",
            "execution_mode": "sequential",
            "metadata": {
                "source": "shadow_recorder",
                "recording_id": recording.recording_id,
                "original_plan_count": len(recording.plans),
            },
            "actions": all_actions,
        }

    def generate_llm_context(self, recording: "WorkflowRecording") -> str:
        """Return a human-readable summary of the recording for LLM reproduction."""
        lines = [
            f"# Workflow Recording: {recording.title}",
            f"Description: {recording.description}",
            f"Plans captured: {len(recording.plans)}",
            "",
        ]
        for rp in recording.plans:
            lines.append(
                f"## Step {rp.sequence}: Plan '{rp.plan_id}' "
                f"({rp.action_count} actions, status={rp.final_status})"
            )
            for act in rp.plan_data.get("actions", []):
                mod = act.get("module", "?")
                action = act.get("action", "?")
                act_id = act.get("id", "")
                params = act.get("params", {})
                lines.append(f"  - [{mod}.{action}] {act_id}: {params}")
        return "\n".join(lines)
