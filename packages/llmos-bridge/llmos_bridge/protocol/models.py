"""IML Protocol v2 — Canonical data models.

All structures in an IML plan are defined here and validated through
Pydantic v2.  This module is the single source of truth for the protocol.
Do not add business logic here — only data shapes and their invariants.
"""

from __future__ import annotations

import re
import uuid
from enum import Enum
from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from llmos_bridge.protocol.constants import (
    ACTION_ID_MAX_LEN,
    ACTION_NAME_MAX_LEN,
    DEFAULT_ACTION_TIMEOUT_SECONDS,
    DEFAULT_MEMORY_TOP_K,
    DEFAULT_PERCEPTION_TIMEOUT_SECONDS,
    DEFAULT_RETRY_BACKOFF_FACTOR,
    DEFAULT_RETRY_DELAY_SECONDS,
    DEFAULT_RETRY_MAX_ATTEMPTS,
    LABEL_MAX_LEN,
    MAX_ACTION_TIMEOUT_SECONDS,
    MAX_ACTIONS_PER_PLAN,
    MAX_MEMORY_TOP_K,
    MAX_PERCEPTION_TIMEOUT_SECONDS,
    MAX_PLAN_DESCRIPTION_LEN,
    MAX_RETRY_DELAY_SECONDS,
    MAX_RETRY_MAX_ATTEMPTS,
    MAX_TAG_LEN,
    MAX_TAGS_PER_ACTION,
    MIN_ACTION_TIMEOUT_SECONDS,
    MODULE_ID_MAX_LEN,
    PLAN_ID_MAX_LEN,
    PROTOCOL_VERSION,
)

_ACTION_ID_RE = re.compile(r"^[a-zA-Z0-9_-]{1," + str(ACTION_ID_MAX_LEN) + r"}$")
_PLAN_ID_RE = re.compile(r"^[a-zA-Z0-9_-]{1," + str(PLAN_ID_MAX_LEN) + r"}$")
_MODULE_ID_RE = re.compile(r"^[a-z][a-z0-9_]{0," + str(MODULE_ID_MAX_LEN - 1) + r"}$")
_ACTION_NAME_RE = re.compile(r"^[a-z][a-z0-9_]{0," + str(ACTION_NAME_MAX_LEN - 1) + r"}$")


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------


class ExecutionMode(str, Enum):
    """How the plan executor should schedule independent actions."""

    SEQUENTIAL = "sequential"
    PARALLEL = "parallel"
    REACTIVE = "reactive"


class PlanMode(str, Enum):
    """How the plan was generated and what guarantees it carries.

    STANDARD:
        The LLM generated the plan directly (probabilistic).
        The Bridge validates structure and params but does not require a
        reasoning trace.  Suitable for all everyday automation tasks.

    COMPILER:
        The LLM went through a structured 4-phase reasoning process before
        emitting the plan:
          Phase 1 — ANALYSIS:   decompose task into atomic intentions
          Phase 2 — RESOLUTION: map each intention to a concrete module action
          Phase 3 — VALIDATION: verify params, deps, types, path safety
          Phase 4 — GENERATION: emit the plan only if all checks passed

        A ``compiler_trace`` dict MUST be present in the plan.  The Bridge
        stores the trace in the audit log and (when a verifier is configured)
        replays the validation phase to confirm consistency between the trace
        and the emitted plan.

        Use for: surgical robots, industrial control, financial transactions,
        any system where a wrong action has irreversible physical consequences.
    """

    STANDARD = "standard"
    COMPILER = "compiler"


class OnErrorBehavior(str, Enum):
    """What the executor should do when an action raises an error."""

    ABORT = "abort"
    CONTINUE = "continue"
    RETRY = "retry"
    ROLLBACK = "rollback"
    SKIP = "skip"


class PlanStatus(str, Enum):
    """Lifecycle status of a submitted plan."""

    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    PAUSED = "paused"


class ActionStatus(str, Enum):
    """Lifecycle status of a single action within a plan."""

    PENDING = "pending"
    WAITING = "waiting"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"
    ROLLED_BACK = "rolled_back"
    AWAITING_APPROVAL = "awaiting_approval"


# ---------------------------------------------------------------------------
# Nested configuration models
# ---------------------------------------------------------------------------


