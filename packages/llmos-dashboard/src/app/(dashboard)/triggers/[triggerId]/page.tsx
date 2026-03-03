"use client";

import React from "react";
import { useParams, useRouter } from "next/navigation";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import {
  Card,
  Typography,
  Space,
  Button,
  Descriptions,
  Tag,
  Spin,
  Alert,
  Row,
  Col,
  Tooltip,
} from "antd";
import {
  ArrowLeftOutlined,
  PlayCircleOutlined,
  PauseCircleOutlined,
  DeleteOutlined,
  ThunderboltOutlined,
  WarningOutlined,
  ClockCircleOutlined,
  FireOutlined,
} from "@ant-design/icons";
import { api, ApiError } from "@/lib/api/client";
import { formatDate, timeAgo } from "@/lib/utils/formatters";
import { PageHeader } from "@/components/common/PageHeader";
import { StatCard } from "@/components/common/StatCard";
import { FeatureDisabled } from "@/components/common/FeatureDisabled";
import type { TriggerInfo } from "@/types/config";

function isFeatureDisabledError(error: unknown): boolean {
  if (error instanceof ApiError) {
    const msg = (error.detail ?? error.message ?? "").toLowerCase();
    return msg.includes("not enabled") || msg.includes("not available");
  }
  return false;
}

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
  idle: "default",
  paused: "orange",
  error: "red",
};

