"""LLMOS App Language — declarative AI application framework (Agentique Mode).

This package implements the YAML-based execution mode where the LLM decides
autonomously which tools to call.  The counterpart is the IML Protocol
(Compiler Mode, in ``protocol/`` and ``orchestration/``) where execution
plans are deterministic and SDK-driven.

Both modes share the same modules (18+), security pipeline, event bus, and
identity system.  The convergence point is ``module.execute()`` — reached
via DaemonToolExecutor (Agentique) or PlanExecutor (Compiler).
"""
