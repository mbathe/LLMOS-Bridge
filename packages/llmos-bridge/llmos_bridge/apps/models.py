"""Pydantic models for the LLMOS App Language (.app.yaml schema).

These models define the complete grammar of the YAML-based AI application
language. A .app.yaml file is parsed into an AppDefinition, which is then
compiled into runtime components by the AppCompiler.
"""

from __future__ import annotations

from enum import Enum
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field


# ─── Enums ──────────────────────────────────────────────────────────────


class LoopType(str, Enum):
    """Agent loop execution mode."""
    reactive = "reactive"          # plan → execute → observe → replan
    single_shot = "single_shot"    # LLM acts once and returns
    continuous = "continuous"      # daemon mode, waits for new triggers


class ContextStrategy(str, Enum):
    """Strategy for managing conversation context window."""
    sliding_window = "sliding_window"
    summarize = "summarize"
    truncate = "truncate"


class TriggerType(str, Enum):
    """Types of triggers that can start an app."""
    cli = "cli"
    http = "http"
    webhook = "webhook"
    schedule = "schedule"
    watch = "watch"
    event = "event"


class TriggerMode(str, Enum):
    """CLI trigger interaction mode."""
    conversation = "conversation"  # multi-turn, same run
    one_shot = "one_shot"          # single input → single run


class AgentRole(str, Enum):
    """Role of an agent in a multi-agent system."""
    coordinator = "coordinator"
    specialist = "specialist"
    reviewer = "reviewer"
    observer = "observer"


class CommunicationMode(str, Enum):
    """Inter-agent communication pattern."""
    orchestrated = "orchestrated"
    peer_to_peer = "peer_to_peer"
    blackboard = "blackboard"


class MultiAgentStrategy(str, Enum):
    """Multi-agent execution strategy."""
    hierarchical = "hierarchical"
    round_robin = "round_robin"
    consensus = "consensus"
    pipeline = "pipeline"


class AuditLevel(str, Enum):
    """Audit logging granularity."""
    none = "none"
    errors = "errors"
    mutations = "mutations"
    full = "full"


class FlowStepType(str, Enum):
    """Types of flow steps."""
    action = "action"
    agent = "agent"
    sequence = "sequence"
    parallel = "parallel"
    branch = "branch"
    loop = "loop"
    map = "map"
    reduce = "reduce"
    race = "race"
    pipe = "pipe"
    spawn = "spawn"
    approval = "approval"
    try_catch = "try_catch"
    dispatch = "dispatch"
    emit = "emit"
    wait = "wait"
    end = "end"
    use_macro = "use_macro"
    goto = "goto"


class MemoryBackend(str, Enum):
    """Storage backend for memory levels."""
    in_memory = "in_memory"
    sqlite = "sqlite"
    redis = "redis"
    chromadb = "chromadb"
    file = "file"


class OnToolError(str, Enum):
    """How the agent loop handles tool errors."""
    show_to_agent = "show_to_agent"
    retry = "retry"
    fail = "fail"
    skip = "skip"


class OnLLMError(str, Enum):
    """How the agent loop handles LLM errors."""
    retry = "retry"
    fail = "fail"


class WebhookAuthType(str, Enum):
    """Webhook authentication method."""
    bearer = "bearer"
    api_key = "api_key"
    hmac = "hmac"
    none = "none"


class StreamFormat(str, Enum):
    """HTTP response streaming format."""
    json = "json"
    streaming_json = "streaming_json"
    sse = "sse"


# ─── Sub-models ─────────────────────────────────────────────────────────


class InterfaceField(BaseModel):
    """Describes an input or output field of an app."""
    type: str = "string"
    description: str = ""
    schema: dict[str, Any] | None = None


class ErrorDefinition(BaseModel):
    """Custom error code the app can return."""
    code: str
    description: str = ""


class AppInterface(BaseModel):
    """Public contract of the app (what it accepts and returns)."""
    input: InterfaceField = Field(default_factory=InterfaceField)
    output: InterfaceField = Field(default_factory=InterfaceField)
    errors: list[ErrorDefinition] = Field(default_factory=list)


