"use client";

import React from "react";
import {
  Card,
  Row,
  Col,
  Descriptions,
  Tag,
  Space,
  Spin,
  Typography,
  Tooltip,
} from "antd";
import {
  CheckCircleOutlined,
  CloseCircleOutlined,
  SafetyCertificateOutlined,
  LinkOutlined,
  GlobalOutlined,
  LaptopOutlined,
  ApiOutlined,
  LockOutlined,
} from "@ant-design/icons";
import type { UseModuleDetailReturn } from "@/hooks/useModuleDetail";
import type { ModuleState, SandboxLevel } from "@/types/module";
import { StatCard } from "@/components/common/StatCard";
import { LifecycleControl } from "./LifecycleControl";

const { Text, Link: AntLink } = Typography;

// ── Color maps ──

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
  starting: "Starting",
  active: "Active",
  paused: "Paused",
  stopping: "Stopping",
  disabled: "Disabled",
  error: "Error",
};

const sandboxColorMap: Record<SandboxLevel, string> = {
  none: "default",
  basic: "blue",
  strict: "orange",
  isolated: "red",
};

const platformColorMap: Record<string, string> = {
  linux: "green",
  windows: "blue",
  macos: "purple",
  all: "default",
};

// ── Component ──

interface OverviewTabProps {
  hook: UseModuleDetailReturn;
}

