"use client";

import React from "react";
import { useParams, useRouter } from "next/navigation";
import dynamic from "next/dynamic";
import { Tabs, Space, Button, Tag, Badge, Typography, Spin } from "antd";
import {
  ArrowLeftOutlined,
  AppstoreOutlined,
  DashboardOutlined,
  ThunderboltOutlined,
  SettingOutlined,
  HeartOutlined,
  FileTextOutlined,
} from "@ant-design/icons";
import { useModuleDetail } from "@/hooks/useModuleDetail";
import { PageHeader } from "@/components/common/PageHeader";
import { OverviewTab } from "./_components/OverviewTab";
import { ActionsTab } from "./_components/ActionsTab";
import { ConfigurationTab } from "./_components/ConfigurationTab";
import { HealthMetricsTab } from "./_components/HealthMetricsTab";
import { DangerZone } from "./_components/DangerZone";

const DocumentationTab = dynamic(
  () => import("./_components/DocumentationTab").then((m) => ({ default: m.DocumentationTab })),
  { ssr: false, loading: () => <Spin style={{ display: "block", textAlign: "center", padding: 48 }} /> },
);

const { Text } = Typography;

const stateColorMap: Record<string, string> = {
  loaded: "default",
  starting: "processing",
  active: "success",
  paused: "warning",
  stopping: "processing",
  disabled: "default",
  error: "error",
};

export default function ModuleDetailPage() {
  const { moduleId } = useParams<{ moduleId: string }>();
  const router = useRouter();
  const hook = useModuleDetail(moduleId);

  const { manifest, info, health } = hook;
  const isLoading = manifest.isLoading || info.isLoading;

  if (isLoading) {
    return (
      <div style={{ textAlign: "center", padding: 80 }}>
        <Spin size="large" />
        <div style={{ marginTop: 16 }}>
          <Text type="secondary">Loading module details...</Text>
        </div>
      </div>
    );
  }

  const currentState = info.data?.state ?? "loaded";
  const moduleType = info.data?.type ?? manifest.data?.module_type ?? "user";
  const version = manifest.data?.version ?? info.data?.version;
  const description = manifest.data?.description ?? info.data?.description ?? "Module details";
  const actionsCount = manifest.data?.actions?.length ?? 0;
  const isHealthy = health.data?.healthy;

  return (
    <Space direction="vertical" size="large" style={{ width: "100%" }}>
      <PageHeader
        icon={<AppstoreOutlined />}
        title={moduleId}
        subtitle={description}
        tags={
          <Space size={4}>
            {version && (
              <Tag color="blue" style={{ fontSize: 12 }}>
                v{version}
              </Tag>
            )}
            <Tag color={moduleType === "system" ? "purple" : "green"} style={{ fontSize: 12 }}>
              {moduleType}
            </Tag>
            <Badge
              status={stateColorMap[currentState] as "success" | "processing" | "default" | "error" | "warning"}
              text={
                <Text style={{ fontSize: 12 }}>
                  {currentState}
                </Text>
              }
            />
            {isHealthy !== undefined && (
              <Badge
                status={isHealthy ? "success" : "error"}
                text={
                  <Text style={{ fontSize: 12 }}>
                    {isHealthy ? "Healthy" : "Unhealthy"}
                  </Text>
                }
              />
            )}
          </Space>
        }
        extra={
          <Button
            icon={<ArrowLeftOutlined />}
            onClick={() => router.push("/modules")}
          >
            Back to Modules
          </Button>
        }
      />

      <Tabs
        defaultActiveKey="overview"
        type="card"
        size="large"
        destroyInactiveTabPane={true}
        items={[
          {
            key: "overview",
            label: (
              <span>
                <DashboardOutlined /> Overview
              </span>
            ),
            children: <OverviewTab hook={hook} />,
          },
          {
            key: "actions",
            label: (
              <span>
                <ThunderboltOutlined /> Actions ({actionsCount})
              </span>
            ),
            children: <ActionsTab hook={hook} />,
          },
          {
            key: "configuration",
            label: (
              <span>
                <SettingOutlined /> Configuration
              </span>
            ),
            children: <ConfigurationTab hook={hook} />,
          },
          {
            key: "health",
            label: (
              <span>
                <HeartOutlined /> Health & Metrics
              </span>
            ),
            children: <HealthMetricsTab hook={hook} />,
          },
          {
            key: "docs",
            label: (
              <span>
                <FileTextOutlined /> Documentation
              </span>
            ),
            children: <DocumentationTab hook={hook} />,
          },
        ]}
      />

      <DangerZone hook={hook} moduleId={moduleId} />
    </Space>
  );
}
