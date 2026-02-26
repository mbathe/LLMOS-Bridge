"""Protocol layer — IML v2 parsing, validation, schema, repair, migration, and compat."""

from llmos_bridge.protocol.compat import CompatibilityReport, ModuleVersionChecker
from llmos_bridge.protocol.migration import MigrationPipeline, MigrationRegistry, default_pipeline
from llmos_bridge.protocol.models import (
    ActionStatus,
    CompilerTrace,
    ExecutionMode,
    IMLAction,
    IMLPlan,
    MemoryConfig,
    OnErrorBehavior,
    PerceptionConfig,
    PlanMetadata,
    PlanMode,
    PlanStatus,
    RetryConfig,
    RollbackConfig,
)
from llmos_bridge.protocol.parser import IMLParser
from llmos_bridge.protocol.repair import CorrectionPromptFormatter, IMLRepair, RepairResult
from llmos_bridge.protocol.schema import SchemaRegistry
from llmos_bridge.protocol.validator import IMLValidator

__all__ = [
    # Core models
    "IMLPlan",
    "IMLAction",
    "ExecutionMode",
    "PlanMode",
    "CompilerTrace",
    "OnErrorBehavior",
    "PlanStatus",
    "ActionStatus",
    "RetryConfig",
    "RollbackConfig",
    "PerceptionConfig",
    "MemoryConfig",
    "PlanMetadata",
    # Parsing and validation
    "IMLParser",
    "IMLValidator",
    "SchemaRegistry",
    # Repair and correction
    "IMLRepair",
    "RepairResult",
    "CorrectionPromptFormatter",
    # Version compatibility
    "ModuleVersionChecker",
    "CompatibilityReport",
    # Schema migration
    "MigrationPipeline",
    "MigrationRegistry",
    "default_pipeline",
]