export function OverviewTab({ hook }: OverviewTabProps) {
  const { manifest, info, health } = hook;

  const manifestData = manifest.data;
  const infoData = info.data;
  const healthData = health.data;

  const isHealthy = healthData?.healthy ?? false;
  const currentState: ModuleState = infoData?.state ?? "loaded";
  const moduleType = infoData?.type ?? manifestData?.module_type ?? "user";
  const actionsCount = manifestData?.actions?.length ?? 0;

  return (
    <Space direction="vertical" size="middle" style={{ width: "100%" }}>
      {/* ── Stat Cards Row ── */}
      <Spin spinning={health.isLoading && manifest.isLoading && info.isLoading}>
        <Row gutter={[16, 16]}>
          <Col xs={24} sm={12} lg={6}>
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
              color={isHealthy ? "#52c41a" : "#ff4d4f"}
              valueStyle={{ color: isHealthy ? "#52c41a" : "#ff4d4f" }}
            />
          </Col>
          <Col xs={24} sm={12} lg={6}>
            <StatCard
              title="Module State"
              value={stateLabelMap[currentState] ?? currentState}
              color={stateColorMap[currentState] ?? "#8c8c8c"}
              valueStyle={{ color: stateColorMap[currentState] ?? "#8c8c8c" }}
            />
          </Col>
          <Col xs={24} sm={12} lg={6}>
            <StatCard
              title="Actions Count"
              value={actionsCount}
              color="#1677ff"
              valueStyle={{ color: "#1677ff" }}
            />
          </Col>
          <Col xs={24} sm={12} lg={6}>
            <StatCard
              title="Module Type"
              value={moduleType}
              color="#722ed1"
              valueStyle={{ color: "#722ed1", textTransform: "capitalize" }}
            />
          </Col>
        </Row>
      </Spin>

      {/* ── Lifecycle Control ── */}
      {infoData ? (
        <LifecycleControl
          state={infoData.state}
          moduleId={infoData.module_id}
          hook={hook}
        />
      ) : (
        <Card>
          <Spin tip="Loading lifecycle state..." />
        </Card>
      )}

      {/* ── Module Identity Card ── */}
      {manifestData ? (
        <Card
          title={
            <Space>
              <SafetyCertificateOutlined />
              <span>Module Identity</span>
            </Space>
          }
        >
          <Descriptions bordered size="small" column={{ xs: 1, sm: 2 }}>
            <Descriptions.Item label="Module ID">
              <Text code>{manifestData.module_id}</Text>
            </Descriptions.Item>
            <Descriptions.Item label="Version">
              <Tag color="blue">v{manifestData.version}</Tag>
            </Descriptions.Item>
            <Descriptions.Item label="Author">
              {manifestData.author ? (
                <Text>{manifestData.author}</Text>
              ) : (
                <Text type="secondary">&mdash;</Text>
              )}
            </Descriptions.Item>
            <Descriptions.Item label="Homepage">
              {manifestData.homepage ? (
                <AntLink href={manifestData.homepage} target="_blank">
                  <GlobalOutlined style={{ marginRight: 4 }} />
                  {manifestData.homepage}
                </AntLink>
              ) : (
                <Text type="secondary">&mdash;</Text>
              )}
            </Descriptions.Item>
            <Descriptions.Item label="License">
              {manifestData.license ? (
                <Tag>{manifestData.license}</Tag>
              ) : (
                <Text type="secondary">&mdash;</Text>
              )}
            </Descriptions.Item>
            <Descriptions.Item label="Sandbox Level">
              <Tag
                color={
                  sandboxColorMap[manifestData.sandbox_level ?? "none"] ??
                  "default"
                }
              >
                {manifestData.sandbox_level ?? "none"}
              </Tag>
            </Descriptions.Item>
            <Descriptions.Item label="Signed" span={2}>
              {manifestData.signing ? (
                <Tooltip
                  title={`Fingerprint: ${manifestData.signing.public_key_fingerprint}`}
                >
                  <Tag color="green" icon={<CheckCircleOutlined />}>
                    Verified
                  </Tag>
                  <Text
                    type="secondary"
                    style={{ fontSize: 12, marginLeft: 8 }}
                  >
                    Signed at{" "}
                    {new Date(manifestData.signing.signed_at).toLocaleString()}
                  </Text>
                </Tooltip>
              ) : (
                <Text type="secondary">Not signed</Text>
              )}
            </Descriptions.Item>
          </Descriptions>
        </Card>
      ) : manifest.isLoading ? (
        <Card>
          <div style={{ textAlign: "center", padding: 32 }}>
            <Spin tip="Loading module identity..." />
          </div>
        </Card>
      ) : null}

      {/* ── Quick Info Row ── */}
      {manifestData ? (
        <Row gutter={[16, 16]}>
          {/* Platforms */}
          <Col xs={24} sm={12} lg={6}>
            <Card
              title={
                <Space>
                  <LaptopOutlined style={{ color: "#1677ff" }} />
                  <span>Platforms</span>
                </Space>
              }
              style={{ height: "100%" }}
              size="small"
            >
              <Space direction="vertical" size="small">
                {manifestData.platforms.length > 0 ? (
                  manifestData.platforms.map((p) => (
                    <Tag
                      key={p}
                      color={platformColorMap[p.toLowerCase()] ?? "default"}
                      style={{ margin: 0 }}
                    >
                      {p}
                    </Tag>
                  ))
                ) : (
                  <Text type="secondary" italic>
                    None
                  </Text>
                )}
              </Space>
            </Card>
          </Col>

          {/* Declared Permissions */}
          <Col xs={24} sm={12} lg={6}>
            <Card
              title={
                <Space>
                  <LockOutlined style={{ color: "#faad14" }} />
                  <span>Declared Permissions</span>
                </Space>
              }
              style={{ height: "100%" }}
              size="small"
            >
              <Space direction="vertical" size="small">
                {manifestData.declared_permissions.length > 0 ? (
                  manifestData.declared_permissions.map((p) => (
                    <Tag key={p} color="orange" style={{ margin: 0 }}>
                      {p}
                    </Tag>
                  ))
                ) : (
                  <Text type="secondary" italic>
                    None
                  </Text>
                )}
              </Space>
            </Card>
          </Col>

          {/* Services */}
          <Col xs={24} sm={12} lg={6}>
            <Card
              title={
                <Space>
                  <ApiOutlined style={{ color: "#13c2c2" }} />
                  <span>Services</span>
                </Space>
              }
              style={{ height: "100%" }}
              size="small"
            >
              <Space direction="vertical" size="small">
                {(manifestData.services ?? []).length > 0 ? (
                  (manifestData.services ?? []).map((s) => (
                    <Tag key={s} color="cyan" style={{ margin: 0 }}>
                      {s}
                    </Tag>
                  ))
                ) : (
                  <Text type="secondary" italic>
                    None
                  </Text>
                )}
              </Space>
            </Card>
          </Col>

          {/* Dependencies */}
          <Col xs={24} sm={12} lg={6}>
            <Card
              title={
                <Space>
                  <LinkOutlined style={{ color: "#722ed1" }} />
                  <span>Dependencies</span>
                </Space>
              }
              style={{ height: "100%" }}
              size="small"
            >
              <Space direction="vertical" size="small">
                {getDependencyEntries(manifestData.dependencies).length > 0 ? (
                  getDependencyEntries(manifestData.dependencies).map((dep) => (
                    <Tag key={dep} color="purple" style={{ margin: 0 }}>
                      {dep}
                    </Tag>
                  ))
                ) : (
                  <Text type="secondary" italic>
                    None
                  </Text>
                )}
              </Space>
            </Card>
          </Col>
        </Row>
      ) : manifest.isLoading ? (
        <Row gutter={[16, 16]}>
          {[1, 2, 3, 4].map((i) => (
            <Col xs={24} sm={12} lg={6} key={i}>
              <Card size="small">
                <div style={{ textAlign: "center", padding: 24 }}>
                  <Spin />
                </div>
              </Card>
            </Col>
          ))}
        </Row>
      ) : null}
    </Space>
  );
}

// ── Helpers ──

function getDependencyEntries(
  deps: string[] | Record<string, string> | undefined,
): string[] {
  if (!deps) return [];
  if (Array.isArray(deps)) return deps;
  return Object.keys(deps);
}
