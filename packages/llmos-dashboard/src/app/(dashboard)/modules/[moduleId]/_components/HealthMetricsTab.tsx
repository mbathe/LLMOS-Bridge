"use client";

import React from "react";
import { Card, Row, Col, Space, Button, Typography, Descriptions, Spin } from "antd";
import {
  ReloadOutlined,
  CheckCircleOutlined,
  CloseCircleOutlined,
} from "@ant-design/icons";
import type { UseModuleDetailReturn } from "@/hooks/useModuleDetail";
import { StatCard } from "@/components/common/StatCard";
import { JsonViewer } from "@/components/common/JsonViewer";
import { EmptyState } from "@/components/common/EmptyState";

const { Text } = Typography;

interface HealthMetricsTabProps {
  hook: UseModuleDetailReturn;
}

function formatCheckedAt(iso: string | undefined): { time: string; date: string } {
  if (!iso) return { time: "--", date: "--" };
  try {
    const d = new Date(iso);
    return {
      time: d.toLocaleTimeString(),
      date: d.toLocaleDateString(),
    };
  } catch {
    return { time: iso, date: "" };
  }
}

function isNonEmptyObject(val: unknown): val is Record<string, unknown> {
  return !!val && typeof val === "object" && !Array.isArray(val) && Object.keys(val).length > 0;
}

function isNumeric(val: unknown): val is number {
  return typeof val === "number";
}

export function HealthMetricsTab({ hook }: HealthMetricsTabProps) {
  const healthData = hook.health.data;
  const metricsData = hook.metrics.data;
  const stateData = hook.stateSnapshot.data;
  const describeData = hook.describe.data;

  const isHealthy = healthData?.healthy ?? false;
  const checkedAt = formatCheckedAt(healthData?.checked_at);
  const version =
    healthData?.module_id ?? hook.info.data?.version ?? "--";

  const healthDetails = healthData?.details;
  const hasHealthDetails = isNonEmptyObject(healthDetails);

  const metrics = metricsData?.metrics;
  const hasMetrics = isNonEmptyObject(metrics);

  const stateSnapshot = stateData?.state_snapshot;
  const hasState = isNonEmptyObject(stateSnapshot);

  const hasDescription =
    describeData &&
    Object.keys(describeData).filter((k) => k !== "module_id").length > 0;

  return (
    <Space direction="vertical" size="large" style={{ width: "100%" }}>
      {/* Health Section */}
      <Row gutter={[16, 16]}>
        <Col xs={24} sm={8}>
          <StatCard
            title="Health Status"
            value={isHealthy ? "Healthy" : "Unhealthy"}
            prefix={
              isHealthy ? (
                <CheckCircleOutlined style={{ color: "#52c41a" }} />
              ) : (
                <CloseCircleOutlined style={{ color: "#ff4d4f" }} />
              )
            }
            valueStyle={{ color: isHealthy ? "#52c41a" : "#ff4d4f" }}
            color={isHealthy ? "#52c41a" : "#ff4d4f"}
          />
        </Col>
        <Col xs={24} sm={8}>
          <StatCard
            title="Version"
            value={hook.info.data?.version ?? "--"}
            color="#1677ff"
          />
        </Col>
        <Col xs={24} sm={8}>
          <StatCard
            title="Last Checked"
            value={checkedAt.time}
            footer={
              <Text type="secondary" style={{ fontSize: 12 }}>
                {checkedAt.date}
              </Text>
            }
          />
        </Col>
      </Row>

      {/* Health Details */}
      <Card
        title="Health Details"
        extra={
          <Button
            icon={<ReloadOutlined />}
            size="small"
            onClick={() => hook.health.refetch()}
            loading={hook.health.isRefetching}
          >
            Refresh
          </Button>
        }
      >
        {hasHealthDetails ? (
          <JsonViewer data={healthDetails} />
        ) : (
          <Text type="secondary">No additional health details reported.</Text>
        )}
      </Card>

      {/* Metrics Section */}
      <Card
        title="Operational Metrics"
        extra={
          <Button
            icon={<ReloadOutlined />}
            size="small"
            onClick={() => hook.metrics.refetch()}
            loading={hook.metrics.isRefetching}
          >
            Refresh
          </Button>
        }
        loading={hook.metrics.isLoading}
      >
        {hasMetrics ? (
          <>
            {/* Numeric metrics as descriptions */}
            {(() => {
              const numericEntries = Object.entries(metrics!).filter(([, v]) =>
                isNumeric(v),
              );
              const complexEntries = Object.entries(metrics!).filter(
                ([, v]) => !isNumeric(v),
              );

              return (
                <Space direction="vertical" size="middle" style={{ width: "100%" }}>
                  {numericEntries.length > 0 && (
                    <Descriptions bordered size="small" column={{ xs: 1, sm: 2, md: 3 }}>
                      {numericEntries.map(([key, val]) => (
                        <Descriptions.Item
                          key={key}
                          label={key.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase())}
                        >
                          <Text strong>{String(val)}</Text>
                        </Descriptions.Item>
                      ))}
                    </Descriptions>
                  )}
                  {complexEntries.length > 0 && (
                    <JsonViewer
                      data={Object.fromEntries(complexEntries)}
                      maxHeight={300}
                    />
                  )}
                </Space>
              );
            })()}
          </>
        ) : (
          <EmptyState description="No metrics reported by this module." />
        )}
      </Card>

      {/* State Snapshot */}
      <Card
        title="State Snapshot"
        extra={
          <Button
            icon={<ReloadOutlined />}
            size="small"
            onClick={() => hook.stateSnapshot.refetch()}
            loading={hook.stateSnapshot.isRefetching}
          >
            Refresh
          </Button>
        }
      >
        {hasState ? (
          <JsonViewer data={stateSnapshot} />
        ) : (
          <EmptyState description="No state data available." />
        )}
      </Card>

      {/* Self-Description */}
      <Card title="Module Self-Description">
        {hasDescription ? (
          <JsonViewer data={describeData} />
        ) : (
          <EmptyState description="No dynamic description available." />
        )}
      </Card>
    </Space>
  );
}