class AppConfig(BaseModel):
    """The `app:` block — identity and metadata."""
    name: str
    version: str = "1.0.0"
    description: str = ""
    author: str = ""
    tags: list[str] = Field(default_factory=list)
    license: str = ""

    max_concurrent_runs: int = Field(default=5, ge=1, le=100)
    max_turns_per_run: int = Field(default=200, ge=1, le=10000)
    max_actions_per_turn: int = Field(default=50, ge=1, le=500)
    timeout: str = "3600s"
    checkpoint: bool = Field(
        default=False,
        description="Enable flow checkpoint/resume. When true, flow state is persisted "
        "after each step so execution can resume after interruption.",
    )

    interface: AppInterface = Field(default_factory=AppInterface)


# ─── Brain (LLM configuration) ──────────────────────────────────────


class FallbackBrain(BaseModel):
    """Fallback LLM provider configuration."""
    provider: str = ""
    model: str
    config: dict[str, Any] = Field(default_factory=dict)


class BrainConfig(BaseModel):
    """LLM provider + model configuration."""
    provider: str = "anthropic"
    model: str = "claude-sonnet-4-6"
    temperature: float = Field(default=0, ge=0, le=2)
    max_tokens: int = Field(default=8192, ge=1, le=200000)
    top_p: float = Field(default=1.0, ge=0, le=1)
    timeout: float = Field(default=120.0, ge=0, description="LLM call timeout in seconds (0 = no timeout)")
    config: dict[str, Any] = Field(default_factory=dict)
    fallback: list[FallbackBrain] = Field(default_factory=list)


# ─── Tools (module actions exposed to LLM) ──────────────────────────


class ToolConstraints(BaseModel):
    """Constraints applied to a tool's execution."""
    timeout: str = ""
    paths: list[str] = Field(default_factory=list)
    max_file_size: str = ""
    network: bool | None = None
    working_directory: str = ""
    forbidden_commands: list[str] = Field(default_factory=list)
    forbidden_patterns: list[str] = Field(default_factory=list)
    allowed_domains: list[str] = Field(default_factory=list)
    max_response_size: str = ""
    read_only: bool | None = None
    forbidden_tables: list[str] = Field(default_factory=list)
    # IML rate limiting (per-action sliding window)
    rate_limit_per_minute: int | None = Field(default=None, ge=1, description="Max calls per minute")
    rate_limit_per_hour: int | None = Field(default=None, ge=1, description="Max calls per hour")
    # IML retry (per-tool retry with backoff)
    max_retries: int = Field(default=0, ge=0, le=10, description="Max retry attempts on failure")
    retry_backoff: Literal["exponential", "fixed", "linear"] = "exponential"


class ToolDefinition(BaseModel):
    """A tool exposed to the agent (module action or builtin)."""
    # Module-based tool
    module: str = ""
    action: str = ""              # single action (if set, only this action)
    actions: list[str] = Field(default_factory=list)  # subset of actions
    exclude: list[str] = Field(default_factory=list)   # actions to exclude
    description: str = ""         # override description for LLM
    constraints: ToolConstraints = Field(default_factory=ToolConstraints)

    # Built-in tool
    builtin: str = ""             # ask_user, todo, delegate, emit, wait
    id: str = ""                  # custom tool ID

    # Custom tool params
    params: dict[str, Any] = Field(default_factory=dict)


# ─── Loop (agent execution behavior) ────────────────────────────────


class RetryConfig(BaseModel):
    """Retry configuration for errors."""
    max_attempts: int = Field(default=3, ge=1, le=20)
    backoff: Literal["exponential", "fixed", "linear"] = "exponential"


class ContextConfig(BaseModel):
    """Context window management configuration."""
    max_tokens: int = Field(default=200000, ge=1000)
    strategy: ContextStrategy = ContextStrategy.summarize
    keep_system_prompt: bool = True
    keep_last_n_messages: int = Field(default=30, ge=1)
    summarize_older: bool = True
    inject_on_start: list[str] = Field(default_factory=list)
    # Budget management (used by context_manager module)
    model_context_window: int = Field(default=200000, ge=1000, description="Total model context window size in tokens")
    output_reserved: int = Field(default=8192, ge=256, description="Tokens reserved for model output generation")
    cognitive_max_tokens: int = Field(default=1500, ge=100, description="Max tokens for cognitive state (objectives never truncated)")
    memory_max_tokens: int = Field(default=2000, ge=100, description="Max tokens for memory context")
    compression_trigger_ratio: float = Field(default=0.75, ge=0.1, le=0.95, description="Compress when history uses this fraction of budget")
    summarization_model: str = Field(default="", description="Model for summarization (empty = same model)")
    min_recent_messages: int = Field(default=10, ge=1, description="Always keep this many recent messages uncompressed")


