"use client";

import React from "react";
import {
  Row,
  Col,
  Typography,
  Space,
  Tag,
  Spin,
  Alert,
  List,
} from "antd";
import {
  SafetyCertificateOutlined,
  LockOutlined,
  ScanOutlined,
  AuditOutlined,
  KeyOutlined,
  DashboardOutlined,
  RightOutlined,
  EyeOutlined,
  CheckCircleOutlined,
  CloseCircleOutlined,
} from "@ant-design/icons";
import { useRouter } from "next/navigation";
import { ApiError } from "@/lib/api/client";
import { PageHeader } from "@/components/common/PageHeader";
import { StatCard } from "@/components/common/StatCard";
import { SecurityArchitecture } from "./_components/SecurityArchitecture";
import { useSecurity } from "@/hooks/useSecurity";

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

const profileTagColorMap: Record<string, string> = {
  readonly: "blue",
  local_worker: "green",
  power_user: "orange",
  unrestricted: "red",
};

const profileColorMap: Record<string, string> = {
  readonly: "#1677ff",
  local_worker: "#52c41a",
  power_user: "#fa8c16",
  unrestricted: "#ff4d4f",
};

interface NavItem {
  key: string;
  icon: React.ReactNode;
  title: string;
  description: string;
  path: string;
  extra?: React.ReactNode;
}