class RetryConfig(BaseModel):
    """Retry policy applied when ``on_error`` is ``"retry"``."""

    max_attempts: Annotated[int, Field(ge=1, le=MAX_RETRY_MAX_ATTEMPTS)] = (
        DEFAULT_RETRY_MAX_ATTEMPTS
    )
    delay_seconds: Annotated[float, Field(ge=0.1, le=MAX_RETRY_DELAY_SECONDS)] = (
        DEFAULT_RETRY_DELAY_SECONDS
    )
    backoff_factor: Annotated[float, Field(ge=1.0, le=10.0)] = DEFAULT_RETRY_BACKOFF_FACTOR
    retry_on: list[str] = Field(
        default_factory=lambda: ["TimeoutError", "ConnectionError"],
        description="Exception class names that trigger a retry. Empty list = retry on any error.",
    )

    def delay_for_attempt(self, attempt: int) -> float:
        """Return the delay in seconds before the *attempt*-th retry (1-indexed)."""
        return self.delay_seconds * (self.backoff_factor ** (attempt - 1))


class RollbackConfig(BaseModel):
    """Describes the compensating action to run on rollback."""

    action: str = Field(
        description="ID of the action within the same plan to execute as rollback."
    )
    params: dict[str, Any] = Field(
        default_factory=dict,
        description="Params for the rollback action. Supports {{result.X.Y}} templates.",
    )


class PerceptionConfig(BaseModel):
    """Screenshot/OCR perception around an action."""

    capture_before: bool = False
    capture_after: bool = True
    ocr_enabled: bool = False
    validate_output: str | None = Field(
        default=None,
        description="JSONPath expression the captured output must satisfy.",
    )
    timeout_seconds: Annotated[
        int, Field(ge=1, le=MAX_PERCEPTION_TIMEOUT_SECONDS)
    ] = DEFAULT_PERCEPTION_TIMEOUT_SECONDS


class MemoryConfig(BaseModel):
    """Memory read/write configuration for an action."""

    read_keys: list[str] = Field(
        default_factory=list,
        description="Key-value memory keys to inject into params before execution.",
    )
    write_key: str | None = Field(
        default=None,
        description="Store the action result under this memory key.",
    )
    vector_search: str | None = Field(
        default=None,
        description="Semantic query for ChromaDB retrieval. Results injected as context.",
    )
    top_k: Annotated[int, Field(ge=1, le=MAX_MEMORY_TOP_K)] = DEFAULT_MEMORY_TOP_K


class ApprovalConfig(BaseModel):
    """Per-action approval customization.

    When an action requires approval (either via ``requires_approval=true``
    or the global ``require_approval_for`` config list), this config controls
    how the approval request is presented and what happens on timeout.
    """

    message: str | None = Field(
        default=None,
        description="Custom message shown to the approver explaining what this action does.",
    )
    risk_level: Literal["low", "medium", "high", "critical"] = Field(
        default="medium",
        description="Risk classification shown in the approval prompt.",
    )
    timeout_seconds: Annotated[int | None, Field(ge=10, le=3600)] = Field(
        default=None,
        description="Per-action timeout override. None = use global default.",
    )
    timeout_behavior: Literal["reject", "skip"] = Field(
        default="reject",
        description="What to do when approval times out: reject (fail) or skip.",
    )
    clarification_options: list[str] = Field(
        default_factory=list,
        description=(
            "Structured options presented to the approver for intent clarification. "
            "When non-empty, the approval UI shows these as selectable choices "
            "rather than just approve/reject. The selected option is returned in "
            "the approval response metadata."
        ),
    )


class PlanMetadata(BaseModel):
    """Non-functional metadata attached to a plan for tracing and debugging."""

    created_by: str | None = None
    llm_model: str | None = None
    tags: list[str] = Field(default_factory=list)
    context: dict[str, Any] = Field(
        default_factory=dict,
        description="Arbitrary key-value pairs for caller-side tracking.",
    )


# ---------------------------------------------------------------------------
# Compiler mode trace model
# ---------------------------------------------------------------------------


