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
  StopOutlined,
  PlayCircleOutlined,
  DeleteOutlined,
  VideoCameraOutlined,
  FileTextOutlined,
  ClockCircleOutlined,
} from "@ant-design/icons";
import { api, ApiError } from "@/lib/api/client";
import { formatDate, timeAgo } from "@/lib/utils/formatters";
import { PageHeader } from "@/components/common/PageHeader";
import { StatCard } from "@/components/common/StatCard";
import { FeatureDisabled } from "@/components/common/FeatureDisabled";
import type { RecordingInfo } from "@/types/config";

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

const statusColorMap: Record<string, string> = {
  active: "green",
  stopped: "default",
  replaying: "blue",
};

export default function RecordingDetailPage() {
  const { recordingId } = useParams<{ recordingId: string }>();
  const router = useRouter();
  const queryClient = useQueryClient();

  const {
    data: recording,
    isLoading,
    error,
  } = useQuery<RecordingInfo>({
    queryKey: ["recordings", recordingId],
    queryFn: () => api.get<RecordingInfo>(`/recordings/${recordingId}`),
    retry: false,
    refetchInterval: 3000,
  });

  const stopMutation = useMutation({
    mutationFn: () => api.post(`/recordings/${recordingId}/stop`),
    onSuccess: () =>
      queryClient.invalidateQueries({
        queryKey: ["recordings", recordingId],
      }),
  });

  const replayMutation = useMutation({
    mutationFn: () => api.get(`/recordings/${recordingId}/replay`),
  });

  const deleteMutation = useMutation({
    mutationFn: () => api.delete(`/recordings/${recordingId}`),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["recordings"] });
      router.push("/recordings");
    },
  });

  if (isLoading) {
    return (
      <div style={{ textAlign: "center", padding: 80 }}>
        <Spin size="large" />
      </div>
    );
  }

  if (error || !recording) {
    if (error && isFeatureDisabledError(error)) {
      return (
        <>
          <PageHeader
            icon={<VideoCameraOutlined />}
            title="Recording"
            subtitle="Recordings are not enabled"
            extra={
              <Button
                icon={<ArrowLeftOutlined />}
                onClick={() => router.push("/recordings")}
              >
                Back to Recordings
              </Button>
            }
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
        message="Failed to load recording"
        description={error ? getErrorMessage(error) : "Recording not found"}
        showIcon
      />
    );
  }

  return (
    <Space direction="vertical" size="large" style={{ width: "100%" }}>
      <PageHeader
        icon={<VideoCameraOutlined />}
        title={recording.title}
        subtitle={`ID: ${recording.recording_id}`}
        tags={
          <>
            <Tag color={statusColorMap[recording.status] ?? "default"}>
              {recording.status}
            </Tag>
            {recording.status === "active" && (
              <Tag color="green" icon={<VideoCameraOutlined />}>
                Recording
              </Tag>
            )}
          </>
        }
        extra={
          <>
            <Button
              icon={<ArrowLeftOutlined />}
              onClick={() => router.push("/recordings")}
            >
              Back
            </Button>
            {recording.status === "active" && (
              <Button
                danger
                icon={<StopOutlined />}
                loading={stopMutation.isPending}
                onClick={() => stopMutation.mutate()}
              >
                Stop Recording
              </Button>
            )}
            {recording.status === "stopped" && (
              <Button
                type="primary"
                icon={<PlayCircleOutlined />}
                loading={replayMutation.isPending}
                onClick={() => replayMutation.mutate()}
              >
                Replay
              </Button>
            )}
            {recording.status !== "active" && (
              <Button
                danger
                icon={<DeleteOutlined />}
                loading={deleteMutation.isPending}
                onClick={() => deleteMutation.mutate()}
              >
                Delete
              </Button>
            )}
          </>
        }
      />

      {/* Stats Row */}
      <Row gutter={[16, 16]}>
        <Col xs={24} sm={12} lg={6}>
          <StatCard
            title="Status"
            value={recording.status}
            prefix={<VideoCameraOutlined />}
            color={
              recording.status === "active"
                ? "#52c41a"
                : recording.status === "replaying"
                  ? "#1677ff"
                  : "#d9d9d9"
            }
            valueStyle={{
              color:
                recording.status === "active"
                  ? "#52c41a"
                  : recording.status === "replaying"
                    ? "#1677ff"
                    : undefined,
            }}
            footer={
              <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                Current recording state
              </Typography.Text>
            }
          />
        </Col>
        <Col xs={24} sm={12} lg={6}>
          <StatCard
            title="Plans Recorded"
            value={recording.plan_count}
            prefix={<FileTextOutlined />}
            color="#722ed1"
            footer={
              <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                Captured plan executions
              </Typography.Text>
            }
          />
        </Col>
        <Col xs={24} sm={12} lg={6}>
          <StatCard
            title="Created"
            value={timeAgo(recording.created_at)}
            prefix={<ClockCircleOutlined />}
            color="#1677ff"
            footer={
              <Tooltip title={formatDate(recording.created_at)}>
                <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                  {formatDate(recording.created_at)}
                </Typography.Text>
              </Tooltip>
            }
          />
        </Col>
        <Col xs={24} sm={12} lg={6}>
          <StatCard
            title="Recording ID"
            value={recording.recording_id.slice(0, 8) + "..."}
            color="#fa8c16"
            footer={
              <Typography.Text
                type="secondary"
                style={{ fontSize: 12 }}
                copyable={{ text: recording.recording_id }}
              >
                Click to copy full ID
              </Typography.Text>
            }
          />
        </Col>
      </Row>

      {/* Recording Details */}
      <Card
        title={
          <Space>
            <VideoCameraOutlined />
            <span>Recording Details</span>
          </Space>
        }
      >
        <Descriptions column={{ xs: 1, sm: 2 }} bordered size="small">
          <Descriptions.Item label="Recording ID">
            <Typography.Text copyable style={{ fontSize: 13 }}>
              {recording.recording_id}
            </Typography.Text>
          </Descriptions.Item>
          <Descriptions.Item label="Title">
            <Typography.Text strong>{recording.title}</Typography.Text>
          </Descriptions.Item>
          <Descriptions.Item label="Status">
            <Tag color={statusColorMap[recording.status] ?? "default"}>
              {recording.status}
            </Tag>
          </Descriptions.Item>
          <Descriptions.Item label="Plans Recorded">
            <Typography.Text strong>{recording.plan_count}</Typography.Text>
          </Descriptions.Item>
          <Descriptions.Item label="Created">
            <Tooltip title={formatDate(recording.created_at)}>
              <span>{timeAgo(recording.created_at)}</span>
            </Tooltip>
          </Descriptions.Item>
          <Descriptions.Item label="Description" span={2}>
            {recording.description || (
              <Typography.Text type="secondary">No description provided</Typography.Text>
            )}
          </Descriptions.Item>
        </Descriptions>
      </Card>
    </Space>
  );
}
