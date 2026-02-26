"""Typed parameter models for every registered action.

Each sub-module mirrors a module in the modules layer and exposes
a ``PARAMS_MAP: dict[str, type[BaseModel]]`` mapping action names
to their Pydantic param models.

The SchemaRegistry uses these maps to:
  - Validate action params at parse time
  - Generate LLM-friendly JSONSchema for each action (Capability Manifest)
"""

from llmos_bridge.protocol.params.api_http import PARAMS_MAP as API_HTTP_PARAMS
from llmos_bridge.protocol.params.browser import PARAMS_MAP as BROWSER_PARAMS
from llmos_bridge.protocol.params.database import PARAMS_MAP as DATABASE_PARAMS
from llmos_bridge.protocol.params.excel import PARAMS_MAP as EXCEL_PARAMS
from llmos_bridge.protocol.params.filesystem import PARAMS_MAP as FILESYSTEM_PARAMS
from llmos_bridge.protocol.params.gui import PARAMS_MAP as GUI_PARAMS
from llmos_bridge.protocol.params.iot import PARAMS_MAP as IOT_PARAMS
from llmos_bridge.protocol.params.os_exec import PARAMS_MAP as OS_EXEC_PARAMS
from llmos_bridge.protocol.params.perception_vision import PARAMS_MAP as VISION_PARAMS
from llmos_bridge.protocol.params.powerpoint import PARAMS_MAP as POWERPOINT_PARAMS
from llmos_bridge.protocol.params.recording import PARAMS_MAP as RECORDING_PARAMS
from llmos_bridge.protocol.params.word import PARAMS_MAP as WORD_PARAMS

ALL_PARAMS: dict[str, dict[str, type]] = {
    "filesystem": FILESYSTEM_PARAMS,
    "os_exec": OS_EXEC_PARAMS,
    "excel": EXCEL_PARAMS,
    "word": WORD_PARAMS,
    "powerpoint": POWERPOINT_PARAMS,
    "browser": BROWSER_PARAMS,
    "gui": GUI_PARAMS,
    "api_http": API_HTTP_PARAMS,
    "database": DATABASE_PARAMS,
    "iot": IOT_PARAMS,
    "vision": VISION_PARAMS,
    "recording": RECORDING_PARAMS,
}

__all__ = [
    "ALL_PARAMS",
    "FILESYSTEM_PARAMS",
    "OS_EXEC_PARAMS",
    "EXCEL_PARAMS",
    "WORD_PARAMS",
    "POWERPOINT_PARAMS",
    "BROWSER_PARAMS",
    "GUI_PARAMS",
    "API_HTTP_PARAMS",
    "DATABASE_PARAMS",
    "IOT_PARAMS",
    "VISION_PARAMS",
    "RECORDING_PARAMS",
]
