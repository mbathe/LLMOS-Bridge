"use client";

import React from "react";
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
  Button,
  Popconfirm,
  Tooltip,
  Badge,
  message,
} from "antd";
import {
  ClusterOutlined,
  CheckCircleOutlined,
  CloseCircleOutlined,
  ArrowLeftOutlined,
  HeartOutlined,
  DeleteOutlined,
  FieldTimeOutlined,
  AppstoreOutlined,
  DashboardOutlined,
  EnvironmentOutlined,
} from "@ant-design/icons";
import { useParams, useRouter } from "next/navigation";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { api, ApiError } from "@/lib/api/client";
import { PageHeader } from "@/components/common/PageHeader";
import { StatCard } from "@/components/common/StatCard";
import { useNodeDetail } from "@/hooks/useCluster";
import { timeAgo, formatTimestamp } from "@/lib/utils/formatters";

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

export default function NodeDetailPage() {
  const params = useParams<{ nodeId: string }>();
  const router = useRouter();
  const queryClient = useQueryClient();
  const nodeId = params.nodeId;

  const { data: node, isLoading, error, refetch } = useNodeDetail(nodeId);

  const heartbeat = useMutation({
    mutationFn: () =>
      api.post<{ node_id: string; health: Record<string, unknown> }>(
        `/nodes/${nodeId}/heartbeat`,
      ),
    onSuccess: () => {
      message.success("Heartbeat sent");
      refetch();
      queryClient.invalidateQueries({ queryKey: ["cluster"] });
    },
    onError: (err) => message.error(getErrorMessage(err)),
  });

  const unregister = useMutation({
    mutationFn: () => api.delete<{ detail: string }>(`/nodes/${nodeId}`),
    onSuccess: () => {
      message.success(`Node '${nodeId}' unregistered`);
      queryClient.invalidateQueries({ queryKey: ["cluster"] });
      router.push("/cluster");
    },
    onError: (err) => message.error(getErrorMessage(err)),
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
        message={`Failed to load node '${nodeId}'`}
        description={getErrorMessage(error)}
        showIcon
        action={
          <Button onClick={() => router.push("/cluster")}>
            Back to Cluster
          </Button>
        }
      />
    );
  }

  if (!node) {
    return (
      <Alert
        type="warning"
        message="Node not found"
        showIcon
        action={
          <Button onClick={() => router.push("/cluster")}>
            Back to Cluster
          </Button>
        }
      />
    );
  }

  return (
    <Space direction="vertical" size="large" style={{ width: "100%" }}>
      <PageHeader
        icon={<ClusterOutlined />}
        title={node.node_id}
        subtitle={node.is_local ? "Local node" : `Remote node — ${node.url ?? "no URL"}`}
        tags={
          <Space size={4}>
            <Badge
              status={node.available ? "success" : "error"}
              text={node.available ? "Available" : "Unavailable"}
            />
            {node.is_local && (
              <Tag color="blue" style={{ borderRadius: 4 }}>
                local
              </Tag>
            )}
            {node.quarantined && (
              <Tag color="red" style={{ borderRadius: 4 }}>
                Quarantined
              </Tag>
            )}
          </Space>
        }
        extra={
          <Space>
            <Button
              icon={<ArrowLeftOutlined />}
              onClick={() => router.push("/cluster")}
            >
              Back
            </Button>
            {!node.is_local && (
              <>
                <Button
                  icon={<HeartOutlined />}
                  onClick={() => heartbeat.mutate()}
                  loading={heartbeat.isPending}
                >
                  Heartbeat
                </Button>
                <Popconfirm
                  title={`Unregister node '${nodeId}'?`}
                  description="This will remove the node from the cluster."
                  onConfirm={() => unregister.mutate()}
                  okText="Yes"
                  cancelText="No"
                >
                  <Button
                    danger
                    icon={<DeleteOutlined />}
                    loading={unregister.isPending}
                  >
                    Unregister
                  </Button>
                </Popconfirm>
              </>
            )}
          </Space>
        }
      />

      {/* Stat Cards */}
      <Row gutter={[16, 16]}>
        <Col xs={24} sm={12} lg={6}>
          <StatCard
            title="Latency"
            value={node.latency_ms != null ? `${node.latency_ms.toFixed(1)} ms` : "—"}
            prefix={<FieldTimeOutlined />}
            color={
              node.latency_ms == null
                ? "#8c8c8c"
                : node.latency_ms < 50
                  ? "#52c41a"
                  : node.latency_ms < 200
                    ? "#faad14"
                    : "#ff4d4f"
            }
            footer={
              <Text type="secondary" style={{ fontSize: 12 }}>
                Last heartbeat round-trip
              </Text>
            }
          />
        </Col>
        <Col xs={24} sm={12} lg={6}>
          <StatCard
            title="Active Actions"
            value={node.active_actions}
            prefix={<DashboardOutlined />}
            color={node.active_actions > 0 ? "#1677ff" : "#8c8c8c"}
            footer={
              <Text type="secondary" style={{ fontSize: 12 }}>
                Currently executing
              </Text>
            }
          />
        </Col>
        <Col xs={24} sm={12} lg={6}>
          <StatCard
            title="Last Heartbeat"
            value={node.last_heartbeat ? timeAgo(node.last_heartbeat) : "—"}
            prefix={<HeartOutlined />}
            color="#722ed1"
            footer={
              node.last_heartbeat ? (
                <Text type="secondary" style={{ fontSize: 12 }}>
                  {formatTimestamp(node.last_heartbeat)}
                </Text>
              ) : (
                <Text type="secondary" style={{ fontSize: 12 }}>
                  No heartbeat recorded
                </Text>
              )
            }
          />
        </Col>
        <Col xs={24} sm={12} lg={6}>
          <StatCard
            title="Modules"
            value={node.modules.length}
            prefix={<AppstoreOutlined />}
            color="#fa8c16"
            footer={
              <Text type="secondary" style={{ fontSize: 12 }}>
                Available capabilities
              </Text>
            }
          />
        </Col>
      </Row>

      {/* Node Details */}
      <Card
        title={
          <Space>
            <ClusterOutlined />
            <span>Node Details</span>
          </Space>
        }
        extra={
          <Tag
            color={node.available ? "green" : "red"}
            style={{ borderRadius: 4 }}
          >
            {node.available ? "Available" : "Unavailable"}
          </Tag>
        }
      >
        <Descriptions
          column={{ xs: 1, sm: 2, lg: 3 }}
          bordered
          size="small"
        >
          <Descriptions.Item label="Node ID">
            <Text strong copyable>
              {node.node_id}
            </Text>
          </Descriptions.Item>
          <Descriptions.Item label="URL">
            {node.url ? (
              <Text copyable style={{ fontFamily: "monospace", fontSize: 12 }}>
                {node.url}
              </Text>
            ) : (
              <Text type="secondary">—</Text>
            )}
          </Descriptions.Item>
          <Descriptions.Item label="Location">
            {node.location ? (
              <Space size={4}>
                <EnvironmentOutlined />
                <Text>{node.location}</Text>
              </Space>
            ) : (
              <Text type="secondary">—</Text>
            )}
          </Descriptions.Item>
          <Descriptions.Item label="Type">
            <Tag color={node.is_local ? "blue" : "green"} style={{ borderRadius: 4 }}>
              {node.is_local ? "Local" : "Remote"}
            </Tag>
          </Descriptions.Item>
          <Descriptions.Item label="Status">
            <Badge
              status={node.available ? "success" : "error"}
              text={node.available ? "Available" : "Unavailable"}
            />
          </Descriptions.Item>
          <Descriptions.Item label="Quarantined">
            {node.quarantined ? (
              <Tag color="red" style={{ borderRadius: 4 }}>
                Yes — excluded from routing
              </Tag>
            ) : (
              <Tag color="green" style={{ borderRadius: 4 }}>
                No
              </Tag>
            )}
          </Descriptions.Item>
          <Descriptions.Item label="Latency">
            {node.latency_ms != null ? (
              <Text>{node.latency_ms.toFixed(1)} ms</Text>
            ) : (
              <Text type="secondary">—</Text>
            )}
          </Descriptions.Item>
          <Descriptions.Item label="Active Actions">
            <Tag
              color={node.active_actions > 0 ? "processing" : "default"}
              style={{ borderRadius: 4 }}
            >
              {node.active_actions}
            </Tag>
          </Descriptions.Item>
          <Descriptions.Item label="Last Heartbeat">
            {node.last_heartbeat ? (
              <Tooltip title={formatTimestamp(node.last_heartbeat)}>
                <Text>{timeAgo(node.last_heartbeat)}</Text>
              </Tooltip>
            ) : (
              <Text type="secondary">—</Text>
            )}
          </Descriptions.Item>
        </Descriptions>
      </Card>

      {/* Modules */}
      <Card
        title={
          <Space>
            <AppstoreOutlined />
            <span>Available Modules</span>
          </Space>
        }
        extra={
          <Text type="secondary" style={{ fontSize: 12 }}>
            {node.modules.length} modules
          </Text>
        }
      >
        {node.modules.length === 0 ? (
          <Text type="secondary">
            {node.is_local
              ? "Module list is managed by the local registry."
              : "No modules reported by this node."}
          </Text>
        ) : (
          <Space size={[8, 8]} wrap>
            {node.modules.map((mod) => (
              <Tag key={mod} color="blue" style={{ borderRadius: 4 }}>
                {mod}
              </Tag>
            ))}
          </Space>
        )}
      </Card>
    </Space>
  );
}
