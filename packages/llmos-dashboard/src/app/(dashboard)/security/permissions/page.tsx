"use client";

import React from "react";
import {
  Card,
  Row,
  Col,
  Table,
  Space,
  Tag,
  Button,
  Typography,
  Spin,
  Alert,
  Tooltip,
  Badge,
} from "antd";
import {
  ArrowLeftOutlined,
  ReloadOutlined,
  KeyOutlined,
  LockOutlined,
  ClockCircleOutlined,
  SafetyCertificateOutlined,
  InfoCircleOutlined,
  AppstoreOutlined,
} from "@ant-design/icons";
import { useRouter } from "next/navigation";
import { ApiError } from "@/lib/api/client";
import { PageHeader } from "@/components/common/PageHeader";
import { StatCard } from "@/components/common/StatCard";
import { formatDate, timeAgo } from "@/lib/utils/formatters";
import { useSecurity } from "@/hooks/useSecurity";
import type { PermissionGrant } from "@/types/security";
import type { ColumnsType } from "antd/es/table";

const { Text } = Typography;

function getErrorMessage(error: unknown): string {
  if (error instanceof ApiError) return error.detail ?? error.message ?? "Unknown error";
  if (error instanceof Error) return error.message;
  return "Unknown error";
}

const PERMISSION_RISK: Record<string, string> = {
  "filesystem.read": "low",
  "filesystem.write": "medium",
  "filesystem.delete": "high",
  "filesystem.sensitive": "critical",
  "device.camera": "high",
  "device.microphone": "high",
  "device.screen": "medium",
  "device.keyboard": "critical",
  "network.read": "low",
  "network.send": "medium",
  "network.external": "medium",
  "data.database.read": "low",
  "data.database.write": "medium",
  "data.database.delete": "high",
  "data.credentials": "critical",
  "data.personal": "high",
  "os.process.execute": "medium",
  "os.process.kill": "high",
  "os.admin": "critical",
  "app.browser": "medium",
  "app.email.read": "medium",
  "app.email.send": "high",
  "iot.gpio.read": "low",
  "iot.gpio.write": "medium",
  "iot.sensor": "low",
  "iot.actuator": "high",
  "module.read": "low",
  "module.manage": "medium",
  "module.install": "high",
};

const riskTagColors: Record<string, string> = {
  low: "green",
  medium: "orange",
  high: "red",
  critical: "volcano",
};

