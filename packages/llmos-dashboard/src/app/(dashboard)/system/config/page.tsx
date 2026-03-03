"use client";

import React from "react";
import { useQuery } from "@tanstack/react-query";
import {
  Typography,
  Space,
  Button,
  Spin,
  Alert,
  Collapse,
  Tag,
} from "antd";
import {
  ArrowLeftOutlined,
  ReloadOutlined,
  SettingOutlined,
  SafetyCertificateOutlined,
  DatabaseOutlined,
  ApiOutlined,
  AppstoreOutlined,
  CloudServerOutlined,
  EyeOutlined,
  ThunderboltOutlined,
  KeyOutlined,
} from "@ant-design/icons";
import { useRouter } from "next/navigation";
import { api, ApiError } from "@/lib/api/client";
import { PageHeader } from "@/components/common/PageHeader";
import { JsonViewer } from "@/components/common/JsonViewer";
import { EmptyState } from "@/components/common/EmptyState";
import type { SystemConfig } from "@/types/events";

const { Text } = Typography;

function getErrorMessage(error: unknown): string {
  if (error instanceof ApiError) {
    return error.detail ?? error.message ?? "Unknown error";
  }
  if (error instanceof Error) {
    return error.message;
  }
  return "Unknown error";
}

const sectionIconMap: Record<string, React.ReactNode> = {
  security: <SafetyCertificateOutlined style={{ color: "#ff4d4f" }} />,
  security_advanced: <KeyOutlined style={{ color: "#ff4d4f" }} />,
  database: <DatabaseOutlined style={{ color: "#1677ff" }} />,
  api: <ApiOutlined style={{ color: "#52c41a" }} />,
  modules: <AppstoreOutlined style={{ color: "#722ed1" }} />,
  module_manager: <AppstoreOutlined style={{ color: "#722ed1" }} />,
  server: <CloudServerOutlined style={{ color: "#fa8c16" }} />,
  perception: <EyeOutlined style={{ color: "#13c2c2" }} />,
  scanner_pipeline: <ThunderboltOutlined style={{ color: "#eb2f96" }} />,
  triggers: <ThunderboltOutlined style={{ color: "#fa8c16" }} />,
  recording: <DatabaseOutlined style={{ color: "#52c41a" }} />,
};

const sectionColorMap: Record<string, string> = {
  security: "red",
  security_advanced: "red",
  database: "blue",
  api: "green",
  modules: "purple",
  module_manager: "purple",
  server: "orange",
  perception: "cyan",
  scanner_pipeline: "magenta",
  triggers: "orange",
  recording: "green",
};

export default function SystemConfigPage() {
  const router = useRouter();

  const {
    data: config,
    isLoading,
    error,
    refetch,
  } = useQuery<SystemConfig>({
    queryKey: ["system-config"],
    queryFn: () => api.get<SystemConfig>("/admin/system/config"),
    retry: false,
  });

  if (isLoading) {
    return (
      <div style={{ textAlign: "center", padding: 80 }}>
        <Spin size="large" />
      </div>
    );
  }

  if (error) {
    return (
      <Alert
        type="error"
        message="Failed to load configuration"
        description={getErrorMessage(error)}
        showIcon
      />
    );
  }

  const sections = Object.entries(config ?? {});

  return (
    <Space direction="vertical" size="large" style={{ width: "100%" }}>
      <PageHeader
        icon={<SettingOutlined />}
        title="Configuration"
        subtitle="View daemon configuration settings"
        tags={
          <Tag color="blue" style={{ borderRadius: 4 }}>
            {sections.length} sections
          </Tag>
        }
        extra={
          <Space>
            <Button
              icon={<ArrowLeftOutlined />}
              onClick={() => router.push("/system")}
            >
              Back to System
            </Button>
            <Button icon={<ReloadOutlined />} onClick={() => refetch()}>
              Refresh
            </Button>
          </Space>
        }
      />

      {sections.length === 0 ? (
        <EmptyState description="No configuration data available." />
      ) : (
        <Collapse
          defaultActiveKey={sections.length > 0 ? [sections[0][0]] : []}
          style={{ borderRadius: 8 }}
          items={sections.map(([sectionName, sectionData]) => {
            const keyCount = Object.keys(
              sectionData as Record<string, unknown>
            ).length;
            const icon =
              sectionIconMap[sectionName] ?? (
                <SettingOutlined style={{ color: "#8c8c8c" }} />
              );
            const tagColor = sectionColorMap[sectionName] ?? "default";

            return {
              key: sectionName,
              label: (
                <div
                  style={{
                    display: "flex",
                    alignItems: "center",
                    justifyContent: "space-between",
                    width: "100%",
                    paddingRight: 8,
                  }}
                >
                  <Space size="middle">
                    <span style={{ fontSize: 16, display: "flex", alignItems: "center" }}>
                      {icon}
                    </span>
                    <Text strong style={{ fontSize: 14 }}>
                      {sectionName}
                    </Text>
                  </Space>
                  <Space size={8}>
                    <Tag
                      color={tagColor}
                      style={{ borderRadius: 4, fontSize: 11 }}
                    >
                      {keyCount} {keyCount === 1 ? "key" : "keys"}
                    </Tag>
                  </Space>
                </div>
              ),
              children: (
                <div style={{ padding: "4px 0" }}>
                  <JsonViewer data={sectionData} maxHeight={500} />
                </div>
              ),
              style: {
                marginBottom: 8,
                borderRadius: 8,
                overflow: "hidden",
              },
            };
          })}
        />
      )}
    </Space>
  );
}
