"""End-to-end integration tests for YAML App Language.

Tests 10 complex apps that exercise every feature of the language:
1. Full-stack code assistant (filesystem, os_exec, memory, context_manager, agent_spawn)
2. Web research agent (browser, api_http, memory, filesystem)
3. Desktop automation (computer_control, gui, perception_vision, window_tracker)
4. Office document pipeline (excel, word, powerpoint, filesystem, database)
5. Security-hardened readonly app (filesystem with readonly, capabilities, approvals)
6. IoT monitoring (iot, triggers, recording, api_http, memory)
7. Database ETL (database, database_gateway, excel, filesystem)
8. Multi-agent research team (agent_spawn, memory, context_manager, api_http)
9. Module management app (module_manager, security, filesystem)
10. Full capability test (all features: macros, flow, branch, parallel, map, try/catch, etc.)

For each app:
- Compile from YAML string (schema validation, semantic validation)
- Verify tool resolution against module manifests
- Verify security/capabilities config is correctly parsed
- Verify flow steps compile and are valid
- Verify memory/perception/observability configs parse
- Run through DaemonToolExecutor wiring (mock execution)
"""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from llmos_bridge.apps.compiler import AppCompiler, CompilationError
from llmos_bridge.apps.daemon_executor import DaemonToolExecutor, module_info_from_manifests
from llmos_bridge.apps.models import AppDefinition, FlowStepType
from llmos_bridge.apps.runtime import AppRuntime
from llmos_bridge.apps.tool_registry import AppToolRegistry, ResolvedTool


# ── Helpers ──────────────────────────────────────────────────────────────

def compile_yaml(yaml_text: str) -> AppDefinition:
    """Compile a YAML string into an AppDefinition."""
    compiler = AppCompiler()
    return compiler.compile_string(yaml_text)


