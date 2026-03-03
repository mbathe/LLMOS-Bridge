"use client";

import React, { useState } from "react";
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
  Table,
  Button,
  Input,
  Form,
  Popconfirm,
  Tooltip,
  Badge,
  message,
} from "antd";
import {
  ClusterOutlined,
  CheckCircleOutlined,
  CloseCircleOutlined,
  ReloadOutlined,
  HeartOutlined,
  DeleteOutlined,
  PlusOutlined,
  EnvironmentOutlined,
  DashboardOutlined,
  BranchesOutlined,
  SwapOutlined,
  LinkOutlined,
} from "@ant-design/icons";
import { useRouter } from "next/navigation";
import { ApiError } from "@/lib/api/client";
import { PageHeader } from "@/components/common/PageHeader";
import { StatCard } from "@/components/common/StatCard";
import { useCluster } from "@/hooks/useCluster";
import { timeAgo } from "@/lib/utils/formatters";
import type { NodeResponse, NodeRegisterRequest } from "@/types/cluster";
import type { ColumnsType } from "antd/es/table";

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

const modeColors: Record<string, string> = {
  standalone: "default",
  orchestrator: "blue",
  node: "green",
};

export default function ClusterPage() {
  const router = useRouter();
  const [form] = Form.useForm<NodeRegisterRequest>();
  const [showRegister, setShowRegister] = useState(false);

  const {
    clusterInfo,
    clusterHealth,
    nodes,
    routingConfig,
    registerNode,
    unregisterNode,
    triggerHeartbeat,
  } = useCluster();

  const info = clusterInfo.data;
  const health = clusterHealth.data;
  const isLoading = clusterInfo.isLoading && clusterHealth.isLoading;
  const error = clusterInfo.error && clusterHealth.error;

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
        message="Failed to load cluster information"
        description={getErrorMessage(clusterInfo.error)}
        showIcon
      />
    );
  }

  const handleRegister = async (values: NodeRegisterRequest) => {
    try {
      await registerNode.mutateAsync(values);
      message.success(`Node '${values.node_id}' registered successfully`);
      form.resetFields();
      setShowRegister(false);
    } catch (err) {
      message.error(getErrorMessage(err));
    }
  };

  const handleUnregister = async (nodeId: string) => {
    try {
      await unregisterNode.mutateAsync(nodeId);
      message.success(`Node '${nodeId}' unregistered`);
    } catch (err) {
      message.error(getErrorMessage(err));
    }
  };

  const handleHeartbeat = async (nodeId: string) => {
    try {
      await triggerHeartbeat.mutateAsync(nodeId);
      message.success(`Heartbeat sent to '${nodeId}'`);
    } catch (err) {
      message.error(getErrorMessage(err));
    }
  };

  const columns: ColumnsType<NodeResponse> = [
    {
      title: "Node ID",
      dataIndex: "node_id",
      key: "node_id",
      render: (id: string, record) => (
        <Space>
          <Text
            strong
            style={{ cursor: "pointer" }}
            onClick={() => router.push(`/cluster/${id}`)}
          >
            {id}
          </Text>
          {record.is_local && (
            <Tag color="blue" style={{ borderRadius: 4 }}>
              local
            </Tag>
          )}
        </Space>
      ),
    },
    {
      title: "Location",
      dataIndex: "location",
      key: "location",
      render: (loc: string) =>
        loc ? (
          <Space size={4}>
            <EnvironmentOutlined style={{ color: "#8c8c8c" }} />
            <Text>{loc}</Text>
          </Space>
        ) : (
          <Text type="secondary">—</Text>
        ),
    },
    {
      title: "Status",
      dataIndex: "available",
      key: "status",
      render: (available: boolean) => (
        <Badge
          status={available ? "success" : "error"}
          text={available ? "Available" : "Unavailable"}
        />
      ),
    },
    {
      title: "Latency",
      dataIndex: "latency_ms",
      key: "latency",
      render: (ms: number | null) =>
        ms != null ? (
          <Text>{ms.toFixed(1)} ms</Text>
        ) : (
          <Text type="secondary">—</Text>
        ),
    },
    {
      title: "Active Actions",
      dataIndex: "active_actions",
      key: "active_actions",
      render: (count: number) => (
        <Tag color={count > 0 ? "processing" : "default"} style={{ borderRadius: 4 }}>
          {count}
        </Tag>
      ),
    },
    {
      title: "Quarantined",
      dataIndex: "quarantined",
      key: "quarantined",
      render: (q: boolean) =>
        q ? (
          <Tag color="red" style={{ borderRadius: 4 }}>
            Quarantined
          </Tag>
        ) : null,
    },
    {
      title: "Modules",
      dataIndex: "modules",
      key: "modules",
      render: (modules: string[]) => {
        if (!modules || modules.length === 0) {
          return <Text type="secondary">—</Text>;
        }
        const shown = modules.slice(0, 3);
        const rest = modules.length - shown.length;
        return (
          <Space size={4} wrap>
            {shown.map((m) => (
              <Tag key={m} style={{ borderRadius: 4, fontSize: 11 }}>
                {m}
              </Tag>
            ))}
            {rest > 0 && (
              <Tooltip title={modules.slice(3).join(", ")}>
                <Tag style={{ borderRadius: 4, fontSize: 11 }}>+{rest}</Tag>
              </Tooltip>
            )}
          </Space>
        );
      },
    },
    {
      title: "Last Heartbeat",
      dataIndex: "last_heartbeat",
      key: "last_heartbeat",
      render: (ts: number | null) =>
        ts ? (
          <Tooltip title={new Date(ts * 1000).toLocaleString()}>
            <Text type="secondary" style={{ fontSize: 12 }}>
              {timeAgo(ts)}
            </Text>
          </Tooltip>
        ) : (
          <Text type="secondary">—</Text>
        ),
    },
    {
      title: "Actions",
      key: "actions",
      render: (_: unknown, record: NodeResponse) => (
        <Space size={4}>
          <Tooltip title="Trigger heartbeat">
            <Button
              size="small"
              icon={<HeartOutlined />}
              onClick={() => handleHeartbeat(record.node_id)}
              loading={triggerHeartbeat.isPending}
              disabled={record.is_local}
            />
          </Tooltip>
          {!record.is_local && (
            <Popconfirm
              title={`Unregister node '${record.node_id}'?`}
              onConfirm={() => handleUnregister(record.node_id)}
              okText="Yes"
              cancelText="No"
            >
              <Button
                size="small"
                danger
                icon={<DeleteOutlined />}
                loading={unregisterNode.isPending}
              />
            </Popconfirm>
          )}
        </Space>
      ),
    },
  ];

  return (
    <Space direction="vertical" size="large" style={{ width: "100%" }}>
      <PageHeader
        icon={<ClusterOutlined />}
        title="Cluster"
        subtitle="Multi-node topology and health"
        tags={
          info ? (
            <Tag
              color={modeColors[info.mode] ?? "default"}
              style={{ borderRadius: 4 }}
            >
              {info.mode}
            </Tag>
          ) : undefined
        }
        extra={
          <Space>
            {info?.mode !== "standalone" && (
              <Button
                icon={<PlusOutlined />}
                onClick={() => setShowRegister(!showRegister)}
              >
                Register Node
              </Button>
            )}
            <Button
              icon={<ReloadOutlined />}
              onClick={() => {
                clusterInfo.refetch();
                clusterHealth.refetch();
                nodes.refetch();
              }}
            >
              Refresh
            </Button>
          </Space>
        }
      />

      {/* Stat Cards */}
      {health && (
        <Row gutter={[16, 16]}>
          <Col xs={24} sm={12} lg={6}>
            <StatCard
              title="Total Nodes"
              value={health.total_nodes}
              prefix={<ClusterOutlined />}
              color="#1677ff"
              footer={
                <Text type="secondary" style={{ fontSize: 12 }}>
                  Registered nodes
                </Text>
              }
            />
          </Col>
          <Col xs={24} sm={12} lg={6}>
            <StatCard
              title="Available"
              value={health.available_nodes}
              prefix={<CheckCircleOutlined />}
              color="#52c41a"
              footer={
                <Text type="secondary" style={{ fontSize: 12 }}>
                  Ready for dispatch
                </Text>
              }
            />
          </Col>
          <Col xs={24} sm={12} lg={6}>
            <StatCard
              title="Unavailable"
              value={health.unavailable_nodes}
              prefix={<CloseCircleOutlined />}
              color={health.unavailable_nodes > 0 ? "#ff4d4f" : "#8c8c8c"}
              footer={
                health.unavailable_nodes > 0 ? (
                  <Text type="danger" style={{ fontSize: 12 }}>
                    Nodes unreachable
                  </Text>
                ) : (
                  <Text type="secondary" style={{ fontSize: 12 }}>
                    All nodes healthy
                  </Text>
                )
              }
            />
          </Col>
          <Col xs={24} sm={12} lg={6}>
            <StatCard
              title="Mode"
              value={info?.mode ?? "—"}
              prefix={<DashboardOutlined />}
              color="#722ed1"
              valueStyle={{ textTransform: "capitalize" as const }}
              footer={
                <Text type="secondary" style={{ fontSize: 12 }}>
                  Cluster: {info?.cluster_name ?? "—"}
                </Text>
              }
            />
          </Col>
        </Row>
      )}

      {/* Cluster Info */}
      {info && (
        <Card
          title={
            <Space>
              <ClusterOutlined />
              <span>Cluster Information</span>
            </Space>
          }
          extra={
            <Tag color="blue" style={{ borderRadius: 4 }}>
              {info.mode}
            </Tag>
          }
        >
          <Descriptions
            column={{ xs: 1, sm: 2, lg: 3 }}
            bordered
            size="small"
          >
            <Descriptions.Item label="Cluster ID">
              <Text copyable style={{ fontSize: 12, fontFamily: "monospace" }}>
                {info.cluster_id}
              </Text>
            </Descriptions.Item>
            <Descriptions.Item label="Cluster Name">
              <Text strong>{info.cluster_name}</Text>
            </Descriptions.Item>
            <Descriptions.Item label="Local Node ID">
              <Tag color="blue" style={{ borderRadius: 4 }}>
                {info.node_id}
              </Tag>
            </Descriptions.Item>
            <Descriptions.Item label="Mode">
              <Tag
                color={modeColors[info.mode] ?? "default"}
                style={{ borderRadius: 4 }}
              >
                {info.mode}
              </Tag>
            </Descriptions.Item>
            <Descriptions.Item label="Applications">
              <Text strong>{info.app_count}</Text>
            </Descriptions.Item>
            <Descriptions.Item label="Identity System">
              <Tag
                color={info.identity_enabled ? "green" : "default"}
                style={{ borderRadius: 4 }}
              >
                {info.identity_enabled ? "Enabled" : "Disabled"}
              </Tag>
            </Descriptions.Item>
          </Descriptions>
        </Card>
      )}

      {/* Routing Strategy */}
      {routingConfig.data && info?.mode !== "standalone" && (
        <Card
          title={
            <Space>
              <BranchesOutlined />
              <span>Smart Routing</span>
            </Space>
          }
          extra={
            <Tag
              color="blue"
              style={{ borderRadius: 4, textTransform: "capitalize" as const }}
            >
              {routingConfig.data.strategy.replace("_", " ")}
            </Tag>
          }
        >
          <Descriptions
            column={{ xs: 1, sm: 2, lg: 3 }}
            bordered
            size="small"
          >
            <Descriptions.Item label="Strategy">
              <Tag
                color="blue"
                style={{ borderRadius: 4 }}
              >
                {routingConfig.data.strategy}
              </Tag>
            </Descriptions.Item>
            <Descriptions.Item label="Node Fallback">
              <Badge
                status={routingConfig.data.node_fallback_enabled ? "success" : "default"}
                text={routingConfig.data.node_fallback_enabled ? "Enabled" : "Disabled"}
              />
            </Descriptions.Item>
            <Descriptions.Item label="Max Retries">
              <Text strong>{routingConfig.data.max_node_retries}</Text>
            </Descriptions.Item>
            <Descriptions.Item label="Quarantine Threshold">
              <Text>
                {routingConfig.data.quarantine_threshold} consecutive failures
              </Text>
            </Descriptions.Item>
            <Descriptions.Item label="Quarantine Duration">
              <Text>{routingConfig.data.quarantine_duration}s</Text>
            </Descriptions.Item>
            {Object.keys(routingConfig.data.module_affinity).length > 0 && (
              <Descriptions.Item label="Module Affinity" span={3}>
                <Space size={[8, 8]} wrap>
                  {Object.entries(routingConfig.data.module_affinity).map(
                    ([mod, node]) => (
                      <Tag
                        key={mod}
                        icon={<LinkOutlined />}
                        style={{ borderRadius: 4 }}
                      >
                        {mod} <SwapOutlined style={{ fontSize: 10, margin: "0 4px" }} /> {node}
                      </Tag>
                    ),
                  )}
                </Space>
              </Descriptions.Item>
            )}
          </Descriptions>
        </Card>
      )}

      {/* Register Node Form */}
      {showRegister && info?.mode !== "standalone" && (
        <Card
          title={
            <Space>
              <PlusOutlined />
              <span>Register Remote Node</span>
            </Space>
          }
          extra={
            <Button size="small" onClick={() => setShowRegister(false)}>
              Cancel
            </Button>
          }
        >
          <Form
            form={form}
            layout="vertical"
            onFinish={handleRegister}
            style={{ maxWidth: 600 }}
          >
            <Row gutter={16}>
              <Col span={12}>
                <Form.Item
                  name="node_id"
                  label="Node ID"
                  rules={[{ required: true, message: "Node ID is required" }]}
                >
                  <Input placeholder="e.g. gpu-node-1" />
                </Form.Item>
              </Col>
              <Col span={12}>
                <Form.Item
                  name="url"
                  label="URL"
                  rules={[
                    { required: true, message: "URL is required" },
                    { type: "url", message: "Must be a valid URL" },
                  ]}
                >
                  <Input placeholder="http://192.168.1.50:40000" />
                </Form.Item>
              </Col>
            </Row>
            <Row gutter={16}>
              <Col span={12}>
                <Form.Item name="api_token" label="API Token">
                  <Input.Password placeholder="Optional" />
                </Form.Item>
              </Col>
              <Col span={12}>
                <Form.Item name="location" label="Location">
                  <Input placeholder="e.g. lyon, paris" />
                </Form.Item>
              </Col>
            </Row>
            <Form.Item>
              <Button
                type="primary"
                htmlType="submit"
                icon={<PlusOutlined />}
                loading={registerNode.isPending}
              >
                Register Node
              </Button>
            </Form.Item>
          </Form>
        </Card>
      )}

      {/* Node Table */}
      <Card
        title={
          <Space>
            <ClusterOutlined />
            <span>Nodes</span>
          </Space>
        }
        extra={
          <Text type="secondary" style={{ fontSize: 12 }}>
            {nodes.data?.length ?? 0} registered
          </Text>
        }
        styles={{ body: { padding: 0 } }}
      >
        <Table
          dataSource={nodes.data ?? health?.nodes ?? []}
          columns={columns}
          rowKey="node_id"
          pagination={false}
          size="middle"
          loading={nodes.isLoading}
          onRow={(record) => ({
            style: { cursor: "pointer" },
            onClick: (e) => {
              const target = e.target as HTMLElement;
              if (
                target.closest("button") ||
                target.closest(".ant-popover") ||
                target.closest(".ant-popconfirm")
              ) {
                return;
              }
              router.push(`/cluster/${record.node_id}`);
            },
          })}
        />
      </Card>
    </Space>
  );
}
