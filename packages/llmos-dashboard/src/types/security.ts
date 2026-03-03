export type PermissionScope = "session" | "permanent";

export interface PermissionGrant {
  permission: string;
  module_id: string;
  scope: PermissionScope;
  granted_at: string;
  granted_by: string;
  reason: string;
  expires_at: string | null;
  app_id: string;  // Which application this grant belongs to ("default" = global)
}

export interface ScannerInfo {
  scanner_id: string;
  name: string;
  enabled: boolean;
  description: string;
}

export interface ScanResult {
  scanner_id: string;
  verdict: "pass" | "warn" | "reject";
  risk_score: number;
  details: string;
  categories: string[];
}

export interface ScanPipelineResult {
  overall_verdict: "pass" | "warn" | "reject";
  results: ScanResult[];
  duration_ms: number;
}

export interface AuditEvent {
  event_type: string;
  timestamp: string;
  topic: string;
  payload: Record<string, unknown>;
}

export interface SecurityStatus {
  profile: string;
  scanner_pipeline_enabled: boolean;
  scanners: ScannerInfo[];
  permissions_count: number;
  rate_limiting_enabled: boolean;
}

// ---------------------------------------------------------------------------
// Security Layers
// ---------------------------------------------------------------------------

export interface SecurityLayer {
  id: string;
  name: string;
  order: number;
  enabled: boolean;
  description: string;
  config: Record<string, unknown>;
  stats?: Record<string, unknown>;
}

export interface SecurityLayersResponse {
  layers: SecurityLayer[];
  profile: string;
  decorators_enabled: boolean;
}

// ---------------------------------------------------------------------------
// Intent Verifier
// ---------------------------------------------------------------------------

export interface ThreatCategory {
  id: string;
  name: string;
  description: string;
  threat_type: string;
  enabled: boolean;
}

export interface IntentVerifierStatus {
  enabled: boolean;
  strict: boolean;
  provider: string;
  model: string;
  timeout: number;
  cache_size: number;
  cache_ttl: number;
  cache_entries: number;
  has_prompt_composer: boolean;
  threat_categories: ThreatCategory[];
}

export interface ThreatDetail {
  threat_type: string;
  severity: string;
  description: string;
  affected_action_ids: string[];
  evidence: string;
}

export interface VerificationResult {
  verdict: "approve" | "reject" | "warn" | "clarify";
  risk_level: string;
  reasoning: string;
  threats: ThreatDetail[];
  clarification_needed: string | null;
  recommendations: string[];
  analysis_duration_ms: number;
  llm_model: string;
  cached: boolean;
}

// ---------------------------------------------------------------------------
// Pattern Management
// ---------------------------------------------------------------------------

export interface PatternRule {
  id: string;
  category: string;
  severity: number;
  description: string;
  enabled: boolean;
}

export interface PatternsResponse {
  patterns: PatternRule[];
  categories: string[];
}

// ---------------------------------------------------------------------------
// Permissions
// ---------------------------------------------------------------------------

export interface PermissionsResponse {
  grants: PermissionGrant[];
  count: number;
}