class PlanningConfig(BaseModel):
    """LLM planning behavior configuration."""
    enabled: bool = True
    batch_actions: bool = True
    max_actions_per_batch: int = Field(default=20, ge=1, le=50)
    replan_on_failure: bool = True


class LoopConfig(BaseModel):
    """Agent loop configuration."""
    type: LoopType = LoopType.reactive
    max_turns: int = Field(default=200, ge=1, le=10000)
    stop_conditions: list[str] = Field(
        default_factory=lambda: ["{{agent.no_tool_calls}}"]
    )
    on_tool_error: OnToolError = OnToolError.show_to_agent
    on_llm_error: OnLLMError = OnLLMError.retry
    retry: RetryConfig = Field(default_factory=RetryConfig)
    context: ContextConfig = Field(default_factory=ContextConfig)
    planning: PlanningConfig = Field(default_factory=PlanningConfig)


# ─── Agent (single agent config) ────────────────────────────────────


class AgentConfig(BaseModel):
    """Complete single-agent configuration."""
    brain: BrainConfig = Field(default_factory=BrainConfig)
    system_prompt: str = ""
    tools: list[ToolDefinition] = Field(default_factory=list)
    loop: LoopConfig = Field(default_factory=LoopConfig)

    # Multi-agent fields (used when in agents[] list)
    id: str = ""
    role: AgentRole = AgentRole.specialist
    expertise: list[str] = Field(default_factory=list)
    preferred_node: str = ""


# ─── Multi-Agent ────────────────────────────────────────────────────


class CommunicationConfig(BaseModel):
    """Inter-agent communication settings."""
    mode: CommunicationMode = CommunicationMode.orchestrated


class MultiAgentConfig(BaseModel):
    """Multi-agent system configuration (replaces single agent)."""
    agents: list[AgentConfig] = Field(default_factory=list)
    communication: CommunicationConfig = Field(default_factory=CommunicationConfig)
    strategy: MultiAgentStrategy = MultiAgentStrategy.hierarchical


# ─── Memory ─────────────────────────────────────────────────────────


class WorkingMemoryConfig(BaseModel):
    """Working memory (per-run, ephemeral)."""
    backend: MemoryBackend = MemoryBackend.in_memory
    max_size: str = "100MB"


class ConversationMemoryConfig(BaseModel):
    """Conversation memory (persisted across turns)."""
    backend: MemoryBackend = MemoryBackend.sqlite
    path: str = "{{data_dir}}/conversations.db"
    max_history: int = Field(default=1000, ge=1)
    auto_summarize: bool = True
    summarize_after: int = Field(default=50, ge=1)


class EpisodicRecallConfig(BaseModel):
    """Auto-recall settings for episodic memory."""
    on_start: bool = True
    query: str = "{{trigger.input}}"
    limit: int = Field(default=5, ge=1, le=50)
    min_similarity: float = Field(default=0.7, ge=0, le=1)


class EpisodicMemoryConfig(BaseModel):
    """Episodic memory (cross-session, vector-based)."""
    backend: MemoryBackend = MemoryBackend.chromadb
    path: str = "{{data_dir}}/episodes"
    auto_record: bool = True
    record_fields: list[str] = Field(
        default_factory=lambda: ["input", "actions_taken", "outcome", "lessons"]
    )
    auto_recall: EpisodicRecallConfig = Field(default_factory=EpisodicRecallConfig)


class ProjectMemoryConfig(BaseModel):
    """Project memory (persistent file)."""
    backend: MemoryBackend = MemoryBackend.file
    path: str = "{{workspace}}/.llmos/MEMORY.md"
    auto_inject: bool = True
    agent_writable: bool = True
    max_lines: int = Field(default=200, ge=1)


class ProceduralMemoryConfig(BaseModel):
    """Procedural memory (learned patterns)."""
    backend: MemoryBackend = MemoryBackend.sqlite
    path: str = "{{data_dir}}/procedures.db"
    learn_from_failures: bool = True
    learn_from_successes: bool = True
    auto_suggest: bool = True


class MemoryConfig(BaseModel):
    """Multi-level memory configuration."""
    working: WorkingMemoryConfig = Field(default_factory=WorkingMemoryConfig)
    conversation: ConversationMemoryConfig | None = None
    episodic: EpisodicMemoryConfig | None = None
    project: ProjectMemoryConfig | None = None
    procedural: ProceduralMemoryConfig | None = None