export default function TriggerDetailPage() {
  const { triggerId } = useParams<{ triggerId: string }>();
  const router = useRouter();
  const queryClient = useQueryClient();

  const {
    data: trigger,
    isLoading,
    error,
  } = useQuery<TriggerInfo>({
    queryKey: ["triggers", triggerId],
    queryFn: () => api.get<TriggerInfo>(`/triggers/${triggerId}`),
    retry: false,
    refetchInterval: 5000,
  });

  const activateMutation = useMutation({
    mutationFn: () => api.post(`/triggers/${triggerId}/enable`),
    onSuccess: () =>
      queryClient.invalidateQueries({ queryKey: ["triggers", triggerId] }),
  });

  const deactivateMutation = useMutation({
    mutationFn: () => api.post(`/triggers/${triggerId}/disable`),
    onSuccess: () =>
      queryClient.invalidateQueries({ queryKey: ["triggers", triggerId] }),
  });

  const deleteMutation = useMutation({
    mutationFn: () => api.delete(`/triggers/${triggerId}`),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["triggers"] });
      router.push("/triggers");
    },
  });

  if (isLoading) {
    return (
      <div style={{ textAlign: "center", padding: 80 }}>
        <Spin size="large" />
      </div>
    );
  }

  if (error || !trigger) {
    if (error && isFeatureDisabledError(error)) {
      return (
        <>
          <PageHeader
            icon={<ThunderboltOutlined />}
            title="Trigger"
            subtitle="Triggers are not enabled"
            extra={
              <Button
                icon={<ArrowLeftOutlined />}
                onClick={() => router.push("/triggers")}
              >
                Back to Triggers
              </Button>
            }
          />
          <FeatureDisabled
            feature="Triggers"
            description="The trigger engine is not active. Enable triggers in your configuration to automate event-driven actions."
            configHint="triggers.enabled = true"
            icon={<ThunderboltOutlined />}
          />
        </>
      );
    }
    return (
      <Alert
        type="error"
        message="Failed to load trigger"
        description={error ? getErrorMessage(error) : "Trigger not found"}
        showIcon
      />
    );
  }

  return (
    <Space direction="vertical" size="large" style={{ width: "100%" }}>
      <PageHeader
        icon={<ThunderboltOutlined />}
        title={trigger.name}
        subtitle={`ID: ${trigger.trigger_id}`}
        tags={
          <>
            <Tag color={stateColorMap[trigger.state] ?? "default"}>
              {trigger.state}
            </Tag>
            {trigger.enabled ? (
              <Tag color="green">Enabled</Tag>
            ) : (
              <Tag color="default">Disabled</Tag>
            )}
            <Tag color="blue">{trigger.trigger_type}</Tag>
          </>
        }
        extra={
          <>
            <Button
              icon={<ArrowLeftOutlined />}
              onClick={() => router.push("/triggers")}
            >
              Back
            </Button>
            {trigger.enabled ? (
              <Button
                icon={<PauseCircleOutlined />}
                loading={deactivateMutation.isPending}
                onClick={() => deactivateMutation.mutate()}
              >
                Deactivate
              </Button>
            ) : (
              <Button
                type="primary"
                icon={<PlayCircleOutlined />}
                loading={activateMutation.isPending}
                onClick={() => activateMutation.mutate()}
              >
                Activate
              </Button>
            )}
            <Button
              danger
              icon={<DeleteOutlined />}
              loading={deleteMutation.isPending}
              onClick={() => deleteMutation.mutate()}
            >
              Delete
            </Button>
          </>
        }
      />

      {/* Stats Row */}
      <Row gutter={[16, 16]}>
        <Col xs={24} sm={12} lg={6}>
          <StatCard
            title="Fire Count"
            value={trigger.fire_count}
            prefix={<FireOutlined />}
            color="#fa8c16"
            footer={
              <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                Total executions
              </Typography.Text>
            }
          />
        </Col>
        <Col xs={24} sm={12} lg={6}>
          <StatCard
            title="Fail Count"
            value={trigger.fail_count}
            prefix={<WarningOutlined />}
            color={trigger.fail_count > 0 ? "#ff4d4f" : "#d9d9d9"}
            valueStyle={
              trigger.fail_count > 0 ? { color: "#ff4d4f" } : undefined
            }
            footer={
              <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                Execution errors
              </Typography.Text>
            }
          />
        </Col>
        <Col xs={24} sm={12} lg={6}>
          <StatCard
            title="Throttle Count"
            value={trigger.throttle_count}
            prefix={<ClockCircleOutlined />}
            color="#722ed1"
            footer={
              <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                Rate-limited events
              </Typography.Text>
            }
          />
        </Col>
        <Col xs={24} sm={12} lg={6}>
          <StatCard
            title="State"
            value={trigger.state}
            prefix={<ThunderboltOutlined />}
            color={
              trigger.state === "active"
                ? "#52c41a"
                : trigger.state === "error"
                  ? "#ff4d4f"
                  : "#d9d9d9"
            }
            valueStyle={{
              color:
                trigger.state === "active"
                  ? "#52c41a"
                  : trigger.state === "error"
                    ? "#ff4d4f"
                    : undefined,
            }}
            footer={
              <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                Current trigger state
              </Typography.Text>
            }
          />
        </Col>
      </Row>

      {/* Trigger Details */}
      <Card
        title={
          <Space>
            <ThunderboltOutlined />
            <span>Trigger Details</span>
          </Space>
        }
      >
        <Descriptions column={{ xs: 1, sm: 2, lg: 3 }} bordered size="small">
          <Descriptions.Item label="Trigger ID">
            <Tooltip title="Click to copy">
              <Typography.Text copyable style={{ fontSize: 13 }}>
                {trigger.trigger_id}
              </Typography.Text>
            </Tooltip>
          </Descriptions.Item>
          <Descriptions.Item label="Name">
            <Typography.Text strong>{trigger.name}</Typography.Text>
          </Descriptions.Item>
          <Descriptions.Item label="Type">
            <Tag color="blue">{trigger.trigger_type}</Tag>
          </Descriptions.Item>
          <Descriptions.Item label="State">
            <Tag color={stateColorMap[trigger.state] ?? "default"}>
              {trigger.state}
            </Tag>
          </Descriptions.Item>
          <Descriptions.Item label="Enabled">
            {trigger.enabled ? (
              <Tag color="green">Yes</Tag>
            ) : (
              <Tag color="default">No</Tag>
            )}
          </Descriptions.Item>
          <Descriptions.Item label="Created">
            <Tooltip title={formatDate(trigger.created_at)}>
              <span>{timeAgo(trigger.created_at)}</span>
            </Tooltip>
          </Descriptions.Item>
          <Descriptions.Item label="Description" span={3}>
            {trigger.description || (
              <Typography.Text type="secondary">No description provided</Typography.Text>
            )}
          </Descriptions.Item>
          {trigger.tags.length > 0 && (
            <Descriptions.Item label="Tags" span={3}>
              <Space wrap>
                {trigger.tags.map((tag) => (
                  <Tag key={tag} color="geekblue">{tag}</Tag>
                ))}
              </Space>
            </Descriptions.Item>
          )}
        </Descriptions>
      </Card>
    </Space>
  );
}
