export type PlanStatus =
  | "pending"
  | "running"
  | "completed"
  | "failed"
  | "cancelled"
  | "paused";

export type ActionStatus =
  | "pending"
  | "running"
  | "completed"
  | "failed"
  | "skipped"
  | "rolled_back"
  | "awaiting_approval";

export type ExecutionMode = "sequential" | "parallel" | "dag";

export type PlanMode = "standard" | "compiler";

export type OnErrorBehavior = "abort" | "continue" | "retry";

export type ApprovalDecision =
  | "approve"
  | "reject"
  | "skip"
  | "modify"
  | "approve_always";

export type RiskLevel = "low" | "medium" | "high" | "critical";

export interface ActionResult {
  action_id: string;
  module: string;
  action: string;
  status: ActionStatus;
  started_at: string | null;
  finished_at: string | null;
  result: Record<string, unknown> | null;
  error: string | null;
  alternatives: string[] | null;
  approval_metadata: ApprovalMetadata | null;
}

export interface ApprovalMetadata {
  risk_level: RiskLevel;
  clarification_options: string[];
  requested_at: string;
}

export interface PlanResponse {
  plan_id: string;
  status: PlanStatus;
  description?: string;
  created_at: number; // Unix timestamp (float)
  updated_at: number; // Unix timestamp (float)
  actions?: ActionResult[];
  rejection_details?: Record<string, unknown> | null;
}

export interface PlanListResponse {
  plans: PlanResponse[];
  total: number;
  page: number;
  per_page: number;
}

export interface SubmitPlanRequest {
  plan: Record<string, unknown>;
  async_execution?: boolean;
}

export interface ApprovalRequest {
  plan_id: string;
  action_id: string;
  risk_level: RiskLevel;
  clarification_options: string[];
  requested_at: string;
}

export interface ApprovePlanActionRequest {
  decision: ApprovalDecision;
  reason?: string;
  modified_params?: Record<string, unknown>;
}