class CompilerTrace(BaseModel):
    """Structured reasoning trace produced by the LLM in COMPILER mode.

    The trace is NOT executed — it is stored in the audit log and optionally
    verified by the Bridge to confirm that the emitted plan is consistent with
    the stated reasoning.

    All four phase fields are plain strings (raw LLM output between XML-like
    tags).  The format is intentionally open so different LLM providers and
    prompting strategies can produce traces without a rigid schema.

    Example (from a plan that moves a surgical arm):

        CompilerTrace(
            analysis="Intention 1: read current arm position ...",
            resolution="Intention 1 → read_position(arm='surgical_arm') ✓",
            validation="□ read_position: params required=[arm] → ✓ present ...",
            generation_approved=True,
            llm_model="claude-sonnet-4-6",
            prompt_tokens=842,
        )

    The Bridge validator (Phase 2 feature) will parse the ``resolution`` and
    ``validation`` fields and cross-check them against the actual ``actions``
    list to detect inconsistencies between what the LLM claimed to verify
    and what it actually generated.
    """

    analysis: str | None = Field(
        default=None,
        description="Phase 1 output: atomic intentions decomposed from the task.",
    )
    resolution: str | None = Field(
        default=None,
        description="Phase 2 output: mapping from each intention to a module action.",
    )
    validation: str | None = Field(
        default=None,
        description="Phase 3 output: line-by-line param/dep/type checks.",
    )
    generation_approved: bool = Field(
        default=False,
        description="True if the LLM explicitly stated all validations passed.",
    )
    llm_model: str | None = Field(
        default=None,
        description="Model ID used to generate this trace.",
    )
    prompt_tokens: int | None = Field(
        default=None,
        description="Token count of the compiler prompt (for cost tracking).",
    )
    extra: dict[str, Any] = Field(
        default_factory=dict,
        description="Provider-specific trace fields (logprobs, latency, etc.).",
    )


# ---------------------------------------------------------------------------
# Core action and plan models
# ---------------------------------------------------------------------------


class IMLAction(BaseModel):
    """A single executable action within an IML plan."""

    id: str = Field(description="Unique identifier within the plan.")
    action: str = Field(description="Action name as registered in the module (snake_case).")
    module: str = Field(description="Module ID (snake_case, e.g. 'filesystem').")
    params: dict[str, Any] = Field(
        default_factory=dict,
        description=(
            "Action parameters.  Values may contain {{result.action_id.field}}, "
            "{{memory.key}}, or {{env.VAR_NAME}} template expressions."
        ),
    )
    depends_on: list[str] = Field(
        default_factory=list,
        description="Action IDs that must reach COMPLETED status before this action runs.",
    )
    on_error: OnErrorBehavior = OnErrorBehavior.ABORT
    requires_approval: bool = False
    timeout: Annotated[
        int, Field(ge=MIN_ACTION_TIMEOUT_SECONDS, le=MAX_ACTION_TIMEOUT_SECONDS)
    ] = DEFAULT_ACTION_TIMEOUT_SECONDS
    retry: RetryConfig | None = None
    rollback: RollbackConfig | None = None
    perception: PerceptionConfig | None = None
    memory: MemoryConfig | None = None
    approval: ApprovalConfig | None = None
    label: str | None = Field(default=None, max_length=LABEL_MAX_LEN)
    tags: Annotated[list[str], Field(max_length=MAX_TAGS_PER_ACTION)] = Field(
        default_factory=list
    )
    target_node: str | None = Field(
        default=None,
        description=(
            "Target node for distributed execution. "
            "None (default) = local node — identical behaviour to standalone LLMOS. "
            "Phase 4: set to a remote node_id (e.g. 'node_lyon_2') to route this "
            "action to a remote LLMOS instance via NodeRegistry."
        ),
    )

    @field_validator("id")
    @classmethod
    def validate_id(cls, v: str) -> str:
        if not _ACTION_ID_RE.match(v):
            raise ValueError(
                f"Action ID '{v}' must match [a-zA-Z0-9_-] and be 1-{ACTION_ID_MAX_LEN} chars."
            )
        return v

    @field_validator("module")
    @classmethod
    def validate_module(cls, v: str) -> str:
        if not _MODULE_ID_RE.match(v):
            raise ValueError(
                f"Module ID '{v}' must match [a-z][a-z0-9_]* and start with a letter."
            )
        return v

    @field_validator("action")
    @classmethod
    def validate_action(cls, v: str) -> str:
        if not _ACTION_NAME_RE.match(v):
            raise ValueError(
                f"Action name '{v}' must match [a-z][a-z0-9_]* and start with a letter."
            )
        return v

    @field_validator("tags", mode="before")
    @classmethod
    def validate_tags(cls, v: list[Any]) -> list[Any]:
        for tag in v:
            if not isinstance(tag, str) or len(tag) > MAX_TAG_LEN:
                raise ValueError(f"Each tag must be a string of at most {MAX_TAG_LEN} chars.")
        return v

    @model_validator(mode="after")
    def validate_retry_config_present_when_needed(self) -> "IMLAction":
        if self.on_error == OnErrorBehavior.RETRY and self.retry is None:
            self.retry = RetryConfig()
        return self