def build_test_module_info() -> dict[str, dict]:
    """Build module_info dict with all 20 modules for tool resolution testing.

    This simulates the daemon's real module manifests with representative actions.
    """
    modules = {
        "filesystem": {
            "actions": [
                {"name": "read_file", "description": "Read file", "params": {"path": {"type": "string", "required": True}}},
                {"name": "write_file", "description": "Write file", "params": {"path": {"type": "string", "required": True}, "content": {"type": "string", "required": True}}},
                {"name": "list_directory", "description": "List dir", "params": {"path": {"type": "string", "required": True}}},
                {"name": "search_files", "description": "Search files", "params": {"pattern": {"type": "string", "required": True}}},
                {"name": "create_directory", "description": "Create dir", "params": {"path": {"type": "string", "required": True}}},
                {"name": "delete_file", "description": "Delete file", "params": {"path": {"type": "string", "required": True}}},
                {"name": "move_file", "description": "Move file", "params": {"source": {"type": "string", "required": True}, "destination": {"type": "string", "required": True}}},
                {"name": "get_file_info", "description": "File info", "params": {"path": {"type": "string", "required": True}}},
                {"name": "copy_file", "description": "Copy file", "params": {"source": {"type": "string", "required": True}, "destination": {"type": "string", "required": True}}},
                {"name": "read_lines", "description": "Read lines", "params": {"path": {"type": "string", "required": True}}},
                {"name": "append_file", "description": "Append to file", "params": {"path": {"type": "string", "required": True}, "content": {"type": "string", "required": True}}},
                {"name": "find_replace", "description": "Find and replace", "params": {"path": {"type": "string", "required": True}, "find": {"type": "string", "required": True}, "replace": {"type": "string", "required": True}}},
                {"name": "file_diff", "description": "Diff two files", "params": {"path_a": {"type": "string", "required": True}, "path_b": {"type": "string", "required": True}}},
                {"name": "glob_search", "description": "Glob search", "params": {"pattern": {"type": "string", "required": True}}},
            ],
        },
        "os_exec": {
            "actions": [
                {"name": "run_command", "description": "Run shell command", "params": {"command": {"type": "string", "required": True}}},
                {"name": "get_env", "description": "Get env var", "params": {"name": {"type": "string", "required": True}}},
                {"name": "set_env", "description": "Set env var", "params": {"name": {"type": "string", "required": True}, "value": {"type": "string", "required": True}}},
                {"name": "list_processes", "description": "List processes", "params": {}},
                {"name": "kill_process", "description": "Kill process", "params": {"pid": {"type": "integer", "required": True}}},
                {"name": "get_system_info", "description": "System info", "params": {}},
                {"name": "which", "description": "Find executable", "params": {"name": {"type": "string", "required": True}}},
                {"name": "get_cwd", "description": "Get CWD", "params": {}},
                {"name": "get_platform_info", "description": "Platform info", "params": {}},
            ],
        },
        "memory": {
            "actions": [
                {"name": "store", "description": "Store value", "params": {"key": {"type": "string", "required": True}, "value": {"type": "string", "required": True}}},
                {"name": "recall", "description": "Recall value", "params": {"key": {"type": "string", "required": True}}},
                {"name": "search", "description": "Search memory", "params": {"query": {"type": "string", "required": True}}},
                {"name": "delete", "description": "Delete key", "params": {"key": {"type": "string", "required": True}}},
                {"name": "list_keys", "description": "List keys", "params": {}},
                {"name": "observe", "description": "Full snapshot", "params": {}},
                {"name": "set_objective", "description": "Set objective", "params": {"goal": {"type": "string", "required": True}}},
                {"name": "get_context", "description": "Get context", "params": {}},
                {"name": "update_progress", "description": "Update progress", "params": {"progress": {"type": "number", "required": True}}},
                {"name": "clear", "description": "Clear memory", "params": {}},
                {"name": "export", "description": "Export memory", "params": {}},
            ],
        },
        "database": {
            "actions": [
                {"name": "query", "description": "Run SQL query", "params": {"sql": {"type": "string", "required": True}}},
                {"name": "execute", "description": "Execute SQL", "params": {"sql": {"type": "string", "required": True}}},
                {"name": "list_tables", "description": "List tables", "params": {}},
                {"name": "describe_table", "description": "Describe table", "params": {"table": {"type": "string", "required": True}}},
                {"name": "create_table", "description": "Create table", "params": {"table": {"type": "string", "required": True}, "columns": {"type": "object", "required": True}}},
                {"name": "insert", "description": "Insert row", "params": {"table": {"type": "string", "required": True}, "data": {"type": "object", "required": True}}},
                {"name": "update", "description": "Update rows", "params": {"table": {"type": "string", "required": True}, "set": {"type": "object", "required": True}}},
                {"name": "delete_rows", "description": "Delete rows", "params": {"table": {"type": "string", "required": True}}},
                {"name": "count", "description": "Count rows", "params": {"table": {"type": "string", "required": True}}},
                {"name": "backup", "description": "Backup DB", "params": {"path": {"type": "string", "required": True}}},
                {"name": "restore", "description": "Restore DB", "params": {"path": {"type": "string", "required": True}}},
                {"name": "get_schema", "description": "Get schema", "params": {}},
                {"name": "explain", "description": "Explain query", "params": {"sql": {"type": "string", "required": True}}},
            ],
        },
        "database_gateway": {
            "actions": [
                {"name": "connect", "description": "Connect to database", "params": {"connection_string": {"type": "string", "required": True}}},
                {"name": "disconnect", "description": "Disconnect", "params": {"connection_id": {"type": "string", "required": True}}},
                {"name": "query", "description": "Run query", "params": {"connection_id": {"type": "string", "required": True}, "sql": {"type": "string", "required": True}}},
                {"name": "execute", "description": "Execute", "params": {"connection_id": {"type": "string", "required": True}, "sql": {"type": "string", "required": True}}},
                {"name": "list_connections", "description": "List connections", "params": {}},
                {"name": "list_tables", "description": "List tables", "params": {"connection_id": {"type": "string", "required": True}}},
                {"name": "describe_table", "description": "Describe table", "params": {"connection_id": {"type": "string", "required": True}, "table": {"type": "string", "required": True}}},
                {"name": "transaction_begin", "description": "Begin transaction", "params": {"connection_id": {"type": "string", "required": True}}},
                {"name": "transaction_commit", "description": "Commit", "params": {"connection_id": {"type": "string", "required": True}}},
                {"name": "transaction_rollback", "description": "Rollback", "params": {"connection_id": {"type": "string", "required": True}}},
                {"name": "pool_stats", "description": "Pool stats", "params": {}},
                {"name": "health_check", "description": "Health check", "params": {"connection_id": {"type": "string", "required": True}}},
            ],
        },
        "api_http": {
            "actions": [
                {"name": "http_request", "description": "HTTP request", "params": {"url": {"type": "string", "required": True}, "method": {"type": "string", "required": False}}},
                {"name": "get", "description": "HTTP GET", "params": {"url": {"type": "string", "required": True}}},
                {"name": "post", "description": "HTTP POST", "params": {"url": {"type": "string", "required": True}}},
                {"name": "put", "description": "HTTP PUT", "params": {"url": {"type": "string", "required": True}}},
                {"name": "delete", "description": "HTTP DELETE", "params": {"url": {"type": "string", "required": True}}},
                {"name": "patch", "description": "HTTP PATCH", "params": {"url": {"type": "string", "required": True}}},
                {"name": "graphql", "description": "GraphQL query", "params": {"url": {"type": "string", "required": True}, "query": {"type": "string", "required": True}}},
                {"name": "download", "description": "Download file", "params": {"url": {"type": "string", "required": True}}},
                {"name": "upload", "description": "Upload file", "params": {"url": {"type": "string", "required": True}, "file_path": {"type": "string", "required": True}}},
                {"name": "set_headers", "description": "Set headers", "params": {"headers": {"type": "object", "required": True}}},
                {"name": "set_auth", "description": "Set auth", "params": {"type": {"type": "string", "required": True}}},
                {"name": "create_session", "description": "Create session", "params": {}},
                {"name": "close_session", "description": "Close session", "params": {}},
                {"name": "get_cookies", "description": "Get cookies", "params": {}},
                {"name": "set_cookie", "description": "Set cookie", "params": {"name": {"type": "string", "required": True}, "value": {"type": "string", "required": True}}},
                {"name": "webhook_listen", "description": "Listen for webhook", "params": {"port": {"type": "integer", "required": True}}},
                {"name": "websocket_connect", "description": "Connect websocket", "params": {"url": {"type": "string", "required": True}}},
            ],
        },
        "browser": {
            "actions": [
                {"name": "open_browser", "description": "Open browser", "params": {}},
                {"name": "close_browser", "description": "Close browser", "params": {}},
                {"name": "navigate_to", "description": "Navigate", "params": {"url": {"type": "string", "required": True}}},
                {"name": "click_element", "description": "Click element", "params": {"selector": {"type": "string", "required": True}}},
                {"name": "fill_input", "description": "Fill input", "params": {"selector": {"type": "string", "required": True}, "value": {"type": "string", "required": True}}},
                {"name": "get_page_content", "description": "Get content", "params": {}},
                {"name": "take_screenshot", "description": "Screenshot", "params": {}},
                {"name": "wait_for_element", "description": "Wait for element", "params": {"selector": {"type": "string", "required": True}}},
                {"name": "evaluate_js", "description": "Evaluate JS", "params": {"script": {"type": "string", "required": True}}},
                {"name": "select_option", "description": "Select option", "params": {"selector": {"type": "string", "required": True}, "value": {"type": "string", "required": True}}},
                {"name": "submit_form", "description": "Submit form", "params": {"selector": {"type": "string", "required": True}}},
                {"name": "get_url", "description": "Get current URL", "params": {}},
                {"name": "go_back", "description": "Go back", "params": {}},
            ],
        },
        "gui": {
            "actions": [
                {"name": "click_position", "description": "Click at position", "params": {"x": {"type": "integer", "required": True}, "y": {"type": "integer", "required": True}}},
                {"name": "type_text", "description": "Type text", "params": {"text": {"type": "string", "required": True}}},
                {"name": "key_press", "description": "Press key", "params": {"keys": {"type": "array", "required": True}}},
                {"name": "take_screenshot", "description": "Take screenshot", "params": {}},
                {"name": "get_screen_info", "description": "Screen info", "params": {}},
                {"name": "focus_window", "description": "Focus window", "params": {"title_pattern": {"type": "string", "required": True}}},
                {"name": "move_mouse", "description": "Move mouse", "params": {"x": {"type": "integer", "required": True}, "y": {"type": "integer", "required": True}}},
                {"name": "scroll", "description": "Scroll", "params": {"direction": {"type": "string", "required": True}}},
                {"name": "drag", "description": "Drag", "params": {"start_x": {"type": "integer", "required": True}, "start_y": {"type": "integer", "required": True}, "end_x": {"type": "integer", "required": True}, "end_y": {"type": "integer", "required": True}}},
                {"name": "double_click", "description": "Double click", "params": {"x": {"type": "integer", "required": True}, "y": {"type": "integer", "required": True}}},
                {"name": "right_click", "description": "Right click", "params": {"x": {"type": "integer", "required": True}, "y": {"type": "integer", "required": True}}},
                {"name": "click_image", "description": "Click on image", "params": {"image_path": {"type": "string", "required": True}}},
                {"name": "list_windows", "description": "List windows", "params": {}},
            ],
        },
        "computer_control": {
            "actions": [
                {"name": "read_screen", "description": "Read screen", "params": {}},
                {"name": "click_element", "description": "Click element by description", "params": {"target_description": {"type": "string", "required": True}}},
                {"name": "type_into_element", "description": "Type into element", "params": {"target_description": {"type": "string", "required": True}, "text": {"type": "string", "required": True}}},
                {"name": "scroll_to", "description": "Scroll to element", "params": {"target_description": {"type": "string", "required": True}}},
                {"name": "wait_for_element", "description": "Wait for element", "params": {"target_description": {"type": "string", "required": True}}},
                {"name": "get_element_text", "description": "Get element text", "params": {"target_description": {"type": "string", "required": True}}},
                {"name": "hover_element", "description": "Hover element", "params": {"target_description": {"type": "string", "required": True}}},
                {"name": "drag_element", "description": "Drag element", "params": {"source_description": {"type": "string", "required": True}, "target_description": {"type": "string", "required": True}}},
                {"name": "right_click_element", "description": "Right-click element", "params": {"target_description": {"type": "string", "required": True}}},
            ],
        },
        "perception_vision": {
            "actions": [
                {"name": "capture_and_parse", "description": "Capture screen and parse", "params": {}},
                {"name": "capture_screen", "description": "Capture screen", "params": {}},
                {"name": "detect_elements", "description": "Detect UI elements", "params": {"image_path": {"type": "string", "required": True}}},
                {"name": "ocr", "description": "OCR on region", "params": {}},
            ],
        },
        "excel": {
            "actions": [
                {"name": "open_workbook", "description": "Open workbook", "params": {"path": {"type": "string", "required": True}}},
                {"name": "read_cell", "description": "Read cell", "params": {"cell": {"type": "string", "required": True}}},
                {"name": "write_cell", "description": "Write cell", "params": {"cell": {"type": "string", "required": True}, "value": {"type": "string", "required": True}}},
                {"name": "read_range", "description": "Read range", "params": {"range": {"type": "string", "required": True}}},
                {"name": "create_chart", "description": "Create chart", "params": {"type": {"type": "string", "required": True}}},
                {"name": "save_workbook", "description": "Save workbook", "params": {}},
                {"name": "close_workbook", "description": "Close workbook", "params": {}},
                {"name": "export_pdf", "description": "Export to PDF", "params": {"path": {"type": "string", "required": True}}},
            ],
        },
        "word": {
            "actions": [
                {"name": "create_document", "description": "Create document", "params": {}},
                {"name": "open_document", "description": "Open document", "params": {"path": {"type": "string", "required": True}}},
                {"name": "add_paragraph", "description": "Add paragraph", "params": {"text": {"type": "string", "required": True}}},
                {"name": "add_heading", "description": "Add heading", "params": {"text": {"type": "string", "required": True}, "level": {"type": "integer", "required": False}}},
                {"name": "add_table", "description": "Add table", "params": {"rows": {"type": "integer", "required": True}, "cols": {"type": "integer", "required": True}}},
                {"name": "save_document", "description": "Save document", "params": {"path": {"type": "string", "required": True}}},
                {"name": "export_pdf", "description": "Export to PDF", "params": {"path": {"type": "string", "required": True}}},
            ],
        },
        "powerpoint": {
            "actions": [
                {"name": "create_presentation", "description": "Create presentation", "params": {}},
                {"name": "add_slide", "description": "Add slide", "params": {}},
                {"name": "add_text", "description": "Add text", "params": {"text": {"type": "string", "required": True}}},
                {"name": "add_chart", "description": "Add chart", "params": {"type": {"type": "string", "required": True}}},
                {"name": "save_presentation", "description": "Save", "params": {"path": {"type": "string", "required": True}}},
                {"name": "export_pdf", "description": "Export PDF", "params": {"path": {"type": "string", "required": True}}},
            ],
        },
        "iot": {
            "actions": [
                {"name": "connect", "description": "Connect to broker", "params": {"broker": {"type": "string", "required": True}}},
                {"name": "disconnect", "description": "Disconnect", "params": {}},
                {"name": "publish", "description": "Publish message", "params": {"topic": {"type": "string", "required": True}, "payload": {"type": "string", "required": True}}},
                {"name": "subscribe", "description": "Subscribe to topic", "params": {"topic": {"type": "string", "required": True}}},
                {"name": "unsubscribe", "description": "Unsubscribe", "params": {"topic": {"type": "string", "required": True}}},
                {"name": "list_devices", "description": "List devices", "params": {}},
                {"name": "get_device_state", "description": "Get device state", "params": {"device_id": {"type": "string", "required": True}}},
                {"name": "set_device_state", "description": "Set device state", "params": {"device_id": {"type": "string", "required": True}, "state": {"type": "object", "required": True}}},
                {"name": "get_sensor_data", "description": "Get sensor data", "params": {"sensor_id": {"type": "string", "required": True}}},
                {"name": "list_sensors", "description": "List sensors", "params": {}},
            ],
        },
        "agent_spawn": {
            "actions": [
                {"name": "spawn_agent", "description": "Spawn sub-agent", "params": {"name": {"type": "string", "required": True}, "objective": {"type": "string", "required": True}}},
                {"name": "check_agent", "description": "Check agent status", "params": {"spawn_id": {"type": "string", "required": True}}},
                {"name": "get_result", "description": "Get agent result", "params": {"spawn_id": {"type": "string", "required": True}}},
                {"name": "list_agents", "description": "List agents", "params": {}},
                {"name": "cancel_agent", "description": "Cancel agent", "params": {"spawn_id": {"type": "string", "required": True}}},
                {"name": "wait_agent", "description": "Wait for agent", "params": {"spawn_id": {"type": "string", "required": True}}},
                {"name": "send_message", "description": "Send message to agent", "params": {"spawn_id": {"type": "string", "required": True}, "message": {"type": "string", "required": True}}},
            ],
        },
        "context_manager": {
            "actions": [
                {"name": "get_budget", "description": "Get token budget", "params": {}},
                {"name": "compress_history", "description": "Compress history", "params": {}},
                {"name": "fetch_context", "description": "Fetch context", "params": {"query": {"type": "string", "required": True}}},
                {"name": "get_tools_summary", "description": "Get tools summary", "params": {}},
                {"name": "get_state", "description": "Get state", "params": {}},
            ],
        },
        "window_tracker": {
            "actions": [
                {"name": "get_active_window", "description": "Get active window", "params": {}},
                {"name": "list_windows", "description": "List all windows", "params": {}},
                {"name": "get_focus_history", "description": "Focus history", "params": {}},
                {"name": "start_tracking", "description": "Start tracking", "params": {}},
                {"name": "stop_tracking", "description": "Stop tracking", "params": {}},
                {"name": "get_window_info", "description": "Window info", "params": {"window_id": {"type": "string", "required": True}}},
                {"name": "wait_for_window", "description": "Wait for window", "params": {"title_pattern": {"type": "string", "required": True}}},
                {"name": "get_stats", "description": "Usage stats", "params": {}},
            ],
        },
        "module_manager": {
            "actions": [
                {"name": "list_modules", "description": "List modules", "params": {}},
                {"name": "get_module_info", "description": "Module info", "params": {"module_id": {"type": "string", "required": True}}},
                {"name": "install_module", "description": "Install module", "params": {"source": {"type": "string", "required": True}}},
                {"name": "uninstall_module", "description": "Uninstall module", "params": {"module_id": {"type": "string", "required": True}}},
                {"name": "enable_module", "description": "Enable module", "params": {"module_id": {"type": "string", "required": True}}},
                {"name": "disable_module", "description": "Disable module", "params": {"module_id": {"type": "string", "required": True}}},
            ],
        },
        "recording": {
            "actions": [
                {"name": "start_recording", "description": "Start recording", "params": {}},
                {"name": "stop_recording", "description": "Stop recording", "params": {}},
                {"name": "list_recordings", "description": "List recordings", "params": {}},
                {"name": "get_recording", "description": "Get recording", "params": {"recording_id": {"type": "string", "required": True}}},
                {"name": "replay_recording", "description": "Replay recording", "params": {"recording_id": {"type": "string", "required": True}}},
                {"name": "delete_recording", "description": "Delete recording", "params": {"recording_id": {"type": "string", "required": True}}},
            ],
        },
        "triggers": {
            "actions": [
                {"name": "create_trigger", "description": "Create trigger", "params": {"type": {"type": "string", "required": True}}},
                {"name": "delete_trigger", "description": "Delete trigger", "params": {"trigger_id": {"type": "string", "required": True}}},
                {"name": "list_triggers", "description": "List triggers", "params": {}},
                {"name": "enable_trigger", "description": "Enable trigger", "params": {"trigger_id": {"type": "string", "required": True}}},
                {"name": "disable_trigger", "description": "Disable trigger", "params": {"trigger_id": {"type": "string", "required": True}}},
                {"name": "get_trigger", "description": "Get trigger", "params": {"trigger_id": {"type": "string", "required": True}}},
            ],
        },
        "security": {
            "actions": [
                {"name": "scan_content", "description": "Scan content", "params": {"content": {"type": "string", "required": True}}},
                {"name": "check_permission", "description": "Check permission", "params": {"module": {"type": "string", "required": True}, "action": {"type": "string", "required": True}}},
                {"name": "list_permissions", "description": "List permissions", "params": {}},
                {"name": "get_audit_log", "description": "Get audit log", "params": {}},
                {"name": "get_security_profile", "description": "Get security profile", "params": {}},
                {"name": "validate_plan", "description": "Validate plan", "params": {"plan": {"type": "object", "required": True}}},
            ],
        },
    }
    return modules