# ─── Perception ─────────────────────────────────────────────────────


class PerceptionActionConfig(BaseModel):
    """Per-action perception config (screenshot/OCR around tool calls)."""
    capture_before: bool = False
    capture_after: bool = True
    ocr_enabled: bool = False
    validate_output: str = ""         # JSONPath validation expression
    timeout_seconds: int = 10


class PerceptionAppConfig(BaseModel):
    """Global perception config for an app.

    Controls automatic screenshot/OCR capture around tool calls.
    Maps to the daemon's PerceptionConfig from IML protocol.
    """
    enabled: bool = False
    capture_before: bool = False
    capture_after: bool = True
    ocr_enabled: bool = False
    timeout_seconds: int = 10
    # Per-module/action overrides
    actions: dict[str, PerceptionActionConfig] = Field(
        default_factory=dict,
        description="Per-action overrides keyed by 'module.action'",
    )


# ─── Capabilities (security) ────────────────────────────────────────


class CapabilityGrant(BaseModel):
    """Permission grant for a module/action."""
    module: str
    actions: list[str] = Field(default_factory=list)   # empty = all
    constraints: ToolConstraints = Field(default_factory=ToolConstraints)


class CapabilityDenial(BaseModel):
    """Permission denial rule."""
    module: str
    action: str = ""
    when: str = ""                # expression that triggers denial
    reason: str = ""


class ApprovalRule(BaseModel):
    """Approval gate rule."""
    module: str = ""
    action: str = ""
    when: str = ""                # expression condition
    message: str = ""             # message shown to human
    timeout: str = "300s"
    on_timeout: Literal["approve", "reject", "skip"] = "reject"
    channel: str = "cli"          # cli | http | slack | email

    # Count-based trigger
    trigger: str = ""             # "action_count"
    threshold: int = 0


class AuditNotification(BaseModel):
    """Audit notification rule."""
    event: str
    channel: str = "log"


class AuditConfig(BaseModel):
    """Audit logging configuration."""
    level: AuditLevel = AuditLevel.full
    log_params: bool = True
    redact_secrets: bool = True
    notify_on: list[AuditNotification] = Field(default_factory=list)


class CapabilitiesConfig(BaseModel):
    """Security capabilities (grants, denials, approvals, audit)."""
    grant: list[CapabilityGrant] = Field(default_factory=list)
    deny: list[CapabilityDenial] = Field(default_factory=list)
    approval_required: list[ApprovalRule] = Field(default_factory=list)
    audit: AuditConfig = Field(default_factory=AuditConfig)


class SandboxConfig(BaseModel):
    """Sandbox configuration for the security: shorthand block."""
    allowed_paths: list[str] = Field(default_factory=list)
    blocked_commands: list[str] = Field(default_factory=list)


class SecurityProfile(str, Enum):
    """Known security profiles."""
    readonly = "readonly"
    local_worker = "local_worker"
    power_user = "power_user"
    unrestricted = "unrestricted"


class SecurityAppConfig(BaseModel):
    """User-friendly security block (maps to daemon permission profiles + constraints).

    This provides a simpler interface than capabilities: for common security
    patterns. The compiler/runtime can convert this into CapabilitiesConfig
    and ToolConstraints as needed.
    """
    profile: SecurityProfile = SecurityProfile.power_user
    sandbox: SandboxConfig = Field(default_factory=SandboxConfig)


# ─── Triggers ───────────────────────────────────────────────────────


class WebhookAuth(BaseModel):
    """Webhook/HTTP authentication config."""
    type: WebhookAuthType = WebhookAuthType.none
    secret: str = ""
    header: str = ""


class TriggerDefinition(BaseModel):
    """A trigger that can start the app."""
    id: str = ""
    type: TriggerType

    # CLI-specific
    prompt: str = "> "
    multiline: bool = True
    history: bool = True
    mode: TriggerMode = TriggerMode.conversation
    greeting: str = ""

    # HTTP/Webhook-specific
    path: str = ""
    method: str = "POST"
    auth: WebhookAuth = Field(default_factory=WebhookAuth)
    events: list[str] = Field(default_factory=list)
    body: dict[str, Any] = Field(default_factory=dict)
    response: dict[str, Any] = Field(default_factory=dict)

    # Schedule-specific
    cron: str = ""
    when: str = ""               # natural language schedule
    timezone: str = ""

    # Watch-specific
    paths: list[str] = Field(default_factory=list)
    debounce: str = "2s"

    # Event-specific
    topic: str = ""

    # Common
    transform: str = ""          # template to transform payload to input
    filters: list[str] = Field(default_factory=list)
    input: str = ""              # static input (for schedule triggers)


