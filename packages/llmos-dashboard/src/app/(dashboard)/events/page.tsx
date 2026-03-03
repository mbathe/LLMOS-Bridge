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
  HistoryOutlined,
  ReloadOutlined,
  FieldTimeOutlined,
  NodeIndexOutlined,
  SyncOutlined,
  ClockCircleOutlined,
} from "@ant-design/icons";
import { api, ApiError } from "@/lib/api/client";
import { PageHeader } from "@/components/common/PageHeader";
import { StatCard } from "@/components/common/StatCard";
import { JsonViewer } from "@/components/common/JsonViewer";
import { formatTimestamp } from "@/lib/utils/formatters";
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
  { label: "Action Progress", value: "llmos.actions.progress" },
  { label: "Action Results", value: "llmos.actions.results" },
  { label: "Security", value: "llmos.security" },
  { label: "Errors", value: "llmos.errors" },
  { label: "Modules", value: "llmos.modules" },
  { label: "Nodes", value: "llmos.cluster.nodes" },
  { label: "Permissions", value: "llmos.permissions" },
  { label: "Perception", value: "llmos.perception" },
  { label: "IoT", value: "llmos.iot" },
  { label: "Database", value: "llmos.db.changes" },
  { label: "Filesystem", value: "llmos.filesystem" },
  { label: "Triggers", value: "llmos.triggers" },
  { label: "Recordings", value: "llmos.recordings" },
  { label: "Approval", value: "llmos.approval" },
];

const topicColorMap: Record<string, string> = {
  "llmos.plans": "blue",
  "llmos.actions": "cyan",
  "llmos.actions.progress": "geekblue",
  "llmos.actions.results": "geekblue",
  "llmos.security": "red",
  "llmos.errors": "red",
  "llmos.modules": "purple",
  "llmos.cluster.nodes": "green",
  "llmos.permissions": "orange",
  "llmos.perception": "magenta",
  "llmos.iot": "lime",
  "llmos.db.changes": "gold",
  "llmos.filesystem": "volcano",
  "llmos.triggers": "orange",
  "llmos.recordings": "gold",
  "llmos.approval": "volcano",
};

export default function EventsPage() {
  const [topicFilter, setTopicFilter] = useState<string>("");
  const [autoRefresh, setAutoRefresh] = useState(true);

  const { data, isLoading, error, refetch } = useQuery<AuditLogResponse>({
    queryKey: ["events", topicFilter],
    queryFn: () =>
      api.get<AuditLogResponse>("/admin/system/events", {
        limit: "100",
        topic: topicFilter,
      }),
    retry: false,
    refetchInterval: autoRefresh ? 5000 : false,
  });

  const uniqueTopics = useMemo(() => {
    if (!data?.events) return 0;
    const topics = new Set(data.events.map((e) => e._topic).filter(Boolean));
    return topics.size;
  }, [data]);

  const latestTimestamp = useMemo(() => {
    if (!data?.events || data.events.length === 0) return null;
    const sorted = [...data.events].sort(
      (a, b) => (b._timestamp ?? 0) - (a._timestamp ?? 0),
    );
    return sorted[0]?._timestamp ?? null;
  }, [data]);

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
      width: 180,
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
      width: 240,
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
        message="Failed to load events"
        description={getErrorMessage(error)}
        showIcon
      />
    );
  }

  return (
    <Space direction="vertical" size="large" style={{ width: "100%" }}>
      <PageHeader
        icon={<HistoryOutlined />}
        title="Events"
        subtitle="System-wide event stream"
        tags={
          autoRefresh ? (
            <Tag icon={<SyncOutlined spin />} color="processing">
              Live
            </Tag>
          ) : undefined
        }
        extra={
          <Space>
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
              style={{ width: 180 }}
              placeholder="Filter by topic"
            />
            <Button icon={<ReloadOutlined />} onClick={() => refetch()}>
              Refresh
            </Button>
          </Space>
        }
      />

      {/* Stats */}
      <Row gutter={[16, 16]}>
        <Col xs={24} sm={8}>
          <StatCard
            title="Total Events"
            value={data?.count ?? 0}
            prefix={<FieldTimeOutlined />}
            color="#1677ff"
            footer={
              <Text type="secondary" style={{ fontSize: 12 }}>
                {topicFilter
                  ? `Filtered by ${topicFilter.replace("llmos.", "")}`
                  : "All topics (ring buffer)"}
              </Text>
            }
          />
        </Col>
        <Col xs={24} sm={8}>
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
        <Col xs={24} sm={8}>
          <StatCard
            title="Latest Event"
            value={
              latestTimestamp ? formatTimestamp(latestTimestamp) : "—"
            }
            prefix={<ClockCircleOutlined />}
            color="#13c2c2"
            valueStyle={{ fontSize: 14 }}
            footer={
              <Text type="secondary" style={{ fontSize: 12 }}>
                Most recent event timestamp
              </Text>
            }
          />
        </Col>
      </Row>

      {/* Events Table */}
      <Card
        title={
          <Space>
            <HistoryOutlined />
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
