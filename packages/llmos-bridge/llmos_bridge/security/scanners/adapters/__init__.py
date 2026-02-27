"""Optional scanner adapters for external ML libraries.

These require additional dependencies:
  - LLMGuardScanner:   ``pip install llm-guard``
  - PromptGuardScanner: ``pip install transformers torch``

Install via the optional extra::

    pip install llmos-bridge[security-ml]
"""

__all__: list[str] = []

try:
    from llmos_bridge.security.scanners.adapters.llm_guard import LLMGuardScanner

    __all__.append("LLMGuardScanner")
except ImportError:
    pass

try:
    from llmos_bridge.security.scanners.adapters.prompt_guard import (
        PromptGuardScanner,
    )

    __all__.append("PromptGuardScanner")
except ImportError:
    pass