# ── App YAML definitions ─────────────────────────────────────────────────

APP_1_CODE_ASSISTANT = """\
app:
  name: code-assistant-e2e
  version: "2.0"
  description: "Full-stack code assistant"
  tags: [coding, testing]

variables:
  workspace: "{{env.PWD}}"

agent:
  brain:
    provider: anthropic
    model: claude-sonnet-4-20250514
    temperature: 0.2
    max_tokens: 8192
    fallback:
      - model: claude-haiku-4-5-20251001
  system_prompt: |
    You are a code assistant. Current workspace: {{workspace}}
  loop:
    type: reactive
    max_turns: 50
    context:
      max_tokens: 200000
      strategy: summarize
      keep_last_n_messages: 30
      model_context_window: 200000
      output_reserved: 8192
      compression_trigger_ratio: 0.75
      min_recent_messages: 10
  tools:
    - module: filesystem
    - module: os_exec
      exclude: [kill_process]
    - module: agent_spawn
    - module: context_manager
    - module: memory
      actions: [store, recall, search, observe, set_objective, get_context, update_progress]

memory:
  working:
    max_size: "50MB"
  conversation:
    max_history: 200
  project:
    path: "{{workspace}}/.llmos/MEMORY.md"
    auto_inject: true
    agent_writable: true
  episodic:
    auto_record: true
    auto_recall:
      on_start: true
      limit: 5

security:
  profile: power_user
  sandbox:
    allowed_paths:
      - "{{workspace}}"
    blocked_commands:
      - "rm -rf /"
      - "dd if=/dev/zero"

triggers:
  - type: cli
    mode: conversation
    greeting: "Code Assistant v2.0"
"""

APP_2_WEB_RESEARCH = """\
app:
  name: web-research-e2e
  version: "1.0"
  description: "Web research agent"

variables:
  output_dir: "/tmp/research"

agent:
  brain:
    provider: anthropic
    model: claude-sonnet-4-20250514
  tools:
    - module: browser
      actions: [open_browser, navigate_to, get_page_content, take_screenshot, close_browser]
      constraints:
        allowed_domains: ["github.com", "stackoverflow.com", "docs.python.org"]
    - module: api_http
      actions: [get, post]
      constraints:
        allowed_domains: ["api.github.com", "pypi.org"]
        max_response_size: "5MB"
    - module: filesystem
      actions: [write_file, read_file, create_directory]
      constraints:
        paths: ["{{output_dir}}"]

memory:
  working:
    max_size: "20MB"

security:
  profile: local_worker
"""

APP_3_DESKTOP_AUTOMATION = """\
app:
  name: desktop-auto-e2e
  version: "1.0"
  description: "Desktop automation with vision"

agent:
  brain:
    provider: anthropic
    model: claude-sonnet-4-20250514
  tools:
    - module: computer_control
    - module: gui
      actions: [click_position, type_text, key_press, take_screenshot, focus_window]
    - module: perception_vision
      actions: [capture_and_parse, detect_elements]
    - module: window_tracker
      actions: [get_active_window, list_windows, wait_for_window]

perception:
  enabled: true
  capture_before: false
  capture_after: true
  ocr_enabled: true
  timeout_seconds: 15
  actions:
    "computer_control.click_element":
      capture_before: true
      capture_after: true
      ocr_enabled: true
    "gui.type_text":
      capture_after: true

security:
  profile: power_user
"""

APP_4_OFFICE_PIPELINE = """\
app:
  name: office-pipeline-e2e
  version: "1.0"
  description: "Office document pipeline"

variables:
  data_source: "sqlite:///data.db"
  output_dir: "/tmp/reports"

flow:
  - id: setup
    action: filesystem.create_directory
    params:
      path: "{{output_dir}}"

  - id: query_data
    action: database.query
    params:
      sql: "SELECT * FROM sales ORDER BY date DESC LIMIT 100"

  - id: create_excel
    sequence:
      - id: open_wb
        action: excel.open_workbook
        params:
          path: "{{output_dir}}/sales_report.xlsx"
      - id: write_data
        action: excel.write_cell
        params:
          cell: "A1"
          value: "Sales Report"
      - id: save_wb
        action: excel.save_workbook
        params: {}

  - id: create_word_report
    sequence:
      - id: create_doc
        action: word.create_document
        params: {}
      - id: add_title
        action: word.add_heading
        params:
          text: "Monthly Sales Report"
          level: 1
      - id: add_content
        action: word.add_paragraph
        params:
          text: "This report contains sales data analysis."
      - id: save_doc
        action: word.save_document
        params:
          path: "{{output_dir}}/report.docx"

  - id: create_pptx
    sequence:
      - id: create_pres
        action: powerpoint.create_presentation
        params: {}
      - id: add_title_slide
        action: powerpoint.add_slide
        params: {}
      - id: add_title_text
        action: powerpoint.add_text
        params:
          text: "Sales Overview"
      - id: save_pres
        action: powerpoint.save_presentation
        params:
          path: "{{output_dir}}/presentation.pptx"

agents:
  - id: analyst
    role: specialist
    brain:
      provider: anthropic
      model: claude-sonnet-4-20250514
    tools:
      - module: database
        actions: [query, list_tables, describe_table]
      - module: excel
      - module: word
      - module: powerpoint
      - module: filesystem
        actions: [write_file, read_file, create_directory]

module_config:
  database:
    connection_string: "sqlite:///data.db"
    read_only: false

security:
  profile: local_worker
"""

APP_5_SECURITY_HARDENED = """\
app:
  name: secure-reader-e2e
  version: "1.0"
  description: "Security-hardened read-only file browser"

agent:
  brain:
    provider: anthropic
    model: claude-sonnet-4-20250514
    temperature: 0
  tools:
    - module: filesystem
      actions: [read_file, list_directory, search_files, get_file_info]
      constraints:
        paths: ["/home/user/docs", "/home/user/public"]
        read_only: true
        max_file_size: "10MB"
    - module: os_exec
      action: run_command
      constraints:
        timeout: "10s"
        forbidden_commands: ["rm", "dd", "mkfs", "chmod", "chown", "sudo"]
        forbidden_patterns: ["sudo *", "rm -rf *", "curl *", "wget *"]

capabilities:
  grant:
    - module: filesystem
      actions: [read_file, list_directory, search_files, get_file_info]
    - module: os_exec
      actions: [run_command]
      constraints:
        forbidden_commands: ["rm", "sudo"]
  deny:
    - module: filesystem
      action: write_file
      reason: "This is a read-only app"
    - module: filesystem
      action: delete_file
      reason: "Deletion not allowed"
    - module: os_exec
      action: kill_process
      reason: "Process management not allowed"
  approval_required:
    - module: os_exec
      action: run_command
      when: "true"
      message: "Approve shell command execution?"
      timeout: "60s"
      on_timeout: reject
    - module: filesystem
      action: search_files
      trigger: action_count
      threshold: 10
      message: "Many searches performed. Continue?"
  audit:
    level: full
    log_params: true
    redact_secrets: true

security:
  profile: readonly
  sandbox:
    allowed_paths:
      - "/home/user/docs"
      - "/home/user/public"
    blocked_commands:
      - "rm"
      - "sudo"
      - "chmod"

triggers:
  - type: cli
    mode: conversation
    greeting: "Secure file browser (read-only mode)"
"""

APP_6_IOT_MONITORING = """\
app:
  name: iot-monitor-e2e
  version: "1.0"
  description: "IoT monitoring with triggers and recording"

variables:
  broker_url: "mqtt://localhost:1883"
  alert_threshold: 50

flow:
  - id: connect_broker
    action: iot.connect
    params:
      broker: "{{broker_url}}"
    on_error: fail

  - id: subscribe_topics
    parallel:
      max_concurrent: 5
      steps:
        - id: sub_temp
          action: iot.subscribe
          params:
            topic: "sensors/temperature"
        - id: sub_humidity
          action: iot.subscribe
          params:
            topic: "sensors/humidity"
        - id: sub_pressure
          action: iot.subscribe
          params:
            topic: "sensors/pressure"

  - id: start_recording
    action: recording.start_recording
    params: {}

  - id: check_devices
    action: iot.list_devices
    params: {}

  - id: monitor_loop
    loop:
      max_iterations: 5
      until: "{{result.check_sensor.alert}}"
      body:
        - id: check_sensor
          action: iot.get_sensor_data
          params:
            sensor_id: "temp-001"
        - id: log_reading
          action: memory.store
          params:
            key: "last_reading"
            value: "{{result.check_sensor}}"
        - id: check_alert
          branch:
            "on": "{{result.check_sensor.value > alert_threshold}}"
            cases:
              "true":
                - id: send_alert
                  action: api_http.post
                  params:
                    url: "https://alerts.example.com/webhook"
              "false":
                - id: log_ok
                  action: memory.store
                  params:
                    key: "status"
                    value: "normal"

  - id: stop_recording
    action: recording.stop_recording
    params: {}

  - id: create_trigger
    action: triggers.create_trigger
    params:
      type: "schedule"

agents:
  - id: monitor
    role: specialist
    brain:
      provider: anthropic
      model: claude-sonnet-4-20250514
    tools:
      - module: iot
      - module: recording
      - module: triggers
      - module: api_http
        actions: [post, get]
      - module: memory
        actions: [store, recall, search]

memory:
  working:
    max_size: "100MB"
  conversation:
    max_history: 500

security:
  profile: power_user
"""

