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
}

export interface RegisterAppRequest {
  yaml_text?: string;
  file_path?: string;
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
