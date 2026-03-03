"use client";

import React from "react";
import { useQuery } from "@tanstack/react-query";
import {
  Card,
  Row,
  Col,
  Typography,
  Space,
  Tag,
  Spin,
  Alert,
  Descriptions,
  List,
  Badge,
  Button,
  Tooltip,
} from "antd";
import {
  SettingOutlined,
  CheckCircleOutlined,
  CloseCircleOutlined,
  ClockCircleOutlined,
  AppstoreOutlined,
  FileTextOutlined,
  ReloadOutlined,
  WarningOutlined,
} from "@ant-design/icons";
import { useRouter } from "next/navigation";
import { api, ApiError } from "@/lib/api/client";
import { PageHeader } from "@/components/common/PageHeader";
import { StatCard } from "@/components/common/StatCard";
import { formatUptime } from "@/lib/utils/formatters";
import type { HealthResponse, SystemStatus } from "@/types/events";

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

const stateColorMap: Record<string, string> = {
  active: "green",
  stopped: "default",
  error: "red",
  starting: "processing",
  paused: "warning",
};

export default function SystemPage() {
  const router = useRouter();

  const {
    data: health,
    isLoading: healthLoading,
    error: healthError,
    refetch: refetchHealth,
  } = useQuery<HealthResponse>({
    queryKey: ["system-health"],
    queryFn: () => api.get<HealthResponse>("/health"),
    retry: false,
    refetchInterval: 15000,
  });

  const {
    data: systemStatus,
    isLoading: statusLoading,
    error: statusError,
  } = useQuery<SystemStatus>({
    queryKey: ["system-status"],
    queryFn: () => api.get<SystemStatus>("/admin/system/status"),
    retry: false,
    refetchInterval: 15000,
  });

  if (healthLoading && statusLoading) {
    return (
      <div style={{ textAlign: "center", padding: 80 }}>
        <Spin size="large" />
      </div>
    );
  }

  if (healthError && statusError) {
    return (
      <Alert
        type="error"
        message="Failed to load system status"
        description={getErrorMessage(healthError)}
        showIcon
      />
    );
  }

  const failedModules = Object.entries(systemStatus?.failed ?? {});
  const healthEntries = Object.entries(systemStatus?.health ?? {});
  const isHealthy = health?.status === "healthy";

  return (
    <Space direction="vertical" size="large" style={{ width: "100%" }}>
      <PageHeader
        icon={<SettingOutlined />}
        title="System Status"
        subtitle="Daemon health and module overview"
        tags={
          health ? (
            <Tag
              color={isHealthy ? "green" : "red"}
              icon={
                isHealthy ? (
                  <CheckCircleOutlined />
                ) : (
                  <CloseCircleOutlined />
                )
              }
            >
              {health.status}
            </Tag>
          ) : undefined
        }
        extra={
          <Space>
            <Button
              onClick={() => router.push("/system/config")}
              icon={<FileTextOutlined />}
            >
              View Config
            </Button>
            <Button
              icon={<ReloadOutlined />}
              onClick={() => refetchHealth()}
            >
              Refresh
            </Button>
          </Space>
        }
      />

      {/* Health Stats */}
      {health && (
        <Row gutter={[16, 16]}>
          <Col xs={24} sm={12} lg={6}>
            <StatCard
              title="Status"
              value={health.status}
              prefix={
                isHealthy ? (
                  <CheckCircleOutlined style={{ color: "#52c41a" }} />
                ) : (
                  <CloseCircleOutlined style={{ color: "#ff4d4f" }} />
                )
              }
              color={isHealthy ? "#52c41a" : "#ff4d4f"}
              valueStyle={{ textTransform: "capitalize" as const }}
              footer={
                <Text type="secondary" style={{ fontSize: 12 }}>
                  v{health.version}
                </Text>
              }
            />
          </Col>
          <Col xs={24} sm={12} lg={6}>
            <StatCard
              title="Uptime"
              value={formatUptime(health.uptime_seconds)}
              prefix={<ClockCircleOutlined />}
              color="#1677ff"
              footer={
                <Text type="secondary" style={{ fontSize: 12 }}>
                  Since last restart
                </Text>
              }
            />
          </Col>
          <Col xs={24} sm={12} lg={6}>
            <StatCard
              title="Modules"
              value={health.modules_loaded}
              prefix={<AppstoreOutlined />}
              color="#722ed1"
              footer={
                health.modules_failed > 0 ? (
                  <Text type="danger" style={{ fontSize: 12 }}>
                    <WarningOutlined /> {health.modules_failed} failed
                  </Text>
                ) : (
                  <Text type="secondary" style={{ fontSize: 12 }}>
                    All modules healthy
                  </Text>
                )
              }
            />
          </Col>
          <Col xs={24} sm={12} lg={6}>
            <StatCard
              title="Active Plans"
              value={health.active_plans}
              prefix={<FileTextOutlined />}
              color="#fa8c16"
              footer={
                <Text type="secondary" style={{ fontSize: 12 }}>
                  Currently executing
                </Text>
              }
            />
          </Col>
        </Row>
      )}

      {/* System Info */}
      {health && (
        <Card
          title={
            <Space>
              <SettingOutlined />
              <span>System Information</span>
            </Space>
          }
          extra={
            <Tag color="blue" style={{ borderRadius: 4 }}>
              v{health.version}
            </Tag>
          }
        >
          <Descriptions
            column={{ xs: 1, sm: 2, lg: 3 }}
            bordered
            size="small"
          >
            <Descriptions.Item label="Version">
              <Text strong>{health.version}</Text>
            </Descriptions.Item>
            <Descriptions.Item label="Protocol Version">
              <Tag color="geekblue" style={{ borderRadius: 4 }}>
                {health.protocol_version}
              </Tag>
            </Descriptions.Item>
            <Descriptions.Item label="Uptime">
              <Space>
                <ClockCircleOutlined />
                <Text>{formatUptime(health.uptime_seconds)}</Text>
              </Space>
            </Descriptions.Item>
            <Descriptions.Item label="Modules Loaded">
              <Tag color="green">{health.modules_loaded}</Tag>
            </Descriptions.Item>
            <Descriptions.Item label="Modules Failed">
              <Tag color={health.modules_failed > 0 ? "red" : "green"}>
                {health.modules_failed}
              </Tag>
            </Descriptions.Item>
            <Descriptions.Item label="Scanner Pipeline">
              <Space wrap>
                {Object.entries(health.scanner_pipeline ?? {}).map(
                  ([name, enabled]) => (
                    <Tag
                      key={name}
                      color={enabled ? "success" : "default"}
                      style={{ borderRadius: 4 }}
                    >
                      {name}
                    </Tag>
                  ),
                )}
              </Space>
            </Descriptions.Item>
          </Descriptions>
        </Card>
      )}

      {/* Module Status from /admin/system/status */}
      {systemStatus && (
        <Row gutter={[16, 16]}>
          <Col xs={24} lg={12}>
            <Card
              title={
                <Space>
                  <AppstoreOutlined />
                  <span>Module Summary</span>
                </Space>
              }
              extra={
                <Tag color="blue">{systemStatus.total_modules} total</Tag>
              }
            >
              <Descriptions column={{ xs: 1, sm: 2 }} bordered size="small">
                <Descriptions.Item label="Total Modules">
                  <Text strong style={{ fontSize: 16 }}>
                    {systemStatus.total_modules}
                  </Text>
                </Descriptions.Item>
                {Object.entries(systemStatus.by_state).map(([state, count]) => (
                  <Descriptions.Item key={state} label={state}>
                    <Space>
                      <Badge
                        status={
                          state === "active"
                            ? "success"
                            : state === "error"
                            ? "error"
                            : "default"
                        }
                      />
                      <Tag
                        color={stateColorMap[state] ?? "default"}
                        style={{ borderRadius: 4 }}
                      >
                        {count}
                      </Tag>
                    </Space>
                  </Descriptions.Item>
                ))}
                {Object.entries(systemStatus.by_type).map(([type, count]) => (
                  <Descriptions.Item key={type} label={`Type: ${type}`}>
                    <Tag style={{ borderRadius: 4 }}>{count}</Tag>
                  </Descriptions.Item>
                ))}
              </Descriptions>
            </Card>
          </Col>

          <Col xs={24} lg={12}>
            <Card
              title={
                <Space>
                  <CheckCircleOutlined />
                  <span>Module Health</span>
                </Space>
              }
              extra={
                <Text type="secondary" style={{ fontSize: 12 }}>
                  {healthEntries.length} modules
                </Text>
              }
              loading={statusLoading}
              styles={{
                body: {
                  padding: healthEntries.length === 0 ? "24px" : 0,
                  maxHeight: 400,
                  overflowY: "auto" as const,
                },
              }}
            >
              {healthEntries.length === 0 ? (
                <Text type="secondary">No health data available.</Text>
              ) : (
                <List
                  dataSource={healthEntries}
                  renderItem={([moduleId, info]) => (
                    <List.Item style={{ padding: "12px 24px" }}>
                      <List.Item.Meta
                        avatar={
                          <div
                            style={{
                              width: 36,
                              height: 36,
                              borderRadius: 8,
                              background:
                                info.status === "ok"
                                  ? "rgba(82, 196, 26, 0.1)"
                                  : "rgba(255, 77, 79, 0.1)",
                              display: "flex",
                              alignItems: "center",
                              justifyContent: "center",
                            }}
                          >
                            {info.status === "ok" ? (
                              <CheckCircleOutlined
                                style={{ color: "#52c41a", fontSize: 16 }}
                              />
                            ) : (
                              <CloseCircleOutlined
                                style={{ color: "#ff4d4f", fontSize: 16 }}
                              />
                            )}
                          </div>
                        }
                        title={
                          <Text strong>{info.module_id}</Text>
                        }
                        description={
                          <Space size={4}>
                            <Tag style={{ fontSize: 10, borderRadius: 4 }}>
                              v{info.version}
                            </Tag>
                          </Space>
                        }
                      />
                      <Tag
                        color={info.status === "ok" ? "green" : "red"}
                        style={{ borderRadius: 4 }}
                      >
                        {info.status}
                      </Tag>
                    </List.Item>
                  )}
                />
              )}
            </Card>
          </Col>
        </Row>
      )}

      {/* Failed Modules */}
      {failedModules.length > 0 && (
        <Card
          title={
            <Space>
              <WarningOutlined style={{ color: "#ff4d4f" }} />
              <span>Failed Modules</span>
            </Space>
          }
          extra={
            <Tag color="red">{failedModules.length} failed</Tag>
          }
          styles={{ body: { padding: 0 } }}
        >
          <List
            dataSource={failedModules}
            renderItem={([moduleId, reason]) => (
              <List.Item style={{ padding: "12px 24px" }}>
                <List.Item.Meta
                  avatar={
                    <div
                      style={{
                        width: 36,
                        height: 36,
                        borderRadius: 8,
                        background: "rgba(255, 77, 79, 0.1)",
                        display: "flex",
                        alignItems: "center",
                        justifyContent: "center",
                      }}
                    >
                      <CloseCircleOutlined
                        style={{ color: "#ff4d4f", fontSize: 16 }}
                      />
                    </div>
                  }
                  title={
                    <Text strong>{moduleId}</Text>
                  }
                  description={
                    <Tooltip title={reason as string}>
                      <Text type="danger" style={{ fontSize: 12 }}>
                        {reason as string}
                      </Text>
                    </Tooltip>
                  }
                />
                <Tag color="red" style={{ borderRadius: 4 }}>
                  Failed
                </Tag>
              </List.Item>
            )}
          />
        </Card>
      )}
    </Space>
  );
}