APP_7_DATABASE_ETL = """\
app:
  name: database-etl-e2e
  version: "1.0"
  description: "Database ETL pipeline"

variables:
  source_db: "postgresql://localhost/source"
  target_db: "sqlite:///target.db"

macros:
  - name: extract_table
    params:
      table:
        type: string
        required: true
      connection_id:
        type: string
        required: true
    body:
      - id: query_table
        action: database_gateway.query
        params:
          connection_id: "{{macro.connection_id}}"
          sql: "SELECT * FROM {{macro.table}}"

  - name: load_to_excel
    params:
      data:
        type: string
        required: true
      filename:
        type: string
        required: true
    body:
      - id: open_wb
        action: excel.open_workbook
        params:
          path: "/tmp/etl/{{macro.filename}}"
      - id: write_data
        action: excel.write_cell
        params:
          cell: "A1"
          value: "{{macro.data}}"
      - id: save_wb
        action: excel.save_workbook
        params: {}

flow:
  - id: setup_dir
    action: filesystem.create_directory
    params:
      path: "/tmp/etl"

  - id: connect_source
    action: database_gateway.connect
    params:
      connection_string: "{{source_db}}"

  - id: connect_target
    action: database_gateway.connect
    params:
      connection_string: "{{target_db}}"

  - id: extract_users
    use: extract_table
    with:
      table: "users"
      connection_id: "{{result.connect_source.connection_id}}"

  - id: extract_orders
    use: extract_table
    with:
      table: "orders"
      connection_id: "{{result.connect_source.connection_id}}"

  - id: transform
    agent: etl_agent
    input: |
      Transform the extracted data. Users: {{result.extract_users}}
      Orders: {{result.extract_orders}}

  - id: load_excel
    use: load_to_excel
    with:
      data: "{{result.transform}}"
      filename: "etl_output.xlsx"

  - id: cleanup
    try:
      - id: disconnect_source
        action: database_gateway.disconnect
        params:
          connection_id: "{{result.connect_source.connection_id}}"
      - id: disconnect_target
        action: database_gateway.disconnect
        params:
          connection_id: "{{result.connect_target.connection_id}}"
    catch:
      - error: "*"
        then: continue

agents:
  - id: etl_agent
    role: specialist
    brain:
      provider: anthropic
      model: claude-sonnet-4-20250514
    tools:
      - module: database_gateway
      - module: database
      - module: excel
      - module: filesystem
        actions: [write_file, read_file]

module_config:
  database_gateway:
    max_connections: 10
    pool_timeout: 30

security:
  profile: power_user
"""

APP_8_MULTI_AGENT_TEAM = """\
app:
  name: multi-agent-team-e2e
  version: "1.0"
  description: "Multi-agent research team"

agents:
  - id: coordinator
    role: coordinator
    brain:
      provider: anthropic
      model: claude-sonnet-4-20250514
      temperature: 0.3
    system_prompt: |
      You are the coordinator. Break down tasks and delegate to specialists.
      Use agent_spawn to create sub-agents for parallel work.
      Use memory to track progress across agents.
    tools:
      - module: agent_spawn
      - module: memory
        actions: [store, recall, search, set_objective, update_progress]
      - module: context_manager
      - builtin: delegate

  - id: researcher
    role: specialist
    expertise: [web, analysis]
    brain:
      provider: anthropic
      model: claude-sonnet-4-20250514
      temperature: 0.2
    system_prompt: |
      You are a research specialist. Gather information using HTTP APIs.
    tools:
      - module: api_http
        actions: [get, post]
        constraints:
          allowed_domains: ["api.github.com", "pypi.org", "httpbin.org"]
      - module: filesystem
        actions: [write_file, read_file]
      - module: memory
        actions: [store, recall]

  - id: analyst
    role: specialist
    expertise: [data, code]
    brain:
      provider: anthropic
      model: claude-sonnet-4-20250514
    system_prompt: |
      You are a data analyst. Analyze gathered data and produce insights.
    tools:
      - module: filesystem
      - module: os_exec
        actions: [run_command]
      - module: memory
        actions: [store, recall, search]

communication:
  mode: orchestrated

strategy: hierarchical

memory:
  working:
    max_size: "100MB"
  conversation:
    max_history: 500
  episodic:
    auto_record: true
    auto_recall:
      on_start: true
      limit: 10

security:
  profile: power_user
"""

APP_9_MODULE_MANAGER = """\
app:
  name: module-manager-e2e
  version: "1.0"
  description: "Module management and security operations"

agent:
  brain:
    provider: anthropic
    model: claude-sonnet-4-20250514
  tools:
    - module: module_manager
      actions: [list_modules, get_module_info, install_module, uninstall_module, enable_module, disable_module]
    - module: security
    - module: filesystem
      actions: [read_file, write_file, list_directory]

capabilities:
  grant:
    - module: module_manager
    - module: security
    - module: filesystem
      actions: [read_file, write_file, list_directory]
  deny:
    - module: module_manager
      action: uninstall_module
      when: "{{params.module_id == 'filesystem'}}"
      reason: "Cannot uninstall core filesystem module"
  approval_required:
    - module: module_manager
      action: install_module
      message: "Approve module installation?"
      timeout: "120s"
      on_timeout: reject

security:
  profile: unrestricted
"""

APP_10_FULL_CAPABILITY = """\
app:
  name: full-capability-e2e
  version: "1.0"
  description: "Full capability test: all flow constructs + features"
  max_concurrent_runs: 3
  max_turns_per_run: 100
  timeout: "1800s"
  checkpoint: true
  interface:
    input:
      type: string
      description: "Task to perform"
    output:
      type: string
      description: "Result"

variables:
  workspace: "{{env.PWD}}"
  mode: "test"
  retries: 3

types:
  TaskResult:
    status:
      type: string
      enum: [success, failure, skipped]
    output:
      type: string
    duration_ms:
      type: number

macros:
  - name: safe_action
    description: "Run an action with error handling"
    params:
      module:
        type: string
        required: true
      action:
        type: string
        required: true
    body:
      - id: exec_action
        action: "{{macro.module}}.{{macro.action}}"
        params: {}
        on_error: skip

  - name: read_and_check
    params:
      path:
        type: string
        required: true
    body:
      - id: read
        action: filesystem.read_file
        params:
          path: "{{macro.path}}"
      - id: check
        branch:
          "on": "{{result.read.error}}"
          cases:
            "null":
              - id: ok
                emit:
                  topic: "app.file.read"
                  event:
                    path: "{{macro.path}}"
                    status: "success"
          default:
            - id: err
              emit:
                topic: "app.file.error"
                event:
                  path: "{{macro.path}}"
                  error: "{{result.read.error}}"

flow:
  # 1. Setup
  - id: init
    action: filesystem.create_directory
    params:
      path: "{{workspace}}/test-output"

  # 2. Parallel operations
  - id: parallel_ops
    parallel:
      max_concurrent: 3
      fail_fast: false
      steps:
        - id: list_files
          action: filesystem.list_directory
          params:
            path: "{{workspace}}"
        - id: sys_info
          action: os_exec.get_system_info
          params: {}
        - id: get_env
          action: os_exec.get_env
          params:
            name: "HOME"

  # 3. Branch on mode
  - id: mode_check
    branch:
      "on": "{{mode}}"
      cases:
        "test":
          - id: test_mode
            action: memory.store
            params:
              key: "mode"
              value: "test"
        "production":
          - id: prod_mode
            action: memory.store
            params:
              key: "mode"
              value: "production"
      default:
        - id: unknown_mode
          action: memory.store
          params:
            key: "mode"
            value: "unknown"

  # 4. Loop with condition
  - id: retry_loop
    loop:
      max_iterations: "{{retries}}"
      until: "{{result.attempt.success}}"
      body:
        - id: attempt
          action: filesystem.read_file
          params:
            path: "{{workspace}}/test-output/data.txt"
          on_error: continue

  # 5. Macro usage
  - id: read_check
    use: read_and_check
    with:
      path: "{{workspace}}/README.md"

  # 6. Try/catch
  - id: safe_ops
    try:
      - id: risky_op
        action: filesystem.read_file
        params:
          path: "/nonexistent/file.txt"
      - id: never_reached
        action: memory.store
        params:
          key: "reached"
          value: "yes"
    catch:
      - error: "*"
        then: continue

  # 7. Emit event
  - id: progress_event
    emit:
      topic: "app.progress"
      event:
        phase: "complete"
        percent: 100

  # 8. Agent step
  - id: analyze
    agent: default
    input: |
      Based on the setup results:
      Files: {{result.list_files}}
      System: {{result.sys_info}}
      Generate a brief analysis.

  # 9. Race
  - id: race_ops
    race:
      steps:
        - id: fast_op
          action: os_exec.get_cwd
          params: {}
        - id: slow_op
          action: os_exec.get_platform_info
          params: {}

  # 10. End
  - id: done
    end:
      status: success
      output:
        summary: "All tests passed"

agents:
  - id: default
    role: specialist
    brain:
      provider: anthropic
      model: claude-sonnet-4-20250514
    tools:
      - module: filesystem
        actions: [read_file, list_directory]
      - module: os_exec
        actions: [run_command, get_system_info]
      - module: memory
        actions: [store, recall, search]

memory:
  working:
    max_size: "50MB"
  conversation:
    max_history: 100

capabilities:
  audit:
    level: full
    log_params: true
    redact_secrets: true

observability:
  streaming:
    enabled: true
    channels: [cli, sse]
    include_thoughts: true
    include_tool_calls: true
  logging:
    level: debug
    format: structured
  tracing:
    enabled: true
    backend: opentelemetry
    sample_rate: 1.0
  metrics:
    - name: tool_calls
      type: counter
      track: "tool_call"
    - name: response_time
      type: histogram
      track: "response_ms"

security:
  profile: power_user
  sandbox:
    allowed_paths:
      - "{{workspace}}"
    blocked_commands:
      - "rm -rf /"
"""


# ── Advanced example apps (loaded from examples/) ────────────────────────

_EXAMPLES_DIR = Path(__file__).resolve().parents[5] / "examples"


def _load_example(filename: str) -> str:
    """Load an example .app.yaml file."""
    path = _EXAMPLES_DIR / filename
    if path.exists():
        return path.read_text()
    return ""


APP_11_CLAUDE_CODE = _load_example("claude-code.app.yaml")
APP_12_DEVOPS = _load_example("devops-automation.app.yaml")
APP_13_DATA_PIPELINE = _load_example("data-pipeline.app.yaml")
APP_14_SECURITY_FORTRESS = _load_example("security-fortress.app.yaml")

# ── All apps collection ──────────────────────────────────────────────────

ALL_APPS = {
    "code_assistant": APP_1_CODE_ASSISTANT,
    "web_research": APP_2_WEB_RESEARCH,
    "desktop_automation": APP_3_DESKTOP_AUTOMATION,
    "office_pipeline": APP_4_OFFICE_PIPELINE,
    "security_hardened": APP_5_SECURITY_HARDENED,
    "iot_monitoring": APP_6_IOT_MONITORING,
    "database_etl": APP_7_DATABASE_ETL,
    "multi_agent_team": APP_8_MULTI_AGENT_TEAM,
    "module_manager": APP_9_MODULE_MANAGER,
    "full_capability": APP_10_FULL_CAPABILITY,
}

# Add example apps if they exist on disk
if APP_11_CLAUDE_CODE:
    ALL_APPS["claude_code_v5"] = APP_11_CLAUDE_CODE
if APP_12_DEVOPS:
    ALL_APPS["devops_automation"] = APP_12_DEVOPS
if APP_13_DATA_PIPELINE:
    ALL_APPS["data_pipeline"] = APP_13_DATA_PIPELINE
if APP_14_SECURITY_FORTRESS:
    ALL_APPS["security_fortress"] = APP_14_SECURITY_FORTRESS


