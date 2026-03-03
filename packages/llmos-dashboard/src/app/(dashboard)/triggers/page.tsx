"use client";

import React, { useState, useMemo } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import {
  Table,
  Space,
  Tag,
  Button,
  Select,
  Typography,
  Switch,
  Spin,
  Alert,
  Card,
  Row,
  Col,
  Tooltip,
} from "antd";
import {
  EyeOutlined,
  PlusOutlined,
  ReloadOutlined,
  ThunderboltOutlined,
  FireOutlined,
  WarningOutlined,
  CheckCircleOutlined,
} from "@ant-design/icons";
import { useRouter } from "next/navigation";
import { api, ApiError } from "@/lib/api/client";
import { timeAgo, truncateId } from "@/lib/utils/formatters";
import { PageHeader } from "@/components/common/PageHeader";
import { StatCard } from "@/components/common/StatCard";
import { FeatureDisabled } from "@/components/common/FeatureDisabled";
import type { TriggerInfo } from "@/types/config";
import type { ColumnsType } from "antd/es/table";

interface TriggersResponse {
  triggers: TriggerInfo[];
}

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

export default function TriggersPage() {
  const router = useRouter();
  const queryClient = useQueryClient();
  const [stateFilter, setStateFilter] = useState<string>("");

  const {
    data,
    isLoading,
    error,
    refetch,
  } = useQuery<TriggersResponse>({
    queryKey: ["triggers", stateFilter],
    queryFn: () =>
      api.get<TriggersResponse>("/triggers", {
        state: stateFilter,
      }),
    retry: false,
    refetchInterval: 10000,
  });

  const enableMutation = useMutation({
    mutationFn: (triggerId: string) =>
      api.post(`/triggers/${triggerId}/enable`),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["triggers"] }),
  });

  const disableMutation = useMutation({
    mutationFn: (triggerId: string) =>
      api.post(`/triggers/${triggerId}/disable`),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["triggers"] }),
  });

  const stateOptions = [
    { label: "All States", value: "" },
    { label: "Active", value: "active" },
    { label: "Idle", value: "idle" },
    { label: "Paused", value: "paused" },
    { label: "Error", value: "error" },
  ];

  const triggers = data?.triggers ?? [];

  const stats = useMemo(() => {
    const total = triggers.length;
    const active = triggers.filter((t) => t.state === "active").length;
    const firingRate = triggers.reduce((sum, t) => sum + t.fire_count, 0);
    const failures = triggers.reduce((sum, t) => sum + t.fail_count, 0);
    return { total, active, firingRate, failures };
  }, [triggers]);

  const columns: ColumnsType<TriggerInfo> = [
    {
      title: "Trigger",
      dataIndex: "name",
      key: "name",
      render: (name: string, record) => (
        <Button
          type="link"
          style={{ padding: 0, fontWeight: 500 }}
          onClick={() => router.push(`/triggers/${record.trigger_id}`)}
        >
          {name}
        </Button>
      ),
    },
    {
      title: "ID",
      dataIndex: "trigger_id",
      key: "trigger_id",
      render: (id: string) => (
        <Tooltip title={id}>
          <Typography.Text type="secondary" style={{ fontSize: 12 }} copyable={{ text: id }}>
            {truncateId(id, 12)}
          </Typography.Text>
        </Tooltip>
      ),
    },
    {
      title: "Type",
      dataIndex: "trigger_type",
      key: "trigger_type",
      render: (type: string) => <Tag color="blue">{type}</Tag>,
    },
    {
      title: "State",
      dataIndex: "state",
      key: "state",
      filters: [
        { text: "Active", value: "active" },
        { text: "Idle", value: "idle" },
        { text: "Paused", value: "paused" },
        { text: "Error", value: "error" },
      ],
      onFilter: (value, record) => record.state === value,
      render: (state: string) => {
        const colorMap: Record<string, string> = {
          active: "green",
          idle: "default",
          paused: "orange",
          error: "red",
        };
        return <Tag color={colorMap[state] ?? "default"}>{state}</Tag>;
      },
    },
    {
      title: "Fires",
      dataIndex: "fire_count",
      key: "fire_count",
      sorter: (a, b) => a.fire_count - b.fire_count,
      render: (count: number) => (
        <Typography.Text strong={count > 0}>{count.toLocaleString()}</Typography.Text>
      ),
    },
    {
      title: "Failures",
      dataIndex: "fail_count",
      key: "fail_count",
      sorter: (a, b) => a.fail_count - b.fail_count,
      render: (count: number) =>
        count > 0 ? (
          <Tag color="red">{count}</Tag>
        ) : (
          <Typography.Text type="secondary">0</Typography.Text>
        ),
    },
    {
      title: "Created",
      dataIndex: "created_at",
      key: "created_at",
      render: (d: string) => (
        <Tooltip title={new Date(d).toLocaleString()}>
          <Typography.Text type="secondary" style={{ fontSize: 12 }}>
            {timeAgo(d)}
          </Typography.Text>
        </Tooltip>
      ),
    },
    {
      title: "Enabled",
      key: "enabled",
      render: (_: unknown, record: TriggerInfo) => (
        <Switch
          checked={record.enabled}
          size="small"
          loading={enableMutation.isPending || disableMutation.isPending}
          onChange={(checked) => {
            if (checked) {
              enableMutation.mutate(record.trigger_id);
            } else {
              disableMutation.mutate(record.trigger_id);
            }
          }}
        />
      ),
    },
    {
      title: "",
      key: "ops",
      width: 60,
      render: (_: unknown, record: TriggerInfo) => (
        <Tooltip title="View details">
          <Button
            type="text"
            size="small"
            icon={<EyeOutlined />}
            onClick={() => router.push(`/triggers/${record.trigger_id}`)}
          />
        </Tooltip>
      ),
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
    if (isFeatureDisabledError(error)) {
      return (
        <>
          <PageHeader
            icon={<ThunderboltOutlined />}
            title="Triggers"
            subtitle="Automate actions with event-driven triggers"
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
        message="Failed to load triggers"
        description={getErrorMessage(error)}
        showIcon
      />
    );
  }

  return (
    <Space direction="vertical" size="large" style={{ width: "100%" }}>
      <PageHeader
        icon={<ThunderboltOutlined />}
        title="Triggers"
        subtitle="Automate actions with event-driven triggers"
        tags={
          <Tag color="blue">{triggers.length} total</Tag>
        }
        extra={
          <>
            <Select
              value={stateFilter}
              onChange={setStateFilter}
              options={stateOptions}
              style={{ width: 140 }}
              placeholder="Filter by state"
            />
            <Button icon={<ReloadOutlined />} onClick={() => refetch()}>
              Refresh
            </Button>
            <Button
              type="primary"
              icon={<PlusOutlined />}
              onClick={() => router.push("/triggers/new")}
            >
              Create Trigger
            </Button>
          </>
        }
      />

      <Row gutter={[16, 16]}>
        <Col xs={24} sm={12} lg={6}>
          <StatCard
            title="Total Triggers"
            value={stats.total}
            prefix={<ThunderboltOutlined />}
            color="#1677ff"
            footer={
              <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                Registered triggers
              </Typography.Text>
            }
          />
        </Col>
        <Col xs={24} sm={12} lg={6}>
          <StatCard
            title="Active"
            value={stats.active}
            prefix={<CheckCircleOutlined />}
            color="#52c41a"
            valueStyle={{ color: "#52c41a" }}
            footer={
              <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                Currently running
              </Typography.Text>
            }
          />
        </Col>
        <Col xs={24} sm={12} lg={6}>
          <StatCard
            title="Firing Rate"
            value={stats.firingRate}
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
            title="Failures"
            value={stats.failures}
            prefix={<WarningOutlined />}
            color={stats.failures > 0 ? "#ff4d4f" : "#d9d9d9"}
            valueStyle={stats.failures > 0 ? { color: "#ff4d4f" } : undefined}
            footer={
              <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                Total errors
              </Typography.Text>
            }
          />
        </Col>
      </Row>

      <Card
        title={
          <Space>
            <ThunderboltOutlined />
            <span>Trigger List</span>
          </Space>
        }
        extra={
          <Typography.Text type="secondary" style={{ fontSize: 12 }}>
            Auto-refreshes every 10s
          </Typography.Text>
        }
      >
        <Table
          columns={columns}
          dataSource={triggers}
          rowKey="trigger_id"
          loading={isLoading}
          pagination={{
            pageSize: 20,
            showTotal: (total) => `${total} triggers`,
            showSizeChanger: true,
          }}
          size="middle"
        />
      </Card>
    </Space>
  );
}