export default function PermissionsPage() {
  const router = useRouter();
  const { layers, permissions: permissionsQuery } = useSecurity();
  const decoratorsEnabled = layers.data?.decorators_enabled ?? false;
  const { data, isLoading, error, refetch } = permissionsQuery;

  const permissions = data?.grants ?? [];
  const permanentCount = permissions.filter((p) => p.scope === "permanent").length;

  // Group by app_id for the summary
  const byApp = permissions.reduce<Record<string, number>>((acc, g) => {
    acc[g.app_id ?? "default"] = (acc[g.app_id ?? "default"] ?? 0) + 1;
    return acc;
  }, {});

  const columns: ColumnsType<PermissionGrant> = [
    {
      title: "Application",
      dataIndex: "app_id",
      key: "app_id",
      width: 120,
      render: (appId: string) => (
        <Tag
          color={appId === "default" ? "default" : "blue"}
          style={{ borderRadius: 4, fontFamily: "monospace", fontSize: 11 }}
        >
          {appId || "default"}
        </Tag>
      ),
      filters: Object.keys(byApp).map((a) => ({ text: a, value: a })),
      onFilter: (value, record) => (record.app_id ?? "default") === value,
    },
    {
      title: "Permission",
      dataIndex: "permission",
      key: "permission",
      render: (perm: string) => (
        <Space>
          <KeyOutlined style={{ color: "#1677ff" }} />
          <Text strong style={{ fontFamily: "monospace", fontSize: 12 }}>{perm}</Text>
        </Space>
      ),
      sorter: (a, b) => a.permission.localeCompare(b.permission),
    },
    {
      title: "Risk",
      key: "risk",
      width: 90,
      render: (_: unknown, record: PermissionGrant) => {
        const risk = PERMISSION_RISK[record.permission] ?? "low";
        return (
          <Tag
            color={riskTagColors[risk] ?? "default"}
            style={{ borderRadius: 4, textTransform: "capitalize" as const }}
          >
            {risk}
          </Tag>
        );
      },
    },
    {
      title: "Module",
      dataIndex: "module_id",
      key: "module_id",
      render: (moduleId: string) => (
        <Tag color="purple" style={{ borderRadius: 4 }}>{moduleId}</Tag>
      ),
    },
    {
      title: "Scope",
      dataIndex: "scope",
      key: "scope",
      width: 130,
      render: (scope: string) => (
        <Badge
          status={scope === "permanent" ? "processing" : "warning"}
          text={
            <Tag color={scope === "permanent" ? "purple" : "cyan"} style={{ borderRadius: 4 }}>
              {scope === "permanent" ? (
                <Space size={4}><LockOutlined style={{ fontSize: 10 }} /><span>Permanent</span></Space>
              ) : (
                <Space size={4}><ClockCircleOutlined style={{ fontSize: 10 }} /><span>Session</span></Space>
              )}
            </Tag>
          }
        />
      ),
    },
    {
      title: "Reason",
      dataIndex: "reason",
      key: "reason",
      ellipsis: true,
      render: (v: string) => v ? (
        <Tooltip title={v}>
          <Text type="secondary" style={{ fontSize: 12 }}>{v}</Text>
        </Tooltip>
      ) : <Text type="secondary" style={{ fontSize: 12 }}>—</Text>,
    },
    {
      title: "Granted",
      dataIndex: "granted_at",
      key: "granted_at",
      width: 110,
      render: (d: string) => (
        <Tooltip title={formatDate(d)}>
          <Text type="secondary" style={{ fontSize: 12 }}>{timeAgo(d)}</Text>
        </Tooltip>
      ),
      sorter: (a, b) => new Date(a.granted_at).getTime() - new Date(b.granted_at).getTime(),
    },
  ];

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
        message="Failed to load permissions"
        description={getErrorMessage(error)}
        showIcon
      />
    );
  }

  return (
    <Space direction="vertical" size="large" style={{ width: "100%" }}>
      <PageHeader
        icon={<KeyOutlined />}
        title="System Permissions"
        subtitle="Read-only overview of all OS-level permission grants across every application"
        tags={<Tag color="blue" style={{ borderRadius: 4 }}>{permissions.length} active</Tag>}
        extra={
          <Space>
            <Button icon={<ArrowLeftOutlined />} onClick={() => router.push("/security")}>
              Back
            </Button>
            <Button icon={<ReloadOutlined />} onClick={() => refetch()}>
              Refresh
            </Button>
          </Space>
        }
      />

      {/* Info alert: how to manage permissions */}
      <Alert
        type="info"
        icon={<InfoCircleOutlined />}
        showIcon
        message="Permissions are managed per application"
        description={
          <span>
            To grant or revoke OS permissions, go to{" "}
            <Button
              type="link"
              size="small"
              style={{ padding: 0 }}
              onClick={() => router.push("/applications")}
            >
              Applications
            </Button>{" "}
            → select an application → <strong>Security</strong> tab → OS Permissions.
          </span>
        }
        style={{ borderRadius: 8 }}
      />

      {!decoratorsEnabled && (
        <Alert
          type="warning"
          message="Runtime enforcement off"
          description="security_advanced.enable_decorators is false — permissions exist in the store but are not enforced at runtime."
          showIcon
          style={{ borderRadius: 8 }}
        />
      )}

      {/* Summary Stats */}
      <Row gutter={[16, 16]}>
        <Col xs={24} sm={8}>
          <StatCard
            title="Total Permissions"
            value={permissions.length}
            prefix={<SafetyCertificateOutlined />}
            color="#1677ff"
            footer={<Text type="secondary" style={{ fontSize: 12 }}>All active grants (all apps)</Text>}
          />
        </Col>
        <Col xs={24} sm={8}>
          <StatCard
            title="Permanent"
            value={permanentCount}
            prefix={<LockOutlined style={{ color: "#722ed1" }} />}
            color="#722ed1"
            footer={<Text type="secondary" style={{ fontSize: 12 }}>Persist across sessions</Text>}
          />
        </Col>
        <Col xs={24} sm={8}>
          <StatCard
            title="Applications"
            value={Object.keys(byApp).length}
            prefix={<AppstoreOutlined style={{ color: "#13c2c2" }} />}
            color="#13c2c2"
            footer={<Text type="secondary" style={{ fontSize: 12 }}>Apps with grants</Text>}
          />
        </Col>
      </Row>

      {/* Grants Table */}
      <Card
        title={<Space><KeyOutlined /><span>All Permission Grants</span></Space>}
        extra={<Text type="secondary" style={{ fontSize: 12 }}>{permissions.length} total</Text>}
      >
        <Table
          columns={columns}
          dataSource={permissions}
          rowKey={(record) => `${record.app_id}:${record.module_id}:${record.permission}`}
          loading={isLoading}
          pagination={{ pageSize: 20, showTotal: (total) => `${total} permissions`, showSizeChanger: true }}
          size="middle"
        />
      </Card>
    </Space>
  );
}