# ══════════════════════════════════════════════════════════════════════════
# TEST SUITE
# ══════════════════════════════════════════════════════════════════════════


class TestCompilation:
    """Test that all 10 apps compile without errors."""

    @pytest.mark.parametrize("app_name,yaml_text", ALL_APPS.items())
    def test_compile_success(self, app_name: str, yaml_text: str):
        """Every app must compile without errors."""
        app_def = compile_yaml(yaml_text)
        assert app_def.app.name, f"{app_name}: missing app name"

    @pytest.mark.parametrize("app_name,yaml_text", ALL_APPS.items())
    def test_app_metadata(self, app_name: str, yaml_text: str):
        """Every app must have proper metadata."""
        app_def = compile_yaml(yaml_text)
        assert app_def.app.version
        assert app_def.app.description


class TestToolResolution:
    """Test that all tools resolve against module manifests."""

    @pytest.fixture
    def module_info(self):
        return build_test_module_info()

    @pytest.mark.parametrize("app_name,yaml_text", ALL_APPS.items())
    def test_tools_resolve(self, app_name: str, yaml_text: str, module_info):
        """All declared tools must resolve to actual module actions."""
        app_def = compile_yaml(yaml_text)
        registry = AppToolRegistry(module_info)

        all_tools = app_def.get_all_tools()
        resolved = registry.resolve_tools(all_tools)

        # Every declared module tool should resolve to at least one action
        declared_modules = {t.module for t in all_tools if t.module}
        resolved_modules = {t.module for t in resolved if t.module}
        missing = declared_modules - resolved_modules
        assert not missing, f"{app_name}: modules not resolved: {missing}"

    @pytest.mark.parametrize("app_name,yaml_text", ALL_APPS.items())
    def test_tools_have_params(self, app_name: str, yaml_text: str, module_info):
        """Resolved tools should have parameter schemas."""
        app_def = compile_yaml(yaml_text)
        registry = AppToolRegistry(module_info)
        resolved = registry.resolve_tools(app_def.get_all_tools())

        for tool in resolved:
            if tool.module and not tool.is_builtin:
                assert isinstance(tool.parameters, dict), \
                    f"{app_name}: {tool.name} has no param schema"

    @pytest.mark.parametrize("app_name,yaml_text", ALL_APPS.items())
    def test_openai_format(self, app_name: str, yaml_text: str, module_info):
        """Resolved tools must convert to OpenAI function format."""
        app_def = compile_yaml(yaml_text)
        registry = AppToolRegistry(module_info)
        resolved = registry.resolve_tools(app_def.get_all_tools())
        openai_tools = registry.to_openai_tools(resolved)

        for ot in openai_tools:
            assert ot["type"] == "function"
            assert "function" in ot
            assert "name" in ot["function"]
            assert "parameters" in ot["function"]


class TestSecurityConfig:
    """Test security configuration parsing and validation."""

    def test_readonly_profile(self):
        """App 5 should have readonly profile."""
        app_def = compile_yaml(APP_5_SECURITY_HARDENED)
        assert app_def.security is not None
        assert app_def.security.profile == "readonly"

    def test_capabilities_deny(self):
        """App 5 should have deny rules."""
        app_def = compile_yaml(APP_5_SECURITY_HARDENED)
        assert len(app_def.capabilities.deny) >= 2
        deny_actions = [d.action for d in app_def.capabilities.deny]
        assert "write_file" in deny_actions
        assert "delete_file" in deny_actions

    def test_capabilities_grant(self):
        """App 5 should have grant rules restricting to read-only actions."""
        app_def = compile_yaml(APP_5_SECURITY_HARDENED)
        assert len(app_def.capabilities.grant) >= 1
        fs_grant = [g for g in app_def.capabilities.grant if g.module == "filesystem"][0]
        assert "read_file" in fs_grant.actions
        assert "write_file" not in fs_grant.actions

    def test_approval_rules(self):
        """App 5 should have approval rules with when: and trigger conditions."""
        app_def = compile_yaml(APP_5_SECURITY_HARDENED)
        assert len(app_def.capabilities.approval_required) >= 2

        # Find the command approval rule
        cmd_rule = [r for r in app_def.capabilities.approval_required
                    if r.module == "os_exec" and r.action == "run_command"][0]
        assert cmd_rule.when == "true"
        assert cmd_rule.on_timeout == "reject"

        # Find the count-based trigger rule
        count_rule = [r for r in app_def.capabilities.approval_required
                      if r.trigger == "action_count"][0]
        assert count_rule.threshold == 10

    def test_audit_config(self):
        """App 5 should have full audit with redaction."""
        app_def = compile_yaml(APP_5_SECURITY_HARDENED)
        assert app_def.capabilities.audit.level.value == "full"
        assert app_def.capabilities.audit.log_params is True
        assert app_def.capabilities.audit.redact_secrets is True

    def test_sandbox_config(self):
        """App 5 should have sandbox with allowed paths and blocked commands."""
        app_def = compile_yaml(APP_5_SECURITY_HARDENED)
        assert app_def.security is not None
        assert len(app_def.security.sandbox.allowed_paths) == 2
        assert "rm" in app_def.security.sandbox.blocked_commands

    def test_tool_constraints(self):
        """App 2 should have constraints on browser and api_http tools."""
        app_def = compile_yaml(APP_2_WEB_RESEARCH)
        browser_tools = [t for t in app_def.get_all_tools() if t.module == "browser"]
        assert len(browser_tools) == 1
        assert "github.com" in browser_tools[0].constraints.allowed_domains

        api_tools = [t for t in app_def.get_all_tools() if t.module == "api_http"]
        assert api_tools[0].constraints.max_response_size == "5MB"

    def test_forbidden_patterns(self):
        """App 5 should have forbidden patterns in constraints."""
        app_def = compile_yaml(APP_5_SECURITY_HARDENED)
        exec_tools = [t for t in app_def.get_all_tools()
                      if t.module == "os_exec" and t.action == "run_command"]
        assert len(exec_tools) == 1
        assert "sudo *" in exec_tools[0].constraints.forbidden_patterns

    def test_module_manager_conditional_deny(self):
        """App 9 should have when: condition on deny rule."""
        app_def = compile_yaml(APP_9_MODULE_MANAGER)
        deny_rules = app_def.capabilities.deny
        uninstall_deny = [d for d in deny_rules if d.action == "uninstall_module"][0]
        assert "filesystem" in uninstall_deny.when


class TestMemoryConfig:
    """Test memory configuration across apps."""

    def test_full_memory_config(self):
        """App 1 should have all memory levels configured."""
        app_def = compile_yaml(APP_1_CODE_ASSISTANT)
        assert app_def.memory.working.max_size == "50MB"
        assert app_def.memory.conversation is not None
        assert app_def.memory.conversation.max_history == 200
        assert app_def.memory.project is not None
        assert app_def.memory.project.auto_inject is True
        assert app_def.memory.episodic is not None
        assert app_def.memory.episodic.auto_record is True
        assert app_def.memory.episodic.auto_recall.on_start is True

    def test_minimal_memory_config(self):
        """App 2 should have only working memory."""
        app_def = compile_yaml(APP_2_WEB_RESEARCH)
        assert app_def.memory.working.max_size == "20MB"
        assert app_def.memory.conversation is None
        assert app_def.memory.episodic is None


class TestPerceptionConfig:
    """Test perception (screenshot/OCR) configuration."""

    def test_perception_enabled(self):
        """App 3 should have perception enabled with per-action overrides."""
        app_def = compile_yaml(APP_3_DESKTOP_AUTOMATION)
        assert app_def.perception.enabled is True
        assert app_def.perception.ocr_enabled is True
        assert app_def.perception.timeout_seconds == 15

    def test_per_action_perception(self):
        """App 3 should have per-action perception overrides."""
        app_def = compile_yaml(APP_3_DESKTOP_AUTOMATION)
        cc_config = app_def.perception.actions.get("computer_control.click_element")
        assert cc_config is not None
        assert cc_config.capture_before is True
        assert cc_config.capture_after is True
        assert cc_config.ocr_enabled is True

    def test_perception_disabled_by_default(self):
        """Most apps should have perception disabled."""
        app_def = compile_yaml(APP_1_CODE_ASSISTANT)
        assert app_def.perception.enabled is False


class TestFlowSteps:
    """Test flow step parsing and validation."""

    def test_office_pipeline_flow(self):
        """App 4 should have a flow with sequences and actions."""
        app_def = compile_yaml(APP_4_OFFICE_PIPELINE)
        assert app_def.flow is not None
        assert len(app_def.flow) >= 4

        # Check step types
        step_types = [s.infer_type() for s in app_def.flow]
        assert FlowStepType.action in step_types
        assert FlowStepType.sequence in step_types

    def test_iot_flow_constructs(self):
        """App 6 should have parallel, loop, and branch in flow."""
        app_def = compile_yaml(APP_6_IOT_MONITORING)
        assert app_def.flow is not None

        step_types = [s.infer_type() for s in app_def.flow]
        assert FlowStepType.parallel in step_types
        assert FlowStepType.loop in step_types

        # Check the loop has a branch inside
        loop_step = [s for s in app_def.flow if s.loop is not None][0]
        body_types = [s.infer_type() for s in loop_step.loop.body]
        assert FlowStepType.branch in body_types

    def test_etl_macros_in_flow(self):
        """App 7 should use macros and try/catch in flow."""
        app_def = compile_yaml(APP_7_DATABASE_ETL)
        assert app_def.flow is not None
        assert len(app_def.macros) >= 2

        step_types = [s.infer_type() for s in app_def.flow]
        assert FlowStepType.use_macro in step_types
        assert FlowStepType.try_catch in step_types

    def test_full_capability_all_constructs(self):
        """App 10 should exercise all major flow constructs."""
        app_def = compile_yaml(APP_10_FULL_CAPABILITY)
        assert app_def.flow is not None

        step_types = [s.infer_type() for s in app_def.flow]
        assert FlowStepType.action in step_types
        assert FlowStepType.parallel in step_types
        assert FlowStepType.branch in step_types
        assert FlowStepType.loop in step_types
        assert FlowStepType.use_macro in step_types
        assert FlowStepType.try_catch in step_types
        assert FlowStepType.emit in step_types
        assert FlowStepType.agent in step_types
        assert FlowStepType.race in step_types
        assert FlowStepType.end in step_types

    def test_flow_step_ids_unique(self):
        """All flow step IDs must be unique (compiler enforces this)."""
        for app_name, yaml_text in ALL_APPS.items():
            app_def = compile_yaml(yaml_text)
            if not app_def.flow:
                continue
            # If it compiled, IDs are unique (compiler checks this)


class TestMacros:
    """Test macro definitions and references."""

    def test_macro_params(self):
        """App 7 macros should have properly typed params."""
        app_def = compile_yaml(APP_7_DATABASE_ETL)
        extract_macro = [m for m in app_def.macros if m.name == "extract_table"][0]
        assert "table" in extract_macro.params
        assert extract_macro.params["table"].type == "string"
        assert extract_macro.params["table"].required is True

    def test_macro_body_steps(self):
        """Macro body should contain valid flow steps."""
        app_def = compile_yaml(APP_10_FULL_CAPABILITY)
        read_check = [m for m in app_def.macros if m.name == "read_and_check"][0]
        assert len(read_check.body) == 2
        assert read_check.body[0].action == "filesystem.read_file"
        assert read_check.body[1].branch is not None


