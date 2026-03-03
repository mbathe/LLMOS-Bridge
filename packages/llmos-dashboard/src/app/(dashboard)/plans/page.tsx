"use client";

import React, { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import {
  Table,
  Space,
  Tag,
  Button,
  Select,
  Typography,
  Card,
  Badge,
  Row,
  Col,
  Tooltip,
} from "antd";
import {
  EyeOutlined,
  StopOutlined,
  ReloadOutlined,
  FileTextOutlined,
  PlayCircleOutlined,
  CheckCircleOutlined,
  CloseCircleOutlined,
  ClockCircleOutlined,
} from "@ant-design/icons";
import { useRouter } from "next/navigation";
import { api } from "@/lib/api/client";
import { PageHeader } from "@/components/common/PageHeader";
import { StatCard } from "@/components/common/StatCard";
import { timeAgo, formatDate, truncateId } from "@/lib/utils/formatters";
import type { PlanListResponse, PlanResponse, PlanStatus } from "@/types/plan";
import type { ColumnsType } from "antd/es/table";

const { Text } = Typography;

const planStatusBadgeMap: Record<PlanStatus, "success" | "processing" | "error" | "default" | "warning"> = {
  pending: "default",
  running: "processing",
  completed: "success",
  failed: "error",
  cancelled: "default",
  paused: "warning",
};

export default function PlansPage() {
  const router = useRouter();
  const [statusFilter, setStatusFilter] = useState<string>("");
  const [page, setPage] = useState(1);

  const { data, isLoading, refetch } = useQuery<PlanListResponse>({
    queryKey: ["plans", statusFilter, page],
    queryFn: () =>
      api.get<PlanListResponse>("/plans", {
        status: statusFilter,
        page: String(page),
        per_page: "20",
      }),
    refetchInterval: 5000,
  });

  const stats = useMemo(() => {
    const plans = data?.plans ?? [];
    return {
      total: data?.total ?? 0,
      running: plans.filter((p) => p.status === "running").length,
      completed: plans.filter((p) => p.status === "completed").length,
      failed: plans.filter((p) => p.status === "failed").length,
    };
  }, [data]);

  const columns: ColumnsType<PlanResponse> = [
    {
      title: "Plan ID",
      dataIndex: "plan_id",
      key: "plan_id",
      width: 180,
      render: (id: string) => (
        <Button
          type="link"
          onClick={() => router.push(`/plans/${id}`)}
          style={{ fontFamily: "monospace", fontSize: 13, padding: 0 }}
        >
          {truncateId(id, 16)}
        </Button>
      ),
    },
    {
      title: "Status",
      dataIndex: "status",
      key: "status",
      width: 160,
      render: (status: PlanStatus) => (
        <Badge
          status={planStatusBadgeMap[status] ?? "default"}
          text={
            <Text style={{ fontSize: 13 }}>
              {status.charAt(0).toUpperCase() + status.slice(1)}
            </Text>
          }
        />
      ),
    },
    {
      title: "Description",
      dataIndex: "description",
      key: "description",
      ellipsis: true,
      render: (desc: string | undefined) => (
        <Text style={{ color: desc ? undefined : "var(--ant-color-text-quaternary)" }}>
          {desc || "No description provided"}
        </Text>
      ),
    },
    {
      title: "Actions",
      dataIndex: "actions",
      key: "action_count",
      width: 100,
      align: "center",
      render: (actions: unknown[]) => (
        <Tag color="blue">{actions?.length ?? 0}</Tag>
      ),
    },
    {
      title: "Created",
      dataIndex: "created_at",
      key: "created_at",
      width: 160,
      render: (d: number) => (
        <Tooltip title={formatDate(d)}>
          <Text type="secondary" style={{ fontSize: 13 }}>
            {timeAgo(d)}
          </Text>
        </Tooltip>
      ),
    },
    {
      title: "Operations",
      key: "ops",
      width: 120,
      align: "center",
      render: (_: unknown, record: PlanResponse) => (
        <Space>
          <Tooltip title="View details">
            <Button
              type="text"
              size="small"
              icon={<EyeOutlined />}
              onClick={() => router.push(`/plans/${record.plan_id}`)}
            />
          </Tooltip>
          {record.status === "running" && (
            <Tooltip title="Cancel plan">
              <Button
                type="text"
                size="small"
                icon={<StopOutlined />}
                danger
                onClick={() => {
                  api.delete(`/plans/${record.plan_id}`).then(() => refetch());
                }}
              />
            </Tooltip>
          )}
        </Space>
      ),
    },
  ];

  const statusOptions = [
    { label: "All", value: "" },
    { label: "Pending", value: "pending" },
    { label: "Running", value: "running" },
    { label: "Completed", value: "completed" },
    { label: "Failed", value: "failed" },
    { label: "Cancelled", value: "cancelled" },
    { label: "Paused", value: "paused" },
  ];

  return (
    <Space direction="vertical" size="large" style={{ width: "100%" }}>
      <PageHeader
        icon={<FileTextOutlined />}
        title="Plans"
        subtitle="Manage and monitor IML plan executions"
        extra={
          <>
            <Select
              value={statusFilter}
              onChange={(v) => {
                setStatusFilter(v);
                setPage(1);
              }}
              options={statusOptions}
              style={{ width: 150 }}
              placeholder="Filter by status"
            />
            <Button icon={<ReloadOutlined />} onClick={() => refetch()}>
              Refresh
            </Button>
          </>
        }
      />

      <Row gutter={[16, 16]}>
        <Col xs={12} sm={6}>
          <StatCard
            title="Total Plans"
            value={stats.total}
            prefix={<FileTextOutlined />}
            color="#1677ff"
            footer={<Text type="secondary" style={{ fontSize: 12 }}>Across all statuses</Text>}
          />
        </Col>
        <Col xs={12} sm={6}>
          <StatCard
            title="Running"
            value={stats.running}
            prefix={<PlayCircleOutlined />}
            color="#1677ff"
            valueStyle={{ color: "#1677ff" }}
            footer={<Text type="secondary" style={{ fontSize: 12 }}>Currently executing</Text>}
            onClick={stats.running > 0 ? () => { setStatusFilter("running"); setPage(1); } : undefined}
          />
        </Col>
        <Col xs={12} sm={6}>
          <StatCard
            title="Completed"
            value={stats.completed}
            prefix={<CheckCircleOutlined />}
            color="#52c41a"
            valueStyle={{ color: "#52c41a" }}
            footer={<Text type="secondary" style={{ fontSize: 12 }}>Successfully finished</Text>}
            onClick={stats.completed > 0 ? () => { setStatusFilter("completed"); setPage(1); } : undefined}
          />
        </Col>
        <Col xs={12} sm={6}>
          <StatCard
            title="Failed"
            value={stats.failed}
            prefix={<CloseCircleOutlined />}
            color="#ff4d4f"
            valueStyle={{ color: "#ff4d4f" }}
            footer={<Text type="secondary" style={{ fontSize: 12 }}>Errors encountered</Text>}
            onClick={stats.failed > 0 ? () => { setStatusFilter("failed"); setPage(1); } : undefined}
          />
        </Col>
      </Row>

      <Card
        title={
          <Space>
            <ClockCircleOutlined />
            <span>Plan History</span>
          </Space>
        }
        styles={{ body: { padding: 0 } }}
      >
        <Table
          columns={columns}
          dataSource={data?.plans ?? []}
          rowKey="plan_id"
          loading={isLoading}
          bordered
          pagination={{
            current: page,
            pageSize: 20,
            total: data?.total ?? 0,
            onChange: setPage,
            showSizeChanger: false,
            showTotal: (total) => `${total} plans`,
          }}
          size="middle"
        />
      </Card>
    </Space>
  );
}
