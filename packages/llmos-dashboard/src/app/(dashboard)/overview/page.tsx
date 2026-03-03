"use client";

import React from "react";
import {
  Row,
  Col,
  Card,
  Typography,
  Space,
  List,
  Tag,
  Spin,
  Button,
  Progress,
  Alert,
  Divider,
  Tooltip,
  Badge,
} from "antd";
import {
  CheckCircleOutlined,
  CloseCircleOutlined,
  AppstoreOutlined,
  FileTextOutlined,
  ClockCircleOutlined,
  ThunderboltOutlined,
  SafetyOutlined,
  SettingOutlined,
  WarningOutlined,
  DashboardOutlined,
  WifiOutlined,
} from "@ant-design/icons";
import { useRouter } from "next/navigation";
import { useDaemonHealth } from "@/hooks/useDaemonHealth";
import { useWSEventStore } from "@/stores/ws-events";
import { formatUptime } from "@/lib/utils/formatters";
import { PageHeader } from "@/components/common/PageHeader";
import { StatCard } from "@/components/common/StatCard";

const { Text, Paragraph } = Typography;

export default function OverviewPage() {
  const { data: health, isLoading, error } = useDaemonHealth(5000);
  const events = useWSEventStore((s) => s.events);
  const wsConnected = useWSEventStore((s) => s.connected);
  const router = useRouter();

  if (isLoading) {
    return (
      <div style={{ textAlign: "center", padding: 100 }}>
        <Spin size="large" />
        <Paragraph style={{ marginTop: 16 }} type="secondary">
          Connecting to daemon...
        </Paragraph>
      </div>
    );
  }

  if (error || !health) {
    return (
      <Alert
        type="error"
        message="Unable to connect to daemon"
        description={error instanceof Error ? error.message : "Connection refused. Is the daemon running?"}
        showIcon
        icon={<CloseCircleOutlined />}
        style={{ maxWidth: 600, margin: "80px auto" }}
        action={
          <Button onClick={() => window.location.reload()}>Retry</Button>
        }
      />
    );
  }

  const recentEvents = events.slice(-30).reverse();
  const failedModules = Object.entries(health.modules?.failed ?? {});
  const totalModules = health.modules_loaded + health.modules_failed;
  const healthPercent = totalModules > 0 ? Math.round((health.modules_loaded / totalModules) * 100) : 100;

  return (
    <Space direction="vertical" size="large" style={{ width: "100%" }}>
      <PageHeader
        icon={<DashboardOutlined />}
        title="Dashboard"
        subtitle={`LLMOS Bridge v${health.version} -- Protocol ${health.protocol_version} -- Uptime: ${formatUptime(health.uptime_seconds)}`}
        tags={
          health.status === "ok" ? (
            <Tag color="success" icon={<CheckCircleOutlined />}>Healthy</Tag>
          ) : (
            <Tag color="error" icon={<CloseCircleOutlined />}>Degraded</Tag>
          )
        }
        extra={
          <Space>
            {wsConnected ? (
              <Tag color="success" icon={<WifiOutlined />}>WS Connected</Tag>
            ) : (
              <Tag color="error" icon={<WifiOutlined />}>WS Disconnected</Tag>
            )}
          </Space>
        }
      />

      {/* Health Stats Row */}
      <Row gutter={[16, 16]}>
        <Col xs={24} sm={12} lg={6}>
          <StatCard
            title="Daemon Status"
            value={health.status === "ok" ? "Healthy" : health.status}
            prefix={
              health.status === "ok" ? (
                <CheckCircleOutlined />
              ) : (
                <CloseCircleOutlined />
              )
            }
            valueStyle={{ color: health.status === "ok" ? "#52c41a" : "#ff4d4f" }}
            color={health.status === "ok" ? "#52c41a" : "#ff4d4f"}
            onClick={() => router.push("/system")}
            footer={
              <Text type="secondary" style={{ fontSize: 12 }}>
                <ClockCircleOutlined style={{ marginRight: 4 }} />
                Uptime: {formatUptime(health.uptime_seconds)}
              </Text>
            }
          />
        </Col>
        <Col xs={24} sm={12} lg={6}>
          <StatCard
            title="Modules"
            value={health.modules_loaded}
            suffix={`/ ${totalModules}`}
            prefix={<AppstoreOutlined />}
            valueStyle={{ color: health.modules_failed > 0 ? "#faad14" : "#52c41a" }}
            color={health.modules_failed > 0 ? "#faad14" : "#52c41a"}
            onClick={() => router.push("/modules")}
            footer={
              <Progress
                percent={healthPercent}
                size="small"
                status={health.modules_failed > 0 ? "exception" : "success"}
                showInfo={false}
              />
            }
          />
        </Col>
        <Col xs={24} sm={12} lg={6}>
          <StatCard
            title="Active Plans"
            value={health.active_plans}
            prefix={<FileTextOutlined />}
            valueStyle={{ color: health.active_plans > 0 ? "#1677ff" : undefined }}
            color="#1677ff"
            onClick={() => router.push("/plans")}
            footer={
              <Text type="secondary" style={{ fontSize: 12 }}>
                Click to view all plans
              </Text>
            }
          />
        </Col>
        <Col xs={24} sm={12} lg={6}>
          <StatCard
            title="WebSocket"
            value={wsConnected ? "Connected" : "Disconnected"}
            prefix={<WifiOutlined />}
            valueStyle={{ color: wsConnected ? "#52c41a" : "#ff4d4f", fontSize: 18 }}
            color={wsConnected ? "#52c41a" : "#ff4d4f"}
            footer={
              <Text type="secondary" style={{ fontSize: 12 }}>
                <ThunderboltOutlined style={{ marginRight: 4 }} />
                {events.length} events received
              </Text>
            }
          />
        </Col>
      </Row>

      {/* Failed Modules Alert */}
      {failedModules.length > 0 && (
        <Alert
          type="warning"
          showIcon
          icon={<WarningOutlined />}
          message={`${failedModules.length} module(s) failed to load`}
          description={
            <div style={{ marginTop: 8 }}>
              {failedModules.map(([modId, reason]) => (
                <div key={modId} style={{ marginBottom: 4 }}>
                  <Tag color="error">{modId}</Tag>
                  <Text type="secondary" style={{ fontSize: 12 }}>{reason}</Text>
                </div>
              ))}
            </div>
          }
        />
      )}

      <Row gutter={[16, 16]}>
        {/* Available Modules */}
        <Col xs={24} lg={12}>
          <Card
            title={
              <Space>
                <AppstoreOutlined />
                <span>Loaded Modules</span>
              </Space>
            }
            extra={<Tag color="success">{health.modules_loaded} active</Tag>}
          >
            <div style={{ display: "flex", flexWrap: "wrap", gap: 6 }}>
              {(health.modules?.available ?? []).map((modId) => (
                <Tooltip key={modId} title={`Click to view ${modId}`}>
                  <Tag
                    color="blue"
                    style={{ cursor: "pointer", marginBottom: 4, borderRadius: 6 }}
                    onClick={() => router.push(`/modules/${modId}`)}
                  >
                    {modId}
                  </Tag>
                </Tooltip>
              ))}
            </div>
            {failedModules.length > 0 && (
              <>
                <Divider style={{ margin: "12px 0" }} />
                <Text type="secondary" style={{ fontSize: 12, display: "block", marginBottom: 6 }}>
                  Failed:
                </Text>
                <div style={{ display: "flex", flexWrap: "wrap", gap: 6 }}>
                  {failedModules.map(([modId]) => (
                    <Tooltip key={modId} title={health.modules.failed[modId]}>
                      <Tag color="error" style={{ borderRadius: 6 }}>{modId}</Tag>
                    </Tooltip>
                  ))}
                </div>
              </>
            )}
          </Card>
        </Col>

        {/* Quick Actions & System Info */}
        <Col xs={24} lg={12}>
          <Card
            title={
              <Space>
                <SettingOutlined />
                <span>System Info & Actions</span>
              </Space>
            }
          >
            <Space direction="vertical" size="middle" style={{ width: "100%" }}>
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
                <Text type="secondary">Version</Text>
                <Tag color="blue">v{health.version}</Tag>
              </div>
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
                <Text type="secondary">Protocol</Text>
                <Tag>{health.protocol_version}</Tag>
              </div>
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
                <Text type="secondary">Scanner Pipeline</Text>
                {typeof health.scanner_pipeline === "object" && health.scanner_pipeline?.error ? (
                  <Tooltip title={String(health.scanner_pipeline.error)}>
                    <Tag color="default">Inactive</Tag>
                  </Tooltip>
                ) : (
                  <Tag color="success">Active</Tag>
                )}
              </div>
              <Divider style={{ margin: "4px 0" }} />
              <Space wrap>
                <Button type="primary" icon={<FileTextOutlined />} onClick={() => router.push("/plans")}>
                  Plans
                </Button>
                <Button icon={<AppstoreOutlined />} onClick={() => router.push("/modules")}>
                  Modules
                </Button>
                <Button icon={<SafetyOutlined />} onClick={() => router.push("/security")}>
                  Security
                </Button>
                <Button icon={<SettingOutlined />} onClick={() => router.push("/system/config")}>
                  Config
                </Button>
              </Space>
            </Space>
          </Card>
        </Col>
      </Row>

      {/* Recent Events */}
      <Card
        title={
          <Space>
            <ThunderboltOutlined />
            <span>Live Event Stream</span>
          </Space>
        }
        extra={
          <Space>
            {wsConnected ? (
              <Badge status="processing" text={<Text type="secondary" style={{ fontSize: 12 }}>Live</Text>} />
            ) : (
              <Badge status="error" text={<Text type="secondary" style={{ fontSize: 12 }}>Offline</Text>} />
            )}
            <Tag>{recentEvents.length} events</Tag>
          </Space>
        }
      >
        {recentEvents.length === 0 ? (
          <div style={{ textAlign: "center", padding: 32 }}>
            <WifiOutlined style={{ fontSize: 32, color: "#d9d9d9" }} />
            <Paragraph type="secondary" style={{ marginTop: 8 }}>
              No events yet. Events will appear here in real-time via WebSocket.
            </Paragraph>
          </div>
        ) : (
          <List
            size="small"
            dataSource={recentEvents.slice(0, 15)}
            renderItem={(event) => (
              <List.Item style={{ padding: "6px 0" }}>
                <Space>
                  <Tag color="blue" style={{ fontSize: 11, minWidth: 100, textAlign: "center" }}>
                    {event.type}
                  </Tag>
                  <Tooltip title={JSON.stringify(event.payload, null, 2)}>
                    <Text style={{ fontSize: 12 }} ellipsis>
                      {JSON.stringify(event.payload).slice(0, 100)}
                    </Text>
                  </Tooltip>
                </Space>
                <Tooltip title={new Date(event.timestamp).toLocaleString()}>
                  <Text type="secondary" style={{ fontSize: 11 }}>
                    {new Date(event.timestamp).toLocaleTimeString()}
                  </Text>
                </Tooltip>
              </List.Item>
            )}
          />
        )}
      </Card>
    </Space>
  );
}