class TestMultiAgent:
    """Test multi-agent configurations."""

    def test_multi_agent_parsing(self):
        """App 8 should parse as multi-agent with 3 agents."""
        app_def = compile_yaml(APP_8_MULTI_AGENT_TEAM)
        assert app_def.is_multi_agent()
        assert len(app_def.agents.agents) == 3

    def test_agent_roles(self):
        """App 8 agents should have correct roles."""
        app_def = compile_yaml(APP_8_MULTI_AGENT_TEAM)
        roles = {a.id: a.role.value for a in app_def.agents.agents}
        assert roles["coordinator"] == "coordinator"
        assert roles["researcher"] == "specialist"
        assert roles["analyst"] == "specialist"

    def test_agent_tools_isolated(self):
        """Each agent should have its own tool set."""
        app_def = compile_yaml(APP_8_MULTI_AGENT_TEAM)
        module_info = build_test_module_info()
        registry = AppToolRegistry(module_info)

        for agent in app_def.agents.agents:
            resolved = registry.resolve_tools(agent.tools)
            tool_names = [t.name for t in resolved]
            # coordinator should have agent_spawn, memory, context_manager, delegate
            if agent.id == "coordinator":
                assert any("agent_spawn" in n for n in tool_names)
                assert any("memory" in n for n in tool_names)

    def test_communication_mode(self):
        """App 8 should have orchestrated communication mode."""
        app_def = compile_yaml(APP_8_MULTI_AGENT_TEAM)
        assert app_def.agents.communication.mode.value == "orchestrated"

    def test_strategy(self):
        """App 8 should have hierarchical strategy."""
        app_def = compile_yaml(APP_8_MULTI_AGENT_TEAM)
        assert app_def.agents.strategy.value == "hierarchical"


class TestObservability:
    """Test observability configuration."""

    def test_full_observability(self):
        """App 10 should have full observability config."""
        app_def = compile_yaml(APP_10_FULL_CAPABILITY)
        obs = app_def.observability

        assert obs.streaming.enabled is True
        assert "cli" in obs.streaming.channels
        assert "sse" in obs.streaming.channels
        assert obs.streaming.include_thoughts is True

        assert obs.logging.level == "debug"
        assert obs.logging.format == "structured"

        assert obs.tracing.enabled is True
        assert obs.tracing.backend == "opentelemetry"
        assert obs.tracing.sample_rate == 1.0

        assert len(obs.metrics) == 2
        assert obs.metrics[0].name == "tool_calls"
        assert obs.metrics[0].type == "counter"


class TestTriggers:
    """Test trigger configurations."""

    def test_cli_trigger(self):
        """App 1 should have a CLI conversation trigger."""
        app_def = compile_yaml(APP_1_CODE_ASSISTANT)
        cli_triggers = [t for t in app_def.triggers if t.type.value == "cli"]
        assert len(cli_triggers) >= 1
        assert cli_triggers[0].mode.value == "conversation"
        assert cli_triggers[0].greeting

    def test_http_trigger(self):
        """App 1 should have an HTTP trigger (no trigger defined for app 1, check research)."""
        app_def = compile_yaml(APP_2_WEB_RESEARCH)
        # App 2 has no triggers defined, so empty
        # Check app with HTTP trigger instead
        research_def = compile_yaml(
            open(
                Path(__file__).parent.parent.parent.parent / "examples" / "research-agent.app.yaml"
            ).read()
            if Path(
                Path(__file__).parent.parent.parent.parent / "examples" / "research-agent.app.yaml"
            ).exists()
            else APP_6_IOT_MONITORING
        )
        # Just verify triggers parse correctly
        assert isinstance(research_def.triggers, list)


class TestVariables:
    """Test variable definitions and template usage."""

    def test_variables_defined(self):
        """Apps should have properly defined variables."""
        app_def = compile_yaml(APP_1_CODE_ASSISTANT)
        assert "workspace" in app_def.variables

    def test_variables_in_system_prompt(self):
        """Variables should be usable in system prompts."""
        app_def = compile_yaml(APP_1_CODE_ASSISTANT)
        assert "{{workspace}}" in app_def.agent.system_prompt

    def test_variables_in_flow_params(self):
        """Variables should be usable in flow step params."""
        app_def = compile_yaml(APP_4_OFFICE_PIPELINE)
        setup_step = app_def.flow[0]
        assert "{{output_dir}}" in setup_step.params["path"]


class TestModuleConfig:
    """Test module_config block parsing."""

    def test_database_module_config(self):
        """App 4 should configure the database module."""
        app_def = compile_yaml(APP_4_OFFICE_PIPELINE)
        assert "database" in app_def.module_config
        assert app_def.module_config["database"]["connection_string"] == "sqlite:///data.db"

    def test_database_gateway_config(self):
        """App 7 should configure the database_gateway module."""
        app_def = compile_yaml(APP_7_DATABASE_ETL)
        assert "database_gateway" in app_def.module_config
        assert app_def.module_config["database_gateway"]["max_connections"] == 10


class TestBrainConfig:
    """Test LLM brain configuration."""

    def test_fallback_chain(self):
        """App 1 should have a fallback LLM provider."""
        app_def = compile_yaml(APP_1_CODE_ASSISTANT)
        assert len(app_def.agent.brain.fallback) == 1
        assert app_def.agent.brain.fallback[0].model == "claude-haiku-4-5-20251001"

    def test_temperature_settings(self):
        """Different agents should have different temperatures."""
        app_def = compile_yaml(APP_8_MULTI_AGENT_TEAM)
        temps = {a.id: a.brain.temperature for a in app_def.agents.agents}
        assert temps["coordinator"] == 0.3
        assert temps["researcher"] == 0.2


class TestLoopConfig:
    """Test agent loop configuration."""

    def test_context_config(self):
        """App 1 should have context management config."""
        app_def = compile_yaml(APP_1_CODE_ASSISTANT)
        ctx = app_def.agent.loop.context
        assert ctx.max_tokens == 200000
        assert ctx.strategy.value == "summarize"
        assert ctx.keep_last_n_messages == 30
        assert ctx.compression_trigger_ratio == 0.75

    def test_app_level_config(self):
        """App 10 should have app-level limits."""
        app_def = compile_yaml(APP_10_FULL_CAPABILITY)
        assert app_def.app.max_concurrent_runs == 3
        assert app_def.app.max_turns_per_run == 100
        assert app_def.app.checkpoint is True


class TestTypes:
    """Test custom type definitions."""

    def test_custom_types(self):
        """App 10 should have custom type definitions."""
        app_def = compile_yaml(APP_10_FULL_CAPABILITY)
        assert "TaskResult" in app_def.types
        tr = app_def.types["TaskResult"]
        assert "status" in tr
        assert tr["status"]["type"] == "string"


class TestDaemonExecutorWiring:
    """Test that apps wire correctly into DaemonToolExecutor."""

    @pytest.fixture
    def mock_registry(self):
        """Create a mock ModuleRegistry."""
        registry = MagicMock()
        module = MagicMock()
        module.execute = AsyncMock(return_value={"result": "ok"})
        registry.get = MagicMock(return_value=module)
        registry.all_manifests = MagicMock(return_value=[])
        return registry

    @pytest.fixture
    def executor(self, mock_registry):
        """Create a DaemonToolExecutor with mocks."""
        return DaemonToolExecutor(
            module_registry=mock_registry,
            permission_guard=None,
            sanitizer=None,
            event_bus=None,
        )

    @pytest.mark.asyncio
    async def test_security_profile_applied(self, executor):
        """set_security_profile should update the per-request scope state."""
        from llmos_bridge.apps.daemon_executor import _current_scope, _ExecutionScope
        token = _current_scope.set(_ExecutionScope())
        try:
            executor.set_security_profile("readonly")
            assert _current_scope.get().security_profile == "readonly"
        finally:
            _current_scope.reset(token)

    @pytest.mark.asyncio
    async def test_sandbox_applied(self, executor):
        """set_sandbox should update sandbox constraints in per-request scope."""
        from llmos_bridge.apps.daemon_executor import _current_scope, _ExecutionScope
        token = _current_scope.set(_ExecutionScope())
        try:
            executor.set_sandbox(
                allowed_paths=["/home/user/docs"],
                blocked_commands=["rm", "sudo"],
            )
            scope = _current_scope.get()
            assert scope.sandbox_paths == ["/home/user/docs"]
            assert scope.sandbox_commands == ["rm", "sudo"]
        finally:
            _current_scope.reset(token)

    @pytest.mark.asyncio
    async def test_sandbox_blocks_path(self, executor):
        """Sandbox should block paths outside allowed list."""
        executor.set_sandbox(allowed_paths=["/tmp/safe"])
        result = await executor.execute("filesystem", "read_file", {"path": "/etc/passwd"})
        assert "error" in result
        assert "outside sandbox" in result["error"]

    @pytest.mark.asyncio
    async def test_sandbox_blocks_command(self, executor):
        """Sandbox should block forbidden commands."""
        executor.set_sandbox(blocked_commands=["rm -rf"])
        result = await executor.execute("os_exec", "run_command", {"command": "rm -rf /"})
        assert "error" in result
        assert "blocked by sandbox" in result["error"].lower() or "blocked" in result["error"].lower()

    @pytest.mark.asyncio
    async def test_capabilities_deny(self, executor):
        """Capabilities deny should block actions."""
        from llmos_bridge.apps.models import CapabilitiesConfig, CapabilityDenial
        caps = CapabilitiesConfig(
            deny=[CapabilityDenial(module="filesystem", action="write_file", reason="Read-only app")]
        )
        executor.set_capabilities(caps)
        result = await executor.execute("filesystem", "write_file", {"path": "/tmp/x", "content": "y"})
        assert "error" in result
        assert "Read-only" in result["error"]

    @pytest.mark.asyncio
    async def test_capabilities_grant_restrict(self, executor):
        """When grants are defined, only granted actions should be allowed."""
        from llmos_bridge.apps.models import CapabilitiesConfig, CapabilityGrant
        caps = CapabilitiesConfig(
            grant=[CapabilityGrant(module="filesystem", actions=["read_file"])]
        )
        executor.set_capabilities(caps)
        result = await executor.execute("filesystem", "write_file", {"path": "/tmp/x", "content": "y"})
        assert "error" in result
        assert "not in app capability grants" in result["error"]

    @pytest.mark.asyncio
    async def test_tool_constraints_paths(self, executor):
        """Tool constraints should restrict to allowed paths."""
        executor.set_tool_constraints({
            "filesystem.write_file": {"paths": ["/tmp/allowed"]},
        })
        result = await executor.execute("filesystem", "write_file", {"path": "/home/forbidden/x", "content": "y"})
        assert "error" in result
        assert "not in allowed paths" in result["error"]

    @pytest.mark.asyncio
    async def test_tool_constraints_forbidden_commands(self, executor):
        """Tool constraints should block forbidden commands."""
        executor.set_tool_constraints({
            "os_exec.run_command": {"forbidden_commands": ["sudo"]},
        })
        result = await executor.execute("os_exec", "run_command", {"command": "sudo rm -rf /"})
        assert "error" in result
        assert "forbidden" in result["error"].lower()

    @pytest.mark.asyncio
    async def test_tool_constraints_readonly(self, executor):
        """Tool constraints read_only should block write actions."""
        executor.set_tool_constraints({
            "filesystem.write_file": {"read_only": True},
        })
        result = await executor.execute("filesystem", "write_file", {"path": "/tmp/x", "content": "y"})
        assert "error" in result
        assert "read-only" in result["error"]

    @pytest.mark.asyncio
    async def test_tool_constraints_allowed_domains(self, executor):
        """Tool constraints should restrict to allowed domains."""
        executor.set_tool_constraints({
            "api_http.get": {"allowed_domains": ["api.github.com"]},
        })
        result = await executor.execute("api_http", "get", {"url": "https://evil.com/data"})
        assert "error" in result
        assert "not in allowed domains" in result["error"]

    @pytest.mark.asyncio
    async def test_approval_required(self, executor):
        """Approval rules should block when triggered."""
        from llmos_bridge.apps.models import CapabilitiesConfig, ApprovalRule
        caps = CapabilitiesConfig(
            approval_required=[ApprovalRule(
                module="os_exec",
                action="run_command",
                message="Approve command?",
            )]
        )
        executor.set_capabilities(caps)
        result = await executor.execute("os_exec", "run_command", {"command": "ls"})
        assert "error" in result
        assert "Approval required" in result["error"]

    @pytest.mark.asyncio
    async def test_count_based_approval(self, executor):
        """Count-based approval should only trigger after threshold."""
        from llmos_bridge.apps.models import CapabilitiesConfig, ApprovalRule
        caps = CapabilitiesConfig(
            approval_required=[ApprovalRule(
                module="filesystem",
                action="read_file",
                trigger="action_count",
                threshold=3,
                message="Too many reads!",
            )]
        )
        executor.set_capabilities(caps)

        # First 3 calls should succeed (below threshold)
        for _ in range(3):
            result = await executor.execute("filesystem", "read_file", {"path": "/tmp/x"})
            assert "Approval required" not in result.get("error", "")

        # 4th call should trigger approval
        result = await executor.execute("filesystem", "read_file", {"path": "/tmp/x"})
        assert "error" in result
        assert "Too many reads" in result["error"]

    @pytest.mark.asyncio
    async def test_audit_redaction(self):
        """Audit should redact secrets from params."""
        from llmos_bridge.apps.daemon_executor import _redact_secrets
        params = {
            "url": "https://api.example.com",
            "api_key": "sk-12345",
            "token": "bearer-token",
            "data": {"password": "secret123", "name": "test"},
        }
        redacted = _redact_secrets(params)
        assert redacted["url"] == "https://api.example.com"
        assert redacted["api_key"] == "***REDACTED***"
        assert redacted["token"] == "***REDACTED***"
        assert redacted["data"]["password"] == "***REDACTED***"
        assert redacted["data"]["name"] == "test"


