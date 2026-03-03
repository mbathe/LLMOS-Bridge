"use client";

import React, { useState, useMemo } from "react";
import { useQuery } from "@tanstack/react-query";
import {
  Card,
  Row,
  Col,
  Table,
  Space,
  Tag,
  Button,
  Typography,
  Select,
  Spin,
  Alert,
  Switch,
  Tooltip,
} from "antd";
import {
  ArrowLeftOutlined,
  ReloadOutlined,
  AuditOutlined,
  FieldTimeOutlined,
  NodeIndexOutlined,
  SyncOutlined,
} from "@ant-design/icons";
import { useRouter } from "next/navigation";
import { api, ApiError } from "@/lib/api/client";
import { PageHeader } from "@/components/common/PageHeader";
import { StatCard } from "@/components/common/StatCard";
import { formatTimestamp } from "@/lib/utils/formatters";
import { JsonViewer } from "@/components/common/JsonViewer";
import type { AuditEventEntry, AuditLogResponse } from "@/types/events";
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

const topicOptions: { label: string; value: string }[] = [
  { label: "All Topics", value: "" },
  { label: "Plans", value: "llmos.plans" },
  { label: "Actions", value: "llmos.actions" },
  { label: "Security", value: "llmos.security" },
  { label: "Modules", value: "llmos.modules" },
  { label: "Perception", value: "llmos.perception" },
  { label: "Memory", value: "llmos.memory" },
  { label: "System", value: "llmos.system" },
  { label: "Triggers", value: "llmos.triggers" },
  { label: "Recordings", value: "llmos.recordings" },
  { label: "Approval", value: "llmos.approval" },
];

const topicColorMap: Record<string, string> = {
  "llmos.plans": "blue",
  "llmos.actions": "cyan",
  "llmos.security": "red",
  "llmos.modules": "purple",
  "llmos.perception": "geekblue",
  "llmos.memory": "magenta",
  "llmos.system": "green",
  "llmos.triggers": "orange",
  "llmos.recordings": "gold",
  "llmos.approval": "volcano",
};

