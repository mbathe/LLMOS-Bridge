"use client";

import React from "react";
import { Card, Button, Space, Typography, Tag, Tooltip, message, Badge } from "antd";
import {
  PlayCircleOutlined,
  StopOutlined,
  PauseCircleOutlined,
  CaretRightOutlined,
  ReloadOutlined,
  LoadingOutlined,
} from "@ant-design/icons";
import type { ModuleState } from "@/types/module";
import type { UseModuleDetailReturn } from "@/hooks/useModuleDetail";

const { Text } = Typography;

interface LifecycleControlProps {
  state: ModuleState;
  moduleId: string;
  hook: UseModuleDetailReturn;
}

const stateColorMap: Record<ModuleState, string> = {
  loaded: "#8c8c8c",
  starting: "#1677ff",
  active: "#52c41a",
  paused: "#faad14",
  stopping: "#1677ff",
  disabled: "#8c8c8c",
  error: "#ff4d4f",
};

const stateLabelMap: Record<ModuleState, string> = {
  loaded: "Loaded",
  starting: "Starting...",
  active: "Active",
  paused: "Paused",
  stopping: "Stopping...",
  disabled: "Disabled",
  error: "Error",
};

const validTransitions: Record<ModuleState, string[]> = {
  loaded: ["enable"],
  starting: [],
  active: ["disable", "pause", "restart"],
  paused: ["resume", "disable"],
  stopping: [],
  disabled: ["enable"],
  error: ["enable", "restart"],
};

export function LifecycleControl({ state, moduleId, hook }: LifecycleControlProps) {
  const allowed = validTransitions[state] ?? [];
  const isTransitioning = state === "starting" || state === "stopping";

  const handleAction = async (action: string) => {
    try {
      let result;
      switch (action) {
        case "enable":
          result = await hook.enableModule.mutateAsync();
          break;
        case "disable":
          result = await hook.disableModule.mutateAsync();
          break;
        case "pause":
          result = await hook.pauseModule.mutateAsync();
          break;
        case "resume":
          result = await hook.resumeModule.mutateAsync();
          break;
        case "restart":
          result = await hook.restartModule.mutateAsync();
          break;
      }
      if (result?.success) {
        message.success(`Module ${action}d successfully`);
      } else if (result?.error) {
        message.error(result.error);
      }
    } catch (err) {
      message.error(err instanceof Error ? err.message : "Action failed");
    }
  };

  const anyPending =
    hook.enableModule.isPending ||
    hook.disableModule.isPending ||
    hook.pauseModule.isPending ||
    hook.resumeModule.isPending ||
    hook.restartModule.isPending;

  return (
    <Card
      title={
        <Space>
          <Text strong>Lifecycle Control</Text>
          <Tag
            color={stateColorMap[state]}
            icon={isTransitioning ? <LoadingOutlined spin /> : undefined}
            style={{ fontSize: 13 }}
          >
            {stateLabelMap[state]}
          </Tag>
        </Space>
      }
      styles={{ body: { padding: "16px 20px" } }}
    >
      <div style={{ display: "flex", alignItems: "center", gap: 24 }}>
        {/* State Indicator */}
        <div style={{ textAlign: "center", minWidth: 80 }}>
          <Badge
            status={
              state === "active"
                ? "success"
                : state === "error"
                  ? "error"
                  : state === "paused"
                    ? "warning"
                    : isTransitioning
                      ? "processing"
                      : "default"
            }
            text=""
            style={{ transform: "scale(2)" }}
          />
          <div style={{ marginTop: 8 }}>
            <Text type="secondary" style={{ fontSize: 11 }}>
              {moduleId}
            </Text>
          </div>
        </div>

        {/* Action Buttons */}
        <Space wrap>
          <Tooltip title={allowed.includes("enable") ? "Start/enable the module" : `Cannot enable from ${state} state`}>
            <Button
              icon={<PlayCircleOutlined />}
              disabled={!allowed.includes("enable") || anyPending}
              loading={hook.enableModule.isPending}
              onClick={() => handleAction("enable")}
              type={allowed.includes("enable") ? "primary" : "default"}
            >
              Enable
            </Button>
          </Tooltip>

          <Tooltip title={allowed.includes("disable") ? "Stop/disable the module" : `Cannot disable from ${state} state`}>
            <Button
              icon={<StopOutlined />}
              disabled={!allowed.includes("disable") || anyPending}
              loading={hook.disableModule.isPending}
              onClick={() => handleAction("disable")}
              danger={allowed.includes("disable")}
            >
              Disable
            </Button>
          </Tooltip>

          <Tooltip title={allowed.includes("pause") ? "Temporarily suspend actions" : `Cannot pause from ${state} state`}>
            <Button
              icon={<PauseCircleOutlined />}
              disabled={!allowed.includes("pause") || anyPending}
              loading={hook.pauseModule.isPending}
              onClick={() => handleAction("pause")}
            >
              Pause
            </Button>
          </Tooltip>

          <Tooltip title={allowed.includes("resume") ? "Resume from paused state" : `Cannot resume from ${state} state`}>
            <Button
              icon={<CaretRightOutlined />}
              disabled={!allowed.includes("resume") || anyPending}
              loading={hook.resumeModule.isPending}
              onClick={() => handleAction("resume")}
              type={allowed.includes("resume") ? "primary" : "default"}
            >
              Resume
            </Button>
          </Tooltip>

          <Tooltip title={allowed.includes("restart") ? "Stop then start the module" : `Cannot restart from ${state} state`}>
            <Button
              icon={<ReloadOutlined />}
              disabled={!allowed.includes("restart") || anyPending}
              loading={hook.restartModule.isPending}
              onClick={() => handleAction("restart")}
            >
              Restart
            </Button>
          </Tooltip>
        </Space>
      </div>
    </Card>
  );
}