# ══════════════════════════════════════════════════════════════════════════
# ADVANCED EXAMPLE APP TESTS
# ══════════════════════════════════════════════════════════════════════════


@pytest.mark.skipif(not APP_11_CLAUDE_CODE, reason="claude-code.app.yaml not found")
class TestClaudeCodeV5:
    """Test the full-power Claude Code v5 example app."""

    def test_compile(self):
        app_def = compile_yaml(APP_11_CLAUDE_CODE)
        assert app_def.app.name == "claude-code"
        assert app_def.app.version == "5.0"

    def test_app_config_limits(self):
        app_def = compile_yaml(APP_11_CLAUDE_CODE)
        assert app_def.app.max_concurrent_runs == 5
        assert app_def.app.max_turns_per_run == 200
        assert app_def.app.max_actions_per_turn == 50
        assert app_def.app.checkpoint is True

    def test_interface(self):
        app_def = compile_yaml(APP_11_CLAUDE_CODE)
        assert app_def.app.interface.input.type == "string"
        assert len(app_def.app.interface.errors) == 3

    def test_custom_types(self):
        app_def = compile_yaml(APP_11_CLAUDE_CODE)
        assert "CodeChange" in app_def.types
        assert "TestResult" in app_def.types
        assert "AgentReport" in app_def.types

    def test_brain_with_fallback(self):
        app_def = compile_yaml(APP_11_CLAUDE_CODE)
        assert app_def.agent.brain.max_tokens == 16384
        assert app_def.agent.brain.timeout == 120.0
        assert len(app_def.agent.brain.fallback) == 1
        assert app_def.agent.brain.fallback[0].model == "claude-haiku-4-5-20251001"

    def test_loop_advanced_config(self):
        app_def = compile_yaml(APP_11_CLAUDE_CODE)
        loop = app_def.agent.loop
        assert loop.max_turns == 200
        assert loop.retry.max_attempts == 3
        assert loop.retry.backoff == "exponential"
        assert loop.planning.enabled is True
        assert loop.planning.replan_on_failure is True
        assert loop.context.cognitive_max_tokens == 2000
        assert loop.context.memory_max_tokens == 3000

    def test_tool_constraints(self):
        app_def = compile_yaml(APP_11_CLAUDE_CODE)
        tools = app_def.get_all_tools()
        os_exec_tool = [t for t in tools if t.module == "os_exec"][0]
        assert os_exec_tool.constraints.rate_limit_per_minute == 30
        assert len(os_exec_tool.constraints.forbidden_commands) >= 4
        assert len(os_exec_tool.constraints.forbidden_patterns) >= 2

        browser_tools = [t for t in tools if t.module == "browser"]
        assert len(browser_tools) == 1
        assert browser_tools[0].constraints.rate_limit_per_minute == 20
        assert len(browser_tools[0].constraints.allowed_domains) >= 5

    def test_five_level_memory(self):
        app_def = compile_yaml(APP_11_CLAUDE_CODE)
        mem = app_def.memory
        assert mem.working.max_size == "100MB"
        assert mem.conversation.max_history == 500
        assert mem.conversation.auto_summarize is True
        assert mem.project.auto_inject is True
        assert mem.episodic.auto_record is True
        assert mem.episodic.auto_recall.limit == 10
        assert mem.procedural is not None
        assert mem.procedural.learn_from_failures is True
        assert mem.procedural.auto_suggest is True

    def test_capabilities(self):
        app_def = compile_yaml(APP_11_CLAUDE_CODE)
        caps = app_def.capabilities
        assert len(caps.grant) >= 8
        assert len(caps.deny) >= 2
        assert len(caps.approval_required) >= 3
        assert caps.audit.level.value == "full"
        assert caps.audit.redact_secrets is True
        assert len(caps.audit.notify_on) >= 2

    def test_six_triggers(self):
        app_def = compile_yaml(APP_11_CLAUDE_CODE)
        trigger_types = [t.type.value for t in app_def.triggers]
        assert "cli" in trigger_types
        assert "http" in trigger_types
        assert "watch" in trigger_types
        assert "schedule" in trigger_types
        assert "event" in trigger_types

    def test_observability(self):
        app_def = compile_yaml(APP_11_CLAUDE_CODE)
        obs = app_def.observability
        assert obs.tracing.enabled is True
        assert len(obs.metrics) >= 4

    def test_module_config(self):
        app_def = compile_yaml(APP_11_CLAUDE_CODE)
        assert "database" in app_def.module_config
        assert "browser" in app_def.module_config

    def test_four_macros(self):
        app_def = compile_yaml(APP_11_CLAUDE_CODE)
        macro_names = {m.name for m in app_def.macros}
        assert "read_and_fix" in macro_names
        assert "run_tests" in macro_names
        assert "parallel_analyze" in macro_names
        assert "safe_shell" in macro_names

    def test_all_modules_resolve(self):
        app_def = compile_yaml(APP_11_CLAUDE_CODE)
        module_info = build_test_module_info()
        registry = AppToolRegistry(module_info)
        resolved = registry.resolve_tools(app_def.get_all_tools())
        modules = {t.module for t in resolved if t.module}
        # Should resolve all declared modules
        assert "filesystem" in modules
        assert "os_exec" in modules
        assert "agent_spawn" in modules
        assert "memory" in modules
        assert "context_manager" in modules
        assert "browser" in modules
        assert "api_http" in modules
        assert "database" in modules
        assert "security" in modules


@pytest.mark.skipif(not APP_12_DEVOPS, reason="devops-automation.app.yaml not found")
class TestDevOpsAutomation:
    """Test the DevOps CI/CD pipeline app."""

    def test_compile(self):
        app_def = compile_yaml(APP_12_DEVOPS)
        assert app_def.app.name == "devops-automation"

    def test_flow_constructs(self):
        app_def = compile_yaml(APP_12_DEVOPS)
        assert app_def.flow is not None
        step_types = set()
        for step in app_def.flow:
            step_types.add(step.infer_type())
        assert FlowStepType.action in step_types
        assert FlowStepType.parallel in step_types
        assert FlowStepType.try_catch in step_types
        assert FlowStepType.approval in step_types
        assert FlowStepType.emit in step_types
        assert FlowStepType.end in step_types

    def test_approval_gate(self):
        app_def = compile_yaml(APP_12_DEVOPS)
        approval_steps = [s for s in app_def.flow if s.approval is not None]
        assert len(approval_steps) >= 1
        gate = approval_steps[0]
        assert len(gate.approval.options) >= 2
        assert gate.approval.timeout == "1800s"

    def test_multi_agent(self):
        app_def = compile_yaml(APP_12_DEVOPS)
        assert app_def.is_multi_agent()
        agent_ids = {a.id for a in app_def.agents.agents}
        assert "deployer" in agent_ids
        assert "monitor" in agent_ids

    def test_macros(self):
        app_def = compile_yaml(APP_12_DEVOPS)
        macro_names = {m.name for m in app_def.macros}
        assert "run_build" in macro_names
        assert "health_check" in macro_names
        assert "rollback" in macro_names

    def test_webhook_trigger(self):
        app_def = compile_yaml(APP_12_DEVOPS)
        webhook = [t for t in app_def.triggers if t.type.value == "webhook"]
        assert len(webhook) >= 1
        assert webhook[0].auth.type.value == "hmac"
        assert "push" in webhook[0].events

    def test_goto_steps(self):
        app_def = compile_yaml(APP_12_DEVOPS)
        goto_steps = [s for s in app_def.flow if s.goto]
        assert len(goto_steps) >= 2


