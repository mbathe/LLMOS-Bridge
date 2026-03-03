import type { PlanStatus, ActionStatus } from "@/types/plan";
import type { ModuleState } from "@/types/module";

export const planStatusColor: Record<PlanStatus, string> = {
  pending: "default",
  running: "processing",
  completed: "success",
  failed: "error",
  cancelled: "default",
  paused: "warning",
};

export const actionStatusColor: Record<ActionStatus, string> = {
  pending: "default",
  running: "processing",
  completed: "success",
  failed: "error",
  skipped: "default",
  rolled_back: "purple",
  awaiting_approval: "warning",
};

export const moduleStateColor: Record<ModuleState, string> = {
  loaded: "default",
  starting: "processing",
  active: "success",
  paused: "warning",
  stopping: "processing",
  error: "error",
  disabled: "default",
};

export const planStatusLabel: Record<PlanStatus, string> = {
  pending: "Pending",
  running: "Running",
  completed: "Completed",
  failed: "Failed",
  cancelled: "Cancelled",
  paused: "Paused",
};

export const actionStatusLabel: Record<ActionStatus, string> = {
  pending: "Pending",
  running: "Running",
  completed: "Completed",
  failed: "Failed",
  skipped: "Skipped",
  rolled_back: "Rolled Back",
  awaiting_approval: "Awaiting Approval",
};

export const moduleStateLabel: Record<ModuleState, string> = {
  loaded: "Loaded",
  starting: "Starting",
  active: "Active",
  paused: "Paused",
  stopping: "Stopping",
  error: "Error",
  disabled: "Disabled",
};