export default function SecurityPage() {
  const router = useRouter();
  const {
    layers,
    status,
    scanners,
    intentStatus,
  } = useSecurity();

  const layersData = layers.data;
  const statusData = status.data;
  const scannersData = scanners.data;
  const intentData = intentStatus.data;
  const decoratorsEnabled = layersData?.decorators_enabled ?? false;

  const isLoading = layers.isLoading && scanners.isLoading;

  if (isLoading) {
    return (
      <div style={{ textAlign: "center", padding: 80 }}>
        <Spin size="large" />
      </div>
    );
  }

  if (layers.error && !layersData && !scannersData) {
    return (
      <Alert
        type="error"
        message="Failed to load security status"
        description={getErrorMessage(layers.error)}
        showIcon
      />
    );
  }

  const scannersList = scannersData?.scanners ?? [];
  const enabledScanners = scannersList.filter((s) => s.enabled);
  const profile = layersData?.profile ?? statusData?.profile ?? "";
  const permLayer = layersData?.layers?.find((l) => l.id === "permission_system");
  const permCount = (permLayer?.stats as Record<string, number> | undefined)?.permissions_count ?? statusData?.permissions_count ?? 0;
  const rateLimitEnabled = decoratorsEnabled && (permLayer?.config as Record<string, unknown> | undefined)?.rate_limiting === true;

  const navItems: NavItem[] = [
    {
      key: "scanners",
      icon: <ScanOutlined style={{ fontSize: 20, color: "#1677ff" }} />,
      title: "Scanner Pipeline",
      description: "Configure scanners, manage heuristic patterns, test input validation",
      path: "/security/scanners",
      extra: (
        <Tag color="cyan">
          {enabledScanners.length}/{scannersList.length} active
        </Tag>
      ),
    },
    {
      key: "intent-verifier",
      icon: <EyeOutlined style={{ fontSize: 20, color: "#722ed1" }} />,
      title: "Intent Verifier",
      description: "LLM-based threat analysis configuration and testing",
      path: "/security/intent-verifier",
      extra: intentData ? (
        <Tag color={intentData.enabled && intentData.model ? "purple" : "default"}>
          {intentData.model || "Not configured"}
        </Tag>
      ) : undefined,
    },
    {
      key: "permissions",
      icon: <KeyOutlined style={{ fontSize: 20, color: "#fa8c16" }} />,
      title: "System Permissions",
      description: "Read-only audit of all OS permission grants across every application. Manage permissions in Applications → Security tab.",
      path: "/security/permissions",
      extra: (
        <Tag color="blue">{permCount} active</Tag>
      ),
    },
    {
      key: "audit",
      icon: <AuditOutlined style={{ fontSize: 20, color: "#52c41a" }} />,
      title: "Audit Log",
      description: "Real-time security event trail and activity history",
      path: "/security/audit",
    },
  ];

  return (
    <Space direction="vertical" size="large" style={{ width: "100%" }}>
      <PageHeader
        icon={<SafetyCertificateOutlined />}
        title="Security Center"
        subtitle="Monitor and configure the multi-layer security architecture"
        tags={
          profile ? (
            <Tag color={profileTagColorMap[profile] ?? "default"}>
              {profile}
            </Tag>
          ) : undefined
        }
      />

      {/* Architecture Visualization */}
      {layersData && (
        <SecurityArchitecture layers={layersData.layers} />
      )}

      {/* Summary Stats */}
      <Row gutter={[16, 16]}>
        <Col xs={24} sm={12} lg={6}>
          <StatCard
            title="Permission Profile"
            value={profile || "N/A"}
            prefix={<LockOutlined />}
            valueStyle={{ fontSize: 18, textTransform: "capitalize" as const }}
            color={profileColorMap[profile] ?? "#8c8c8c"}
            footer={
              profile ? (
                <Tag color={profileTagColorMap[profile] ?? "default"}>{profile}</Tag>
              ) : undefined
            }
          />
        </Col>
        <Col xs={24} sm={12} lg={6}>
          <StatCard
            title="Active Permissions"
            value={permCount}
            prefix={<KeyOutlined />}
            color="#1677ff"
            onClick={() => router.push("/security/permissions")}
            footer={
              decoratorsEnabled ? (
                <Text type="secondary" style={{ fontSize: 12 }}>
                  Click to view audit
                </Text>
              ) : (
                <Text type="secondary" style={{ fontSize: 12 }}>
                  Decorators off
                </Text>
              )
            }
          />
        </Col>
        <Col xs={24} sm={12} lg={6}>
          <StatCard
            title="Scanner Pipeline"
            value={scannersData?.enabled ? "Enabled" : "Disabled"}
            prefix={
              scannersData?.enabled ? (
                <CheckCircleOutlined style={{ color: "#52c41a" }} />
              ) : (
                <CloseCircleOutlined style={{ color: "#ff4d4f" }} />
              )
            }
            valueStyle={{ fontSize: 18 }}
            color={scannersData?.enabled ? "#52c41a" : "#ff4d4f"}
            onClick={() => router.push("/security/scanners")}
            footer={
              <Text type="secondary" style={{ fontSize: 12 }}>
                {enabledScanners.length} scanner{enabledScanners.length !== 1 ? "s" : ""} active
              </Text>
            }
          />
        </Col>
        <Col xs={24} sm={12} lg={6}>
          <StatCard
            title="Rate Limiting"
            value={rateLimitEnabled ? "Enabled" : "Disabled"}
            prefix={
              <DashboardOutlined
                style={{
                  color: rateLimitEnabled ? "#52c41a" : "#8c8c8c",
                }}
              />
            }
            valueStyle={{ fontSize: 18 }}
            color={rateLimitEnabled ? "#52c41a" : "#d9d9d9"}
            footer={
              <Text type="secondary" style={{ fontSize: 12 }}>
                {rateLimitEnabled
                  ? "Per-action rate limits active"
                  : decoratorsEnabled
                    ? "No rate limits enforced"
                    : "Requires enable_decorators"}
              </Text>
            }
          />
        </Col>
      </Row>

      {/* Navigation */}
      <Row gutter={[16, 16]}>
        <Col xs={24}>
          <List
            grid={{ gutter: 16, xs: 1, sm: 2, lg: 4 }}
            dataSource={navItems}
            renderItem={(item) => (
              <List.Item>
                <div
                  onClick={() => router.push(item.path)}
                  style={{
                    cursor: "pointer",
                    padding: "20px",
                    borderRadius: 8,
                    border: "1px solid var(--ant-color-border)",
                    background: "var(--ant-color-bg-container)",
                    transition: "all 0.2s",
                    height: "100%",
                  }}
                  onMouseEnter={(e) => {
                    (e.currentTarget as HTMLElement).style.borderColor =
                      "var(--ant-color-primary)";
                    (e.currentTarget as HTMLElement).style.boxShadow =
                      "0 2px 8px rgba(0,0,0,0.06)";
                  }}
                  onMouseLeave={(e) => {
                    (e.currentTarget as HTMLElement).style.borderColor =
                      "var(--ant-color-border)";
                    (e.currentTarget as HTMLElement).style.boxShadow = "none";
                  }}
                >
                  <Space direction="vertical" size={8} style={{ width: "100%" }}>
                    <Space style={{ width: "100%", justifyContent: "space-between" }}>
                      <div
                        style={{
                          width: 40,
                          height: 40,
                          borderRadius: 8,
                          background: "var(--ant-color-bg-layout)",
                          display: "flex",
                          alignItems: "center",
                          justifyContent: "center",
                        }}
                      >
                        {item.icon}
                      </div>
                      <RightOutlined style={{ color: "var(--ant-color-text-quaternary)", fontSize: 12 }} />
                    </Space>
                    <Text strong>{item.title}</Text>
                    <Text type="secondary" style={{ fontSize: 12 }}>
                      {item.description}
                    </Text>
                    {item.extra && <div>{item.extra}</div>}
                  </Space>
                </div>
              </List.Item>
            )}
          />
        </Col>
      </Row>
    </Space>
  );
}
