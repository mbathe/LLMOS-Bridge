"use client";

import React, { useMemo } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import {
  Table,
  Space,
  Tag,
  Button,
  Typography,
  Spin,
  Alert,
  Card,
  Row,
  Col,
  Tooltip,
} from "antd";
import {
  EyeOutlined,
  StopOutlined,
  DeleteOutlined,
  ReloadOutlined,
  VideoCameraOutlined,
  PlayCircleOutlined,
  FileTextOutlined,
  PauseCircleOutlined,
} from "@ant-design/icons";
import { useRouter } from "next/navigation";
import { api, ApiError } from "@/lib/api/client";
import { timeAgo, truncateId } from "@/lib/utils/formatters";
import { PageHeader } from "@/components/common/PageHeader";
import { StatCard } from "@/components/common/StatCard";
import { FeatureDisabled } from "@/components/common/FeatureDisabled";
import type { RecordingInfo } from "@/types/config";
import type { ColumnsType } from "antd/es/table";

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

interface RecordingsResponse {
  recordings: RecordingInfo[];
}

export default function RecordingsPage() {
  const router = useRouter();
  const queryClient = useQueryClient();

  const {
    data,
    isLoading,
    error,
    refetch,
  } = useQuery<RecordingsResponse>({
    queryKey: ["recordings"],
    queryFn: () => api.get<RecordingsResponse>("/recordings"),
    retry: false,
    refetchInterval: 5000,
  });

  const stopMutation = useMutation({
    mutationFn: (recordingId: string) =>
      api.post(`/recordings/${recordingId}/stop`),
    onSuccess: () =>
      queryClient.invalidateQueries({ queryKey: ["recordings"] }),
  });

  const deleteMutation = useMutation({
    mutationFn: (recordingId: string) =>
      api.delete(`/recordings/${recordingId}`),
    onSuccess: () =>
      queryClient.invalidateQueries({ queryKey: ["recordings"] }),
  });

  const recordings = data?.recordings ?? [];

  const stats = useMemo(() => {
    const total = recordings.length;
    const active = recordings.filter((r) => r.status === "active").length;
    const stopped = recordings.filter((r) => r.status === "stopped").length;
    const totalPlans = recordings.reduce((sum, r) => sum + r.plan_count, 0);
    return { total, active, stopped, totalPlans };
  }, [recordings]);

  const statusColorMap: Record<string, string> = {
    active: "green",
    stopped: "default",
    replaying: "blue",
  };

  const columns: ColumnsType<RecordingInfo> = [
    {
      title: "Title",
      dataIndex: "title",
      key: "title",
      render: (title: string, record) => (
        <Space>
          <Button
            type="link"
            style={{ padding: 0, fontWeight: 500 }}
            onClick={() =>
              router.push(`/recordings/${record.recording_id}`)
            }
          >
            {title}
          </Button>
          {record.status === "active" && (
            <Tag color="green" icon={<VideoCameraOutlined />}>
              Recording
            </Tag>
          )}
        </Space>
      ),
    },
    {
      title: "ID",
      dataIndex: "recording_id",
      key: "recording_id",
      render: (id: string) => (
        <Tooltip title={id}>
          <Typography.Text type="secondary" style={{ fontSize: 12 }} copyable={{ text: id }}>
            {truncateId(id, 12)}
          </Typography.Text>
        </Tooltip>
      ),
    },
    {
      title: "Status",
      dataIndex: "status",
      key: "status",
      filters: [
        { text: "Active", value: "active" },
        { text: "Stopped", value: "stopped" },
        { text: "Replaying", value: "replaying" },
      ],
      onFilter: (value, record) => record.status === value,
      render: (status: string) => (
        <Tag color={statusColorMap[status] ?? "default"}>{status}</Tag>
      ),
    },
    {
      title: "Plans",
      dataIndex: "plan_count",
      key: "plan_count",
      sorter: (a, b) => a.plan_count - b.plan_count,
      render: (count: number) => (
        <Typography.Text strong={count > 0}>{count}</Typography.Text>
      ),
    },
    {
      title: "Description",
      dataIndex: "description",
      key: "description",
      ellipsis: true,
      render: (desc: string) =>
        desc ? (
          <Tooltip title={desc}>
            <Typography.Text ellipsis style={{ maxWidth: 200 }}>
              {desc}
            </Typography.Text>
          </Tooltip>
        ) : (
          <Typography.Text type="secondary">--</Typography.Text>
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
      title: "",
      key: "ops",
      width: 160,
      render: (_: unknown, record: RecordingInfo) => (
        <Space>
          <Tooltip title="View details">
            <Button
              type="text"
              size="small"
              icon={<EyeOutlined />}
              onClick={() =>
                router.push(`/recordings/${record.recording_id}`)
              }
            />
          </Tooltip>
          {record.status === "active" && (
            <Tooltip title="Stop recording">
              <Button
                type="text"
                size="small"
                icon={<StopOutlined />}
                danger
                loading={stopMutation.isPending}
                onClick={() => stopMutation.mutate(record.recording_id)}
              />
            </Tooltip>
          )}
          {record.status !== "active" && (
            <Tooltip title="Delete recording">
              <Button
                type="text"
                size="small"
                icon={<DeleteOutlined />}
                danger
                loading={deleteMutation.isPending}
                onClick={() => deleteMutation.mutate(record.recording_id)}
              />
            </Tooltip>
          )}
        </Space>
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
            icon={<VideoCameraOutlined />}
            title="Recordings"
            subtitle="Capture and replay plan execution sessions"
          />
          <FeatureDisabled
            feature="Recordings"
            description="Recording is not active. Enable recordings in your configuration to capture and replay plan executions."
            configHint="recording.enabled = true"
            icon={<VideoCameraOutlined />}
          />
        </>
      );
    }
    return (
      <Alert
        type="error"
        message="Failed to load recordings"
        description={getErrorMessage(error)}
        showIcon
      />
    );
  }

  return (
    <Space direction="vertical" size="large" style={{ width: "100%" }}>
      <PageHeader
        icon={<VideoCameraOutlined />}
        title="Recordings"
        subtitle="Capture and replay plan execution sessions"
        tags={
          <Tag color="blue">{recordings.length} total</Tag>
        }
        extra={
          <Button icon={<ReloadOutlined />} onClick={() => refetch()}>
            Refresh
          </Button>
        }
      />

      <Row gutter={[16, 16]}>
        <Col xs={24} sm={12} lg={6}>
          <StatCard
            title="Total Recordings"
            value={stats.total}
            prefix={<VideoCameraOutlined />}
            color="#1677ff"
            footer={
              <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                All sessions
              </Typography.Text>
            }
          />
        </Col>
        <Col xs={24} sm={12} lg={6}>
          <StatCard
            title="Active"
            value={stats.active}
            prefix={<PlayCircleOutlined />}
            color="#52c41a"
            valueStyle={stats.active > 0 ? { color: "#52c41a" } : undefined}
            footer={
              <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                Currently recording
              </Typography.Text>
            }
          />
        </Col>
        <Col xs={24} sm={12} lg={6}>
          <StatCard
            title="Stopped"
            value={stats.stopped}
            prefix={<PauseCircleOutlined />}
            color="#d9d9d9"
            footer={
              <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                Completed sessions
              </Typography.Text>
            }
          />
        </Col>
        <Col xs={24} sm={12} lg={6}>
          <StatCard
            title="Total Plans Recorded"
            value={stats.totalPlans}
            prefix={<FileTextOutlined />}
            color="#722ed1"
            footer={
              <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                Across all sessions
              </Typography.Text>
            }
          />
        </Col>
      </Row>

      <Card
        title={
          <Space>
            <VideoCameraOutlined />
            <span>Recording Sessions</span>
          </Space>
        }
        extra={
          <Typography.Text type="secondary" style={{ fontSize: 12 }}>
            Auto-refreshes every 5s
          </Typography.Text>
        }
      >
        <Table
          columns={columns}
          dataSource={recordings}
          rowKey="recording_id"
          loading={isLoading}
          pagination={{
            pageSize: 20,
            showTotal: (total) => `${total} recordings`,
            showSizeChanger: true,
          }}
          size="middle"
        />
      </Card>
    </Space>
  );
}