class IMLPlan(BaseModel):
    """A complete IML plan — the top-level unit submitted to LLMOS Bridge."""

    plan_id: str = Field(
        default_factory=lambda: str(uuid.uuid4()),
        description="Unique plan identifier.  Auto-generated if not provided.",
    )
    protocol_version: Literal["2.0"] = PROTOCOL_VERSION  # type: ignore[assignment]
    description: str = Field(
        max_length=MAX_PLAN_DESCRIPTION_LEN,
        description="Human-readable summary of what the plan accomplishes.",
    )
    session_id: str | None = Field(
        default=None,
        description="Optional session identifier for grouping related plans.",
    )
    execution_mode: ExecutionMode = ExecutionMode.SEQUENTIAL
    plan_mode: PlanMode = Field(
        default=PlanMode.STANDARD,
        description=(
            "Generation contract for this plan.  "
            "'standard' accepts probabilistic LLM output (default). "
            "'compiler' requires a full 4-phase reasoning trace in "
            "compiler_trace and asserts generation_approved=True."
        ),
    )
    compiler_trace: CompilerTrace | None = Field(
        default=None,
        description=(
            "Structured reasoning trace produced by the LLM in COMPILER mode. "
            "Must be present and have generation_approved=True when "
            "plan_mode='compiler'.  Stored in the audit log for post-hoc "
            "verification and compliance."
        ),
    )
    metadata: PlanMetadata | None = None
    module_requirements: dict[str, str] = Field(
        default_factory=dict,
        description=(
            "Declares the minimum module versions required by this plan. "
            "Keys are module IDs, values are PEP-440 version specifiers "
            "(e.g. {\"filesystem\": \">=1.2.0\", \"browser\": \">=0.5.0\"}). "
            "The executor validates these constraints before starting execution "
            "via ModuleVersionChecker in protocol.compat."
        ),
    )
    actions: Annotated[
        list[IMLAction], Field(min_length=1, max_length=MAX_ACTIONS_PER_PLAN)
    ]

    @field_validator("plan_id")
    @classmethod
    def validate_plan_id(cls, v: str) -> str:
        if not _PLAN_ID_RE.match(v):
            raise ValueError(
                f"Plan ID '{v}' must match [a-zA-Z0-9_-] and be 1-{PLAN_ID_MAX_LEN} chars."
            )
        return v

    @model_validator(mode="after")
    def validate_compiler_mode_contract(self) -> "IMLPlan":
        """In COMPILER mode the trace is mandatory and must be approved."""
        if self.plan_mode == PlanMode.COMPILER:
            if self.compiler_trace is None:
                raise ValueError(
                    "plan_mode='compiler' requires a compiler_trace.  "
                    "The LLM must include the 4-phase reasoning trace "
                    "(analysis/resolution/validation/generation_approved=true)."
                )
            if not self.compiler_trace.generation_approved:
                raise ValueError(
                    "plan_mode='compiler' requires compiler_trace.generation_approved=true.  "
                    "The LLM must explicitly confirm all validations passed "
                    "before the plan is accepted by the Bridge."
                )
        return self

    @model_validator(mode="after")
    def validate_action_ids_unique(self) -> "IMLPlan":
        seen: set[str] = set()
        duplicates: list[str] = []
        for action in self.actions:
            if action.id in seen:
                duplicates.append(action.id)
            seen.add(action.id)
        if duplicates:
            raise ValueError(f"Duplicate action IDs: {sorted(set(duplicates))}")
        return self

    @model_validator(mode="after")
    def validate_depends_on_references(self) -> "IMLPlan":
        action_ids = {a.id for a in self.actions}
        for action in self.actions:
            unknown = [d for d in action.depends_on if d not in action_ids]
            if unknown:
                raise ValueError(
                    f"Action '{action.id}' depends on unknown action(s): {unknown}"
                )
            if action.id in action.depends_on:
                raise ValueError(f"Action '{action.id}' cannot depend on itself.")
        return self

    @model_validator(mode="after")
    def validate_rollback_references(self) -> "IMLPlan":
        action_ids = {a.id for a in self.actions}
        for action in self.actions:
            if action.rollback and action.rollback.action not in action_ids:
                raise ValueError(
                    f"Action '{action.id}' rollback targets unknown action "
                    f"'{action.rollback.action}'."
                )
        return self

    def get_action(self, action_id: str) -> IMLAction | None:
        """Return the action with *action_id*, or None."""
        for action in self.actions:
            if action.id == action_id:
                return action
        return None

    def action_ids(self) -> list[str]:
        """Return action IDs in declaration order."""
        return [a.id for a in self.actions]
