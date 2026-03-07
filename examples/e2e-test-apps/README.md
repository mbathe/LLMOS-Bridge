# E2E Test Applications

10 complex YAML apps that exercise every feature of the LLMOS App Language.
Used by `tests/integration/apps/test_e2e_apps.py` (129 tests).

| # | App | Modules | Features Tested |
|---|-----|---------|-----------------|
| 01 | Code Assistant | filesystem, os_exec, agent_spawn, context_manager, memory | Variables, fallback LLM, reactive loop, context management, episodic memory |
| 02 | Web Research | browser, api_http, memory, filesystem | Tool constraints (allowed_domains), working memory |
| 03 | Desktop Automation | computer_control, gui, perception_vision, window_tracker | Perception config, screenshot/OCR |
| 04 | Office Pipeline | excel, word, powerpoint, filesystem, database | Flow steps (action, parallel, branch, loop, try/catch), macros |
| 05 | Security Hardened | filesystem | Capabilities (grant/deny/approval), sandbox, read_only, audit config |
| 06 | IoT Monitoring | iot, triggers, recording, api_http, memory | Triggers (schedule, watch, event), recording, observability |
| 07 | Database ETL | database, database_gateway, excel, filesystem | Flow (map, reduce, pipe, race), module_config, multi-step pipelines |
| 08 | Multi-Agent Team | agent_spawn, memory, context_manager, api_http, filesystem | Multi-agent (hierarchical), agent roles, delegation |
| 09 | Module Manager | module_manager, security, filesystem | Module lifecycle, security scanning |
| 10 | Full Capability | filesystem, os_exec, memory, database, api_http, agent_spawn | All flow types (18), macros, goto, dispatch, emit, wait, approval |

## Running the tests

```bash
cd packages/llmos-bridge
poetry run pytest tests/integration/apps/ -v --no-cov
```