export default function AuditPage() {
  const router = useRouter();
  const [topicFilter, setTopicFilter] = useState<string>("");
  const [autoRefresh, setAutoRefresh] = useState(true);

  const {
    data,
    isLoading,
    error,
    refetch,
  } = useQuery<AuditLogResponse>({
    queryKey: ["security-audit", topicFilter],
    queryFn: () =>
      api.get<AuditLogResponse>("/admin/security/audit", {
        limit: "50",
        topic: topicFilter,
      }),
    retry: false,
    refetchInterval: autoRefresh ? 5000 : false,
  });

  // Count unique topics with events
  const uniqueTopics = useMemo(() => {
    if (!data?.events) return 0;
    const topics = new Set(data.events.map((e) => e._topic).filter(Boolean));
    return topics.size;
  }, [data]);

  // Build a "details" object from the event entry, excluding internal fields
  const getEventDetails = (record: AuditEventEntry): Record<string, unknown> => {
    const { event, _topic, _timestamp, ...rest } = record;
    return rest;
  };

  const columns: ColumnsType<AuditEventEntry> = [
    {
      title: "Timestamp",
      key: "timestamp",
      width: 180,
      render: (_: unknown, record: AuditEventEntry) => (
        <Tooltip
          title={
            record._timestamp
              ? new Date(record._timestamp * 1000).toISOString()
              : undefined
          }
        >
          <Text style={{ fontSize: 12, fontFamily: "monospace" }}>
            {record._timestamp ? formatTimestamp(record._timestamp) : "-"}
          </Text>
        </Tooltip>
      ),
      sorter: (a, b) => (a._timestamp ?? 0) - (b._timestamp ?? 0),
      defaultSortOrder: "descend" as const,
    },
    {
      title: "Topic",
      key: "topic",
      width: 170,
      render: (_: unknown, record: AuditEventEntry) => {
        const topic = record._topic ?? "";
        const shortTopic = topic.replace("llmos.", "");
        return (
          <Tag
            color={topicColorMap[topic] ?? "default"}
            style={{ borderRadius: 4 }}
          >
            {shortTopic || "-"}
          </Tag>
        );
      },
      filters: topicOptions
        .filter((o) => o.value !== "")
        .map((o) => ({ text: o.label, value: o.value })),
      onFilter: (value, record) => record._topic === value,
    },
    {
      title: "Event",
      dataIndex: "event",
      key: "event",
      width: 220,
      render: (type: string) => (
        <Text strong style={{ fontSize: 13 }}>
          {type}
        </Text>
      ),
    },
    {
      title: "Details",
      key: "details",
      render: (_: unknown, record: AuditEventEntry) => {
        const details = getEventDetails(record);
        const keys = Object.keys(details);
        return (
          <Space size={4} wrap>
            {keys.slice(0, 3).map((key) => (
              <Tooltip key={key} title={`${key}: ${JSON.stringify(details[key])}`}>
                <Tag style={{ fontSize: 11, borderRadius: 4 }}>
                  {key}
                </Tag>
              </Tooltip>
            ))}
            {keys.length > 3 && (
              <Text type="secondary" style={{ fontSize: 11 }}>
                +{keys.length - 3} more
              </Text>
            )}
          </Space>
        );
      },
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
    return (
      <Alert
        type="error"
        message="Failed to load audit log"
        description={getErrorMessage(error)}
        showIcon
      />
    );
  }

  return (
    <Space direction="vertical" size="large" style={{ width: "100%" }}>
      <PageHeader
        icon={<AuditOutlined />}
        title="Audit Log"
        subtitle="Real-time security event trail"
        tags={
          autoRefresh ? (
            <Tag icon={<SyncOutlined spin />} color="processing">
              Live
            </Tag>
          ) : undefined
        }
        extra={
          <Space>
            <Button
              icon={<ArrowLeftOutlined />}
              onClick={() => router.push("/security")}
            >
              Back to Security
            </Button>
            <Tooltip title="Auto-refresh every 5 seconds">
              <Space size="small">
                <Text type="secondary" style={{ fontSize: 12 }}>
                  Auto-refresh
                </Text>
                <Switch
                  size="small"
                  checked={autoRefresh}
                  onChange={setAutoRefresh}
                />
              </Space>
            </Tooltip>
            <Select
              value={topicFilter}
              onChange={setTopicFilter}
              options={topicOptions}
              style={{ width: 160 }}
              placeholder="Filter by topic"
            />
            <Button icon={<ReloadOutlined />} onClick={() => refetch()}>
              Refresh
            </Button>
          </Space>
        }
      />

      {/* Summary Stats */}
      <Row gutter={[16, 16]}>
        <Col xs={24} sm={12}>
          <StatCard
            title="Total Events"
            value={data?.count ?? 0}
            prefix={<FieldTimeOutlined />}
            color="#1677ff"
            footer={
              <Text type="secondary" style={{ fontSize: 12 }}>
                {topicFilter
                  ? `Filtered by ${topicFilter.replace("llmos.", "")}`
                  : "All topics"}
              </Text>
            }
          />
        </Col>
        <Col xs={24} sm={12}>
          <StatCard
            title="Active Topics"
            value={uniqueTopics}
            prefix={<NodeIndexOutlined />}
            color="#722ed1"
            footer={
              <Text type="secondary" style={{ fontSize: 12 }}>
                Topics with recent events
              </Text>
            }
          />
        </Col>
      </Row>

      {/* Events Table */}
      <Card
        title={
          <Space>
            <AuditOutlined />
            <span>Event Log</span>
          </Space>
        }
        extra={
          <Text type="secondary" style={{ fontSize: 12 }}>
            {data?.count ?? 0} events
          </Text>
        }
      >
        <Table
          columns={columns}
          dataSource={data?.events ?? []}
          rowKey={(record, index) =>
            `${record._timestamp}-${record.event}-${index}`
          }
          loading={isLoading}
          pagination={{
            pageSize: 50,
            showTotal: (total) => `${total} events`,
            showSizeChanger: true,
          }}
          size="small"
          expandable={{
            expandedRowRender: (record) => (
              <div style={{ padding: "8px 0" }}>
                <JsonViewer data={getEventDetails(record)} maxHeight={300} />
              </div>
            ),
            rowExpandable: (record) =>
              Object.keys(getEventDetails(record)).length > 0,
          }}
        />
      </Card>
    </Space>
  );
}
