"""Security layer — Permission profiles, guards, audit trail, OS-level permissions, decorators, scanners."""

from llmos_bridge.security.audit import AuditLogger
from llmos_bridge.security.decorators import (
    audit_trail,
    data_classification,
    intent_verified,
    rate_limited,
    requires_permission,
    sensitive_action,
)
from llmos_bridge.security.guard import PermissionGuard
from llmos_bridge.security.intent_verifier import (
    IntentVerifier,
    ThreatDetail,
    ThreatType,
    VerificationResult,
    VerificationVerdict,
)
from llmos_bridge.security.llm_client import LLMClient, LLMMessage, LLMResponse, NullLLMClient
from llmos_bridge.security.manager import SecurityManager
from llmos_bridge.security.models import (
    DataClassification,
    Permission,
    PermissionGrant,
    PermissionScope,
    RiskLevel,
)
from llmos_bridge.security.permission_store import PermissionStore
from llmos_bridge.security.permissions import PermissionManager
from llmos_bridge.security.profiles import PermissionProfile, PermissionProfileConfig
from llmos_bridge.security.prompt_composer import PromptComposer
from llmos_bridge.security.providers import (
    AnthropicLLMClient,
    BaseHTTPLLMClient,
    OllamaLLMClient,
    OpenAILLMClient,
)
from llmos_bridge.security.rate_limiter import ActionRateLimiter
from llmos_bridge.security.sanitizer import OutputSanitizer
from llmos_bridge.security.scanners import (
    HeuristicScanner,
    InputScanner,
    PatternRule,
    PipelineResult,
    ScanContext,
    ScannerRegistry,
    ScanResult,
    ScanVerdict,
    SecurityPipeline,
)
from llmos_bridge.security.threat_categories import (
    ThreatCategory,
    ThreatCategoryRegistry,
)

__all__ = [
    # Existing
    "PermissionProfile",
    "PermissionProfileConfig",
    "PermissionGuard",
    "AuditLogger",
    "OutputSanitizer",
    # OS-level permission system
    "Permission",
    "RiskLevel",
    "DataClassification",
    "PermissionScope",
    "PermissionGrant",
    "PermissionStore",
    "PermissionManager",
    "ActionRateLimiter",
    "SecurityManager",
    # Decorators
    "requires_permission",
    "sensitive_action",
    "rate_limited",
    "audit_trail",
    "data_classification",
    "intent_verified",
    # Intent verification (Couche 1)
    "IntentVerifier",
    "VerificationResult",
    "VerificationVerdict",
    "ThreatType",
    "ThreatDetail",
    "LLMClient",
    "LLMMessage",
    "LLMResponse",
    "NullLLMClient",
    # Extensible verification (Couche 1 — Phase 2)
    "ThreatCategory",
    "ThreatCategoryRegistry",
    "PromptComposer",
    "BaseHTTPLLMClient",
    "OpenAILLMClient",
    "AnthropicLLMClient",
    "OllamaLLMClient",
    # Scanners (Layers 1-2)
    "InputScanner",
    "ScanResult",
    "ScanContext",
    "ScanVerdict",
    "ScannerRegistry",
    "SecurityPipeline",
    "PipelineResult",
    "HeuristicScanner",
    "PatternRule",
]
