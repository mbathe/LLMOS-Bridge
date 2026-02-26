"""TriggerModule — IML interface to TriggerDaemon.

This module exposes the TriggerDaemon capabilities to LLM plans via IML actions.
An LLM can register, activate, deactivate, and list triggers from within a plan —
enabling trigger chaining where one plan creates triggers that will fire future plans.
"""

from llmos_bridge.modules.triggers.module import TriggerModule

__all__ = ["TriggerModule"]