# ─── Flow steps ─────────────────────────────────────────────────────


class ApprovalOption(BaseModel):
    """Option presented to user in an approval gate."""
    label: str
    value: str
    schema: dict[str, Any] | None = None


class CatchHandler(BaseModel):
    """Error handler in a try/catch block."""
    error: str = "*"             # error type or "*" for catch-all
    do: dict[str, Any] | None = None
    then: Literal["fail", "continue", ""] = ""


class EndConfig(BaseModel):
    """End step configuration."""
    status: Literal["success", "failure", "cancelled"] = "success"
    output: dict[str, Any] = Field(default_factory=dict)


class FlowStep(BaseModel):
    """A single step in the flow.

    Flow steps are polymorphic — the type is inferred from which fields
    are present. Only one of the step-type fields should be set.
    """
    id: str = ""

    # ── action step ──
    action: str = ""             # "module.action_name"
    params: dict[str, Any] = Field(default_factory=dict)
    timeout: str = ""
    on_error: str = ""           # fail | skip | continue | rollback
    retry: RetryConfig | None = None
    perception: PerceptionActionConfig | None = None  # per-action perception

    # ── agent step ──
    agent: str = ""              # agent ID or "default"
    input: str = ""              # input text/template for agent

    # ── sequence step ──
    sequence: list[FlowStep] | None = None

    # ── parallel step ──
    parallel: ParallelConfig | None = None

    # ── branch step ──
    branch: BranchConfig | None = None

    # ── loop step ──
    loop: LoopFlowConfig | None = None

    # ── map step ──
    map: MapConfig | None = None

    # ── reduce step ──
    reduce: ReduceConfig | None = None

    # ── race step ──
    race: RaceConfig | None = None

    # ── pipe step ──
    pipe: list[FlowStep] | None = None

    # ── spawn step ──
    spawn: SpawnConfig | None = None

    # ── approval step ──
    approval: ApprovalFlowConfig | None = None

    # ── try/catch step ──
    try_steps: list[FlowStep] | None = Field(None, alias="try")
    catch: list[CatchHandler] | None = None
    finally_steps: list[FlowStep] | None = Field(None, alias="finally")

    # ── dispatch step ──
    dispatch: DispatchConfig | None = None

    # ── emit step ──
    emit: EmitConfig | None = None

    # ── wait step ──
    wait: WaitConfig | None = None

    # ── end step ──
    end: EndConfig | None = None

    # ── macro usage ──
    use: str = ""                # macro name
    with_params: dict[str, Any] = Field(default_factory=dict, alias="with")

    # ── goto ──
    goto: str = ""               # step ID to jump to

    model_config = {"populate_by_name": True}

    def infer_type(self) -> FlowStepType:
        """Infer the step type from which fields are populated."""
        if self.action:
            return FlowStepType.action
        if self.agent:
            return FlowStepType.agent
        if self.sequence is not None:
            return FlowStepType.sequence
        if self.parallel is not None:
            return FlowStepType.parallel
        if self.branch is not None:
            return FlowStepType.branch
        if self.loop is not None:
            return FlowStepType.loop
        if self.map is not None:
            return FlowStepType.map
        if self.reduce is not None:
            return FlowStepType.reduce
        if self.race is not None:
            return FlowStepType.race
        if self.pipe is not None:
            return FlowStepType.pipe
        if self.spawn is not None:
            return FlowStepType.spawn
        if self.approval is not None:
            return FlowStepType.approval
        if self.try_steps is not None:
            return FlowStepType.try_catch
        if self.dispatch is not None:
            return FlowStepType.dispatch
        if self.emit is not None:
            return FlowStepType.emit
        if self.wait is not None:
            return FlowStepType.wait
        if self.end is not None:
            return FlowStepType.end
        if self.use:
            return FlowStepType.use_macro
        if self.goto:
            return FlowStepType.goto
        return FlowStepType.action  # fallback


