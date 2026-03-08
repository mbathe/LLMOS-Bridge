// ─── App Language Types ───────────────────────────────────────────

export type AppStatus = "registered" | "running" | "stopped" | "error";

export interface AppRecord {
  id: string;
  name: string;
  version: string;
  description: string;
  author: string;
  file_path: string;
  status: AppStatus;
  tags: string[];
  created_at: number;
  updated_at: number;
  last_run_at: number;
  run_count: number;
  error_message: string;
  application_id: string;
  prepared: boolean;
}

export interface RegisterAppRequest {
  yaml_text?: string;
  file_path?: string;
  application_id?: string;
}

export interface PrepareAppResponse {
  app_name: string;
  modules_checked: number;
  modules_missing: string[];
  tools_resolved: number;
  llm_warmed: boolean;
  memory_ready: boolean;
  capabilities_applied: boolean;
  duration_ms: number;
  ready: boolean;
}

export interface RunAppRequest {
  input: string;
  variables?: Record<string, unknown>;
  stream?: boolean;
}

export interface RunAppResponse {
  success: boolean;
  output: string;
  error: string | null;
  duration_ms: number;
  total_turns: number;
  stop_reason: string;
}

export interface ValidateAppResponse {
  valid: boolean;
  errors: string[];
}

export interface UpdateStatusRequest {
  status: AppStatus;
}

export interface ExecuteToolRequest {
  module_id: string;
  action: string;
  params: Record<string, unknown>;
  app_id?: string;
}

export interface ExecuteToolResponse {
  success: boolean;
  result?: Record<string, unknown>;
  error?: string;
}

/**
 * Linked Application identity (auto-created from YAML app registration).
 * The app_id is shared between AppRecord.id and ApplicationResponse.app_id.
 */
export interface LinkedApplicationInfo {
  app_id: string;
  name: string;
  allowed_modules: string[];
  allowed_actions: Record<string, string[]>;
  max_concurrent_plans: number;
  max_actions_per_plan: number;
  enabled: boolean;
}

/** Deny rule declared in capabilities.deny */
export interface YamlDenyRule {
  module?: string;
  action?: string;
  reason?: string;
  when?: string;
}

/** Approval rule declared in capabilities.approval_required */
export interface YamlApprovalRule {
  module?: string;
  action?: string;
  message?: string;
  timeout?: string;
  on_timeout?: string;
}

/** Agent brain configuration */
export interface YamlAgentBrain {
  provider: string;
  model: string;
  temperature?: number;
  max_tokens?: number;
}

/** Trigger summary */
export interface YamlTriggerSummary {
  id: string;
  type: string;
}

/**
 * Structured view of a YAML app's configuration + sync status with Identity.
 * Returned by GET /apps/{app_id}/parsed.
 */
export interface YamlParsedConfig {
  /** Modules declared in capabilities.grant */
  yaml_modules: string[];
  /** Per-module action restrictions from capabilities.grant */
  yaml_allowed_actions: Record<string, string[]>;
  /** Rules from capabilities.deny */
  yaml_deny: YamlDenyRule[];
  /** Rules from capabilities.approval_required */
  yaml_approval_required: YamlApprovalRule[];
  /** Security profile declared in security.profile */
  yaml_security_profile: string;
  /** Paths from security.sandbox.allowed_paths */
  yaml_sandbox_paths: string[];
  /** Agent brain config (provider, model, temperature, max_tokens) */
  yaml_agent: YamlAgentBrain | null;
  /** Triggers declared in the YAML */
  yaml_triggers: YamlTriggerSummary[];
  /** Variables declared in variables: block */
  yaml_variables: Record<string, string>;
  /** Current allowed_modules in the linked Application identity */
  identity_modules: string[];
  /** Current allowed_actions in the linked Application identity */
  identity_actions: Record<string, string[]>;
  /** True when YAML capabilities.grant matches the Identity exactly */
  in_sync: boolean;
}
