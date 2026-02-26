"""LLMOS Bridge — Local daemon bridging LLMs to the operating system.

LLMOS Bridge implements the IML (Instruction Markup Language) protocol v2,
providing a standardised interface for LLMs to interact with files, applications,
GUIs, IoT devices, databases, and external services.

Architecture layers (bottom to top):
    1. Protocol  — IML parser, Pydantic validation, JSONSchema generation
    2. Security  — Permission profiles, action guards, output sanitiser, audit trail
    3. Orchestration — DAG scheduler, state machine, executor, rollback engine
    4. Modules   — FileSystem, OS, Excel, Word, Browser, GUI, API, Database, IoT
    5. Perception — Screenshot capture, OCR, visual feedback loop
    6. Memory    — ChromaDB vector store, SQLite state store, context builder
    7. API/SDK   — FastAPI HTTP + WebSocket server, LangChain SDK surface
"""

__version__ = "0.1.0"
__protocol_version__ = "2.0"
__author__ = "LLMOS Bridge Contributors"
__license__ = "Apache-2.0"

from llmos_bridge.protocol.models import IMLAction, IMLPlan

__all__ = [
    "__version__",
    "__protocol_version__",
    "IMLPlan",
    "IMLAction",
]
