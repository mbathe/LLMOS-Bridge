export interface WSMessage {
  type: string;
  payload: Record<string, unknown>;
  timestamp: string;
}

export interface SSEEvent {
  event: string;
  data: Record<string, unknown>;
}

export interface ModulesStatus {
  available: string[];
  failed: Record<string, string>;
  platform_excluded: Record<string, string>;
}

export interface HealthResponse {
  status: string;
  version: string;
  protocol_version: string;
  uptime_seconds: number;
  modules_loaded: number;
  modules_failed: number;
  modules: ModulesStatus;
  active_plans: number;
  scanner_pipeline: Record<string, unknown>;
  timestamp: number;
}

export interface SystemStatus {
  total_modules: number;
  by_state: Record<string, number>;
  by_type: Record<string, number>;
  failed: Record<string, string>;
  platform_excluded: Record<string, string>;
  health: Record<string, { status: string; module_id: string; version: string }>;
}

export interface SystemConfig {
  [section: string]: Record<string, unknown>;
}

export interface ErrorResponse {
  error: string;
  code: string;
  detail: string | null;
  request_id: string;
}

export type EventTopic =
  | "llmos.plans"
  | "llmos.actions"
  | "llmos.security"
  | "llmos.modules"
  | "llmos.perception"
  | "llmos.memory"
  | "llmos.system"
  | "llmos.triggers"
  | "llmos.recordings"
  | "llmos.approval"
  | "llmos.actions.progress"
  | "llmos.actions.results"
  | "llmos.nodes";

export interface AuditEventEntry {
  event: string;
  _topic?: string;
  _timestamp?: number;
  [key: string]: unknown;
}

export interface AuditLogResponse {
  events: AuditEventEntry[];
  count: number;
}

export interface ScannerDetail {
  scanner_id: string;
  priority: number;
  version: string;
  description: string;
  pattern_count: number;
  enabled_pattern_count: number;
  categories: string[];
  enabled: boolean;
}

export interface ScannersResponse {
  enabled: boolean;
  fail_fast: boolean;
  reject_threshold: number;
  warn_threshold: number;
  scanners: ScannerDetail[];
}