class ParallelConfig(BaseModel):
    """Parallel execution configuration."""
    steps: list[FlowStep] = Field(default_factory=list)
    max_concurrent: int = Field(default=10, ge=1)
    fail_fast: bool = False


class BranchConfig(BaseModel):
    """Conditional branching configuration."""
    on: str                       # expression to evaluate
    cases: dict[str, list[FlowStep]] = Field(default_factory=dict)
    default: list[FlowStep] | None = None


class LoopFlowConfig(BaseModel):
    """Loop construct configuration."""
    max_iterations: int | str = 10
    until: str = ""               # condition expression
    body: list[FlowStep] = Field(default_factory=list)


class MapConfig(BaseModel):
    """Map over a collection."""
    over: str                     # expression yielding a list
    as_var: str = Field("item", alias="as")
    max_concurrent: int = Field(default=5, ge=1)
    step: list[FlowStep] = Field(default_factory=list)

    model_config = {"populate_by_name": True}


class ReduceConfig(BaseModel):
    """Reduce (aggregate) a collection."""
    over: str                     # expression yielding a list
    initial: dict[str, Any] = Field(default_factory=dict)
    as_var: str = Field("acc", alias="as")
    step: FlowStep | dict[str, Any] | None = None

    model_config = {"populate_by_name": True}


class RaceConfig(BaseModel):
    """Race: first to finish wins."""
    steps: list[FlowStep] = Field(default_factory=list)


class SpawnConfig(BaseModel):
    """Spawn a sub-application."""
    app: str                      # path to .app.yaml or app name
    input: str = ""
    timeout: str = "300s"
    await_result: bool = Field(True, alias="await")

    model_config = {"populate_by_name": True}


class ApprovalFlowConfig(BaseModel):
    """Human approval gate in a flow."""
    message: str
    options: list[ApprovalOption] = Field(default_factory=list)
    timeout: str = "300s"
    on_timeout: str = "reject"
    channel: str = "cli"
    on: dict[str, Any] = Field(default_factory=dict)  # value → goto/inject


class DispatchConfig(BaseModel):
    """Dynamic dispatch (module/action resolved at runtime)."""
    module: str                   # expression
    action: str                   # expression
    params: str | dict[str, Any] = ""  # expression or dict


class EmitConfig(BaseModel):
    """Publish an event to the bus."""
    topic: str
    event: dict[str, Any] = Field(default_factory=dict)


class WaitConfig(BaseModel):
    """Wait for an event from the bus."""
    topic: str
    filter: str = ""
    timeout: str = "3600s"


# ─── Macros ─────────────────────────────────────────────────────────


class MacroParam(BaseModel):
    """Parameter definition for a macro."""
    type: Literal["string", "int", "integer", "float", "number", "bool", "boolean", "object", "array", "list"] = "string"
    required: bool = True
    default: Any = None


class MacroDefinition(BaseModel):
    """Reusable flow snippet."""
    name: str
    description: str = ""
    params: dict[str, MacroParam] = Field(default_factory=dict)
    body: list[FlowStep] = Field(default_factory=list)

    @classmethod
    def _normalize_params(cls, v: dict[str, Any]) -> dict[str, MacroParam]:
        """Normalize raw dicts to MacroParam objects at parse time."""
        result: dict[str, MacroParam] = {}
        for key, val in v.items():
            if isinstance(val, MacroParam):
                result[key] = val
            elif isinstance(val, dict):
                result[key] = MacroParam.model_validate(val)
            else:
                result[key] = MacroParam(default=val, required=False)
        return result

    def model_post_init(self, __context: Any) -> None:
        # Normalize params after Pydantic parsing
        if self.params:
            normalized: dict[str, MacroParam] = {}
            for key, val in self.params.items():
                if isinstance(val, MacroParam):
                    normalized[key] = val
                elif isinstance(val, dict):
                    normalized[key] = MacroParam.model_validate(val)
                else:
                    normalized[key] = MacroParam(default=val, required=False)
            object.__setattr__(self, "params", normalized)


# ─── Observability ──────────────────────────────────────────────────


class StreamingConfig(BaseModel):
    """Output streaming configuration."""
    enabled: bool = True
    channels: list[str] = Field(default_factory=lambda: ["cli", "sse"])
    include_thoughts: bool = True
    include_tool_calls: bool = True
    include_results: bool = False