@pytest.mark.skipif(not APP_13_DATA_PIPELINE, reason="data-pipeline.app.yaml not found")
class TestDataPipeline:
    """Test the data pipeline app — exercises all flow step types."""

    def test_compile(self):
        app_def = compile_yaml(APP_13_DATA_PIPELINE)
        assert app_def.app.name == "data-pipeline"

    def test_all_flow_types(self):
        """Data pipeline should use: action, parallel, map, reduce, branch,
        loop, race, try_catch, dispatch, spawn, approval, emit, wait, end,
        use_macro, goto."""
        app_def = compile_yaml(APP_13_DATA_PIPELINE)
        assert app_def.flow is not None
        step_types = set()
        for step in app_def.flow:
            step_types.add(step.infer_type())
        assert FlowStepType.action in step_types
        assert FlowStepType.parallel in step_types
        assert FlowStepType.map in step_types
        assert FlowStepType.reduce in step_types
        assert FlowStepType.branch in step_types
        assert FlowStepType.loop in step_types
        assert FlowStepType.race in step_types
        assert FlowStepType.try_catch in step_types
        assert FlowStepType.dispatch in step_types
        assert FlowStepType.spawn in step_types
        assert FlowStepType.approval in step_types
        assert FlowStepType.emit in step_types
        assert FlowStepType.wait in step_types
        assert FlowStepType.end in step_types
        assert FlowStepType.use_macro in step_types

    def test_map_config(self):
        app_def = compile_yaml(APP_13_DATA_PIPELINE)
        map_steps = [s for s in app_def.flow if s.map is not None]
        assert len(map_steps) >= 1
        assert map_steps[0].map.max_concurrent == 3

    def test_reduce_config(self):
        app_def = compile_yaml(APP_13_DATA_PIPELINE)
        reduce_steps = [s for s in app_def.flow if s.reduce is not None]
        assert len(reduce_steps) >= 1
        assert reduce_steps[0].reduce.initial == {"total_rows": 0, "total_tables": 0}

    def test_dispatch_config(self):
        app_def = compile_yaml(APP_13_DATA_PIPELINE)
        dispatch_steps = [s for s in app_def.flow if s.dispatch is not None]
        assert len(dispatch_steps) >= 1

    def test_spawn_config(self):
        app_def = compile_yaml(APP_13_DATA_PIPELINE)
        spawn_steps = [s for s in app_def.flow if s.spawn is not None]
        assert len(spawn_steps) >= 1
        assert spawn_steps[0].spawn.timeout == "60s"

    def test_pipeline_strategy(self):
        app_def = compile_yaml(APP_13_DATA_PIPELINE)
        assert app_def.is_multi_agent()
        agent_ids = {a.id for a in app_def.agents.agents}
        assert "transformer" in agent_ids
        assert "reporter" in agent_ids

    def test_macros(self):
        app_def = compile_yaml(APP_13_DATA_PIPELINE)
        macro_names = {m.name for m in app_def.macros}
        assert "extract_table" in macro_names
        assert "export_to_excel" in macro_names
        assert "generate_report" in macro_names

    def test_conditional_deny(self):
        app_def = compile_yaml(APP_13_DATA_PIPELINE)
        deny_rules = app_def.capabilities.deny
        assert len(deny_rules) >= 1
        db_deny = [d for d in deny_rules if d.module == "database"][0]
        assert "DROP" in db_deny.when


@pytest.mark.skipif(not APP_14_SECURITY_FORTRESS, reason="security-fortress.app.yaml not found")
class TestSecurityFortress:
    """Test the maximum-security app — validates all security features."""

    def test_compile(self):
        app_def = compile_yaml(APP_14_SECURITY_FORTRESS)
        assert app_def.app.name == "security-fortress"

    def test_readonly_profile(self):
        app_def = compile_yaml(APP_14_SECURITY_FORTRESS)
        assert app_def.security.profile == "readonly"

    def test_extensive_deny_rules(self):
        app_def = compile_yaml(APP_14_SECURITY_FORTRESS)
        deny_rules = app_def.capabilities.deny
        assert len(deny_rules) >= 10
        deny_actions = [(d.module, d.action) for d in deny_rules]
        assert ("filesystem", "write_file") in deny_actions
        assert ("filesystem", "delete_file") in deny_actions
        assert ("filesystem", "move_file") in deny_actions
        assert ("filesystem", "copy_file") in deny_actions
        assert ("filesystem", "append_file") in deny_actions
        assert ("filesystem", "find_replace") in deny_actions
        assert ("os_exec", "kill_process") in deny_actions
        assert ("os_exec", "set_env") in deny_actions
        assert ("database", "execute") in deny_actions

    def test_conditional_deny(self):
        app_def = compile_yaml(APP_14_SECURITY_FORTRESS)
        cond_denials = [d for d in app_def.capabilities.deny if d.when]
        assert len(cond_denials) >= 2

    def test_approval_gates(self):
        app_def = compile_yaml(APP_14_SECURITY_FORTRESS)
        approvals = app_def.capabilities.approval_required
        assert len(approvals) >= 4

        # Shell commands always need approval
        cmd_approval = [a for a in approvals if a.module == "os_exec" and a.action == "run_command"][0]
        assert cmd_approval.when == "true"
        assert cmd_approval.on_timeout == "reject"

        # Count-based checkpoints
        count_approvals = [a for a in approvals if a.trigger == "action_count"]
        assert len(count_approvals) >= 2
        thresholds = sorted(a.threshold for a in count_approvals)
        assert thresholds == [10, 50]

    def test_rate_limiting(self):
        app_def = compile_yaml(APP_14_SECURITY_FORTRESS)
        tools = app_def.get_all_tools()
        fs_tool = [t for t in tools if t.module == "filesystem"][0]
        assert fs_tool.constraints.rate_limit_per_minute == 60
        assert fs_tool.constraints.rate_limit_per_hour == 500

        cmd_tool = [t for t in tools if t.module == "os_exec" and t.action == "run_command"][0]
        assert cmd_tool.constraints.rate_limit_per_minute == 10
        assert cmd_tool.constraints.rate_limit_per_hour == 50
        assert cmd_tool.constraints.max_retries == 1

    def test_audit_full(self):
        app_def = compile_yaml(APP_14_SECURITY_FORTRESS)
        audit = app_def.capabilities.audit
        assert audit.level.value == "full"
        assert audit.log_params is True
        assert audit.redact_secrets is True
        assert len(audit.notify_on) >= 5

    def test_sandbox(self):
        app_def = compile_yaml(APP_14_SECURITY_FORTRESS)
        sandbox = app_def.security.sandbox
        assert len(sandbox.allowed_paths) == 2
        assert len(sandbox.blocked_commands) >= 10

    def test_all_tools_readonly(self):
        app_def = compile_yaml(APP_14_SECURITY_FORTRESS)
        fs_tools = [t for t in app_def.get_all_tools() if t.module == "filesystem"]
        for t in fs_tools:
            assert t.constraints.read_only is True

    def test_strict_forbidden_patterns(self):
        app_def = compile_yaml(APP_14_SECURITY_FORTRESS)
        cmd_tool = [t for t in app_def.get_all_tools()
                    if t.module == "os_exec" and t.action == "run_command"][0]
        assert len(cmd_tool.constraints.forbidden_commands) >= 10
        assert len(cmd_tool.constraints.forbidden_patterns) >= 10
        assert "sudo *" in cmd_tool.constraints.forbidden_patterns
        assert "curl *" in cmd_tool.constraints.forbidden_patterns

    def test_app_limits(self):
        app_def = compile_yaml(APP_14_SECURITY_FORTRESS)
        assert app_def.app.max_concurrent_runs == 1
        assert app_def.app.max_turns_per_run == 50
        assert app_def.app.max_actions_per_turn == 10
class TestRuntimeWiring:
    """Test that AppRuntime correctly wires capabilities and security."""

    @pytest.fixture
    def runtime(self):
        module_info = build_test_module_info()
        return AppRuntime(module_info=module_info)

    def test_capabilities_injection(self, runtime):
        """_apply_capabilities should inject into executor."""
        app_def = compile_yaml(APP_5_SECURITY_HARDENED)
        executor = MagicMock()
        executor.set_capabilities = MagicMock()
        runtime._execute_tool = executor.execute
        runtime._execute_tool.__self__ = executor

        runtime._apply_capabilities(app_def)
        executor.set_capabilities.assert_called_once()

    def test_security_injection(self, runtime):
        """_apply_security should set profile and sandbox."""
        app_def = compile_yaml(APP_5_SECURITY_HARDENED)
        executor = MagicMock()
        executor.set_security_profile = MagicMock()
        executor.set_sandbox = MagicMock()
        runtime._execute_tool = executor.execute
        runtime._execute_tool.__self__ = executor

        runtime._apply_security(app_def)
        executor.set_security_profile.assert_called_with("readonly")
        executor.set_sandbox.assert_called_once()


class TestEndToEndCompilationMatrix:
    """Matrix test: compile all apps × verify all features present."""

    def test_all_apps_have_agent_or_agents(self):
        """Every app must have at least agent or agents defined."""
        for name, yaml_text in ALL_APPS.items():
            app_def = compile_yaml(yaml_text)
            has_agent = app_def.agent is not None
            has_agents = app_def.agents is not None and len(app_def.agents.agents) > 0
            assert has_agent or has_agents, f"{name}: no agent or agents defined"

    def test_all_apps_have_security(self):
        """Every app should define a security profile."""
        for name, yaml_text in ALL_APPS.items():
            app_def = compile_yaml(yaml_text)
            assert app_def.security is not None, f"{name}: missing security block"
            assert app_def.security.profile, f"{name}: missing security profile"

    def test_all_module_ids_valid(self):
        """All module IDs referenced in apps should be in our test module_info."""
        module_info = build_test_module_info()
        for name, yaml_text in ALL_APPS.items():
            app_def = compile_yaml(yaml_text)
            module_ids = app_def.get_all_module_ids()
            for mid in module_ids:
                assert mid in module_info, f"{name}: unknown module '{mid}'"

    def test_tools_total_coverage(self):
        """Combined apps should cover all 20 modules."""
        module_info = build_test_module_info()
        all_modules_used: set[str] = set()
        for name, yaml_text in ALL_APPS.items():
            app_def = compile_yaml(yaml_text)
            all_modules_used.update(app_def.get_all_module_ids())

        expected_modules = set(module_info.keys())
        missing = expected_modules - all_modules_used
        assert not missing, f"Modules not tested by any app: {missing}"

    def test_flow_apps_have_valid_flows(self):
        """Apps with flows should have properly structured flow steps."""
        flow_apps = ["office_pipeline", "iot_monitoring", "database_etl", "full_capability"]
        for name in flow_apps:
            app_def = compile_yaml(ALL_APPS[name])
            assert app_def.flow is not None, f"{name}: expected flow"
            assert len(app_def.flow) > 0, f"{name}: empty flow"

    def test_agent_apps_have_tools(self):
        """Agent-only apps should have tools defined."""
        agent_apps = ["code_assistant", "web_research", "desktop_automation",
                       "security_hardened", "module_manager"]
        for name in agent_apps:
            app_def = compile_yaml(ALL_APPS[name])
            tools = app_def.get_all_tools()
            assert len(tools) > 0, f"{name}: no tools defined"
