"use client";

import { Badge, Tag } from "antd";
import type { PlanStatus, ActionStatus } from "@/types/plan";
import type { ModuleState } from "@/types/module";
import {
  planStatusColor,
  actionStatusColor,
  moduleStateColor,
  planStatusLabel,
  actionStatusLabel,
  moduleStateLabel,
} from "@/lib/utils/status-colors";

type StatusType = "plan" | "action" | "module";

interface StatusBadgeProps {
  type: StatusType;
  status: string;
  showDot?: boolean;
}

export function StatusBadge({ type, status, showDot = false }: StatusBadgeProps) {
  let color: string;
  let label: string;

  switch (type) {
    case "plan":
      color = planStatusColor[status as PlanStatus] ?? "default";
      label = planStatusLabel[status as PlanStatus] ?? status;
      break;
    case "action":
      color = actionStatusColor[status as ActionStatus] ?? "default";
      label = actionStatusLabel[status as ActionStatus] ?? status;
      break;
    case "module":
      color = moduleStateColor[status as ModuleState] ?? "default";
      label = moduleStateLabel[status as ModuleState] ?? status;
      break;
  }

  if (showDot) {
    return <Badge status={color as "success" | "processing" | "error" | "default" | "warning"} text={label} />;
  }

  return <Tag color={color}>{label}</Tag>;
}