class LoggingConfig(BaseModel):
    """Logging configuration."""
    level: str = "info"
    format: str = "structured"
    file: str = ""


class TracingConfig(BaseModel):
    """Distributed tracing configuration."""
    enabled: bool = False
    backend: str = "opentelemetry"
    sample_rate: float = Field(default=1.0, ge=0, le=1)


class MetricDefinition(BaseModel):
    """Custom metric tracking."""
    name: str
    type: str = "counter"         # counter | gauge | histogram
    track: str = ""               # expression


class ObservabilityConfig(BaseModel):
    """Observability configuration."""
    streaming: StreamingConfig = Field(default_factory=StreamingConfig)
    logging: LoggingConfig = Field(default_factory=LoggingConfig)
    tracing: TracingConfig = Field(default_factory=TracingConfig)
    metrics: list[MetricDefinition] = Field(default_factory=list)


# ─── Type definitions ───────────────────────────────────────────────


class TypeField(BaseModel):
    """A field in a custom type definition."""
    type: str = "string"
    description: str = ""
    required: bool = True
    default: Any = None
    enum: list[str] | None = None


# ─── Top-level: AppDefinition ───────────────────────────────────────


class AppDefinition(BaseModel):
    """Complete application definition — the root of a .app.yaml file.

    This is the top-level model that represents the entire YAML schema.
    All blocks are optional except `app`.
    """
    app: AppConfig

    # Single agent (mutually exclusive with agents_config)
    agent: AgentConfig | None = None

    # Multi-agent (mutually exclusive with agent)
    agents: MultiAgentConfig | None = None

    # Standalone tools block (merged with agent.tools if both present)
    tools: list[ToolDefinition] | None = None

    # Memory
    memory: MemoryConfig = Field(default_factory=MemoryConfig)

    # Perception (screenshot/OCR around tool calls)
    perception: PerceptionAppConfig = Field(default_factory=PerceptionAppConfig)

    # Security (advanced — fine-grained grants, denials, approvals, audit)
    capabilities: CapabilitiesConfig = Field(default_factory=CapabilitiesConfig)

    # Security (simple — profile + sandbox shorthand)
    security: SecurityAppConfig | None = None

    # Triggers
    triggers: list[TriggerDefinition] = Field(default_factory=list)

    # Explicit flow (overrides default agent loop when present)
    flow: list[FlowStep] | None = None

    # Macros
    macros: list[MacroDefinition] = Field(default_factory=list)

    # Variables
    variables: dict[str, Any] = Field(default_factory=dict)

    # Custom types
    types: dict[str, dict[str, Any]] = Field(default_factory=dict)

    # Observability
    observability: ObservabilityConfig = Field(default_factory=ObservabilityConfig)

    # Module configuration — per-module settings passed to on_config_update()
    module_config: dict[str, dict[str, Any]] = Field(
        default_factory=dict,
        description=(
            "Per-module configuration. Keys are module IDs, values are config dicts. "
            "Applied at app startup via each module's on_config_update() lifecycle hook. "
            "Example: {web_search: {engine: google, max_results: 5}}"
        ),
    )

    def is_multi_agent(self) -> bool:
        """Check if this is a multi-agent application."""
        return self.agents is not None and len(self.agents.agents) > 0

    def get_all_tools(self) -> list[ToolDefinition]:
        """Get all tools from agent(s) + top-level tools blocks."""
        result: list[ToolDefinition] = []
        if self.agent and self.agent.tools:
            result.extend(self.agent.tools)
        if self.agents:
            for a in self.agents.agents:
                if a.tools:
                    result.extend(a.tools)
        if self.tools:
            result.extend(self.tools)
        return result

    def get_agent(self, agent_id: str = "") -> AgentConfig | None:
        """Get an agent by ID. Empty string returns the default/single agent."""
        if not agent_id or agent_id == "default":
            return self.agent
        if self.agents:
            for a in self.agents.agents:
                if a.id == agent_id:
                    return a
        return None

    def get_all_module_ids(self) -> set[str]:
        """Extract all unique module IDs from tools and capabilities."""
        modules: set[str] = set()
        for tool in self.get_all_tools():
            if tool.module:
                modules.add(tool.module)
        if self.agents:
            for a in self.agents.agents:
                for tool in a.tools:
                    if tool.module:
                        modules.add(tool.module)
        for grant in self.capabilities.grant:
            modules.add(grant.module)
        return modules
