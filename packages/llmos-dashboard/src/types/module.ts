// ─── Enums / Unions ───

export type ModuleState =
  | "loaded"
  | "starting"
  | "active"
  | "paused"
  | "stopping"
  | "error"
  | "disabled";

export type ModuleType = "system" | "user";
export type SandboxLevel = "none" | "basic" | "strict" | "isolated";
export type RiskLevel = "" | "low" | "medium" | "high" | "critical";
export type ExecutionMode = "sync" | "async" | "background" | "scheduled";

// ─── Sub-types ───

export interface CapabilitySpec {
  permission: string;
  scope?: string;
  constraints?: Record<string, unknown>;
}

export interface ServiceDescriptor {
  name: string;
  methods: string[];
  description: string;
}

export interface ResourceLimits {
  max_cpu_percent: number;
  max_memory_mb: number;
  max_execution_seconds: number;
  max_concurrent_actions: number;
}

export interface ModuleSignature {
  public_key_fingerprint: string;
  signature_hex: string;
  signed_hash: string;
  signed_at: string;
}

// ─── ActionSpec (full v3) ───

export interface ActionSpec {
  name: string;
  description: string;
  params_schema: Record<string, unknown>;
  returns: string;
  returns_description?: string;
  permission_required: string | null;
  platforms: string[];
  examples: Record<string, unknown>[];
  tags?: string[];
  permissions?: string[];
  risk_level?: RiskLevel;
  irreversible?: boolean;
  data_classification?: string;
  streams_progress?: boolean;
  side_effects?: string[];
  output_schema?: Record<string, unknown>;
  execution_mode?: ExecutionMode;
  capabilities?: CapabilitySpec[];
}

// ─── ModuleManifest (full v2/v3) ───

export interface ModuleManifest {
  module_id: string;
  version: string;
  description: string;
  author?: string;
  homepage?: string;
  platforms: string[];
  actions: ActionSpec[];
  dependencies: string[] | Record<string, string>;
  tags: string[];
  declared_permissions: string[];
  services?: string[];
  // v2
  module_type?: ModuleType;
  provides_services?: ServiceDescriptor[];
  consumes_services?: string[];
  emits_events?: string[];
  subscribes_events?: string[];
  config_schema?: Record<string, unknown>;
  // v3
  resource_limits?: ResourceLimits;
  sandbox_level?: SandboxLevel;
  license?: string;
  optional_dependencies?: string[];
  module_dependencies?: Record<string, string>;
  signing?: ModuleSignature;
  declared_capabilities?: CapabilitySpec[];
}

// ─── GET /modules (public, array) ───

export interface ModuleInfo {
  module_id: string;
  available: boolean;
  version: string;
  description: string;
  action_count: number;
  state?: ModuleState;
  platforms?: string[];
  tags?: string[];
}

// ─── GET /admin/modules/{id} ───

export interface ModuleDetailInfo {
  module_id: string;
  version: string;
  description: string;
  state: ModuleState;
  type: ModuleType;
  actions: string[];
  disabled_actions: Record<string, string>;
  health?: Record<string, unknown>;
  metrics?: Record<string, unknown>;
}

// ─── Health / Metrics / State ───

export interface ModuleHealth {
  module_id: string;
  healthy: boolean;
  details: Record<string, unknown>;
  checked_at: string;
  [key: string]: unknown;
}

export interface ModuleMetrics {
  module_id: string;
  metrics: Record<string, unknown>;
  collected_at: string;
}

export interface ModuleStateSnapshot {
  module_id: string;
  state_snapshot: Record<string, unknown>;
}

export interface ModuleDescription {
  module_id: string;
  [key: string]: unknown;
}

// ─── Documentation ───

export interface ModuleDocs {
  module_id: string;
  readme: string | null;
  actions: string | null;
  integration: string | null;
  changelog: string | null;
}

// ─── Configuration ───

export interface ModuleConfigSchema {
  configurable: boolean;
  schema: JSONSchemaDefinition | null;
}

export interface JSONSchemaDefinition {
  type: string;
  title?: string;
  description?: string;
  properties?: Record<string, JSONSchemaProperty>;
  required?: string[];
  additionalProperties?: boolean;
}

export interface JSONSchemaProperty {
  type: string;
  title?: string;
  description?: string;
  default?: unknown;
  enum?: unknown[];
  minimum?: number;
  maximum?: number;
  minLength?: number;
  maxLength?: number;
  // UI metadata from ConfigField()
  "x-ui-label"?: string;
  "x-ui-category"?: string;
  "x-ui-widget"?: string;
  "x-ui-order"?: number;
  "x-ui-restart-required"?: boolean;
  "x-ui-secret"?: boolean;
}

// ─── Lifecycle / Action toggle responses ───

export interface LifecycleResponse {
  module_id: string;
  state: ModuleState;
  success: boolean;
  error?: string;
}

export interface ActionToggleResponse {
  module_id: string;
  action: string;
  enabled: boolean;
  reason?: string;
}

export interface VerifyResponse {
  verified: boolean;
  error?: string;
}

export interface UninstallResponse {
  success: boolean;
  module_id: string;
  version?: string;
  error?: string;
}

// ─── Module Status (used in health response) ───

export interface ModuleStatusDetail {
  available: ModuleInfo[];
  failed: Record<string, string>;
  platform_excluded: Record<string, string>;
}

// ─── Hub ───

export interface HubSearchResult {
  module_id: string;
  version: string;
  description: string;
  author: string;
  downloads: number;
  verified: boolean;
}

// ─── Community Module Install Pipeline ───

export interface InstalledModuleInfo {
  module_id: string;
  version: string;
  install_path: string;
  enabled: boolean;
  sandbox_level: string;
  installed_at: number;
  updated_at: number;
  trust_tier: string;
  scan_score: number;
  signature_status: string;
}

export interface InstalledModulesResponse {
  modules: InstalledModuleInfo[];
  total: number;
}

export interface InstallResult {
  success: boolean;
  module_id: string;
  version: string;
  installed_deps: string[];
  validation_warnings: string[];
  scan_score: number;
  trust_tier: string;
  scan_findings_count: number;
}

export interface UpgradeResult {
  success: boolean;
  module_id: string;
  version: string;
  validation_warnings: string[];
  scan_score: number;
  trust_tier: string;
  scan_findings_count: number;
}

// -- Security scan types --

export interface ModuleSecurityInfo {
  module_id: string;
  trust_tier: string;
  scan_score: number;
  signature_status: string;
  checksum: string;
  findings_count: number;
}

export interface ScanFinding {
  rule_id: string;
  category: string;
  severity: number;
  file_path: string;
  line_number: number;
  line_content: string;
  description: string;
}

export interface ScanReportResponse {
  module_id: string;
  scan_score: number;
  verdict: string;
  findings: ScanFinding[];
  files_scanned: number;
  scan_duration_ms: number;
}

export interface RescanResponse {
  module_id: string;
  scan_score: number;
  verdict: string;
  findings_count: number;
  trust_tier: string;
}
