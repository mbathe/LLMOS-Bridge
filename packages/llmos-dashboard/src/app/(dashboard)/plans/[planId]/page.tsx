"use client";

import React, { useState, useCallback, useMemo } from "react";
import { useParams, useRouter } from "next/navigation";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import {
  Card,
  Typography,
  Space,
  Tag,
  Descriptions,
  Timeline,
  Button,
  Spin,
  Alert,
  Modal,
  Radio,
  Input,
  Row,
  Col,
  Badge,
  Tooltip,
  Divider,
} from "antd";
import {
  ArrowLeftOutlined,
  StopOutlined,
  CheckCircleOutlined,
  CloseCircleOutlined,
  ClockCircleOutlined,
  LoadingOutlined,
  ExclamationCircleOutlined,
  MinusCircleOutlined,
  RollbackOutlined,
  FileTextOutlined,
  ThunderboltOutlined,
  FieldTimeOutlined,
  WarningOutlined,
} from "@ant-design/icons";
import { api } from "@/lib/api/client";
import { PageHeader } from "@/components/common/PageHeader";
import { StatCard } from "@/components/common/StatCard";
import { StatusBadge } from "@/components/common/StatusBadge";
import { JsonViewer } from "@/components/common/JsonViewer";
import { useSSE } from "@/hooks/useSSE";
import { formatDate, timeAgo } from "@/lib/utils/formatters";
import type {
  PlanResponse,
  ActionResult,
  ActionStatus,
  ApprovalDecision,
  ApprovePlanActionRequest,
} from "@/types/plan";

const { Text } = Typography;
const { TextArea } = Input;

const actionStatusIcon: Record<ActionStatus, React.ReactNode> = {
  pending: <ClockCircleOutlined style={{ color: "#8c8c8c" }} />,
  running: <LoadingOutlined style={{ color: "#1677ff" }} />,
  completed: <CheckCircleOutlined style={{ color: "#52c41a" }} />,
  failed: <CloseCircleOutlined style={{ color: "#ff4d4f" }} />,
  skipped: <MinusCircleOutlined style={{ color: "#8c8c8c" }} />,
  rolled_back: <RollbackOutlined style={{ color: "#722ed1" }} />,
  awaiting_approval: <ExclamationCircleOutlined style={{ color: "#faad14" }} />,
};

const actionStatusBadgeMap: Record<ActionStatus, "success" | "processing" | "error" | "default" | "warning"> = {
  pending: "default",
  running: "processing",
  completed: "success",
  failed: "error",
  skipped: "default",
  rolled_back: "warning",
  awaiting_approval: "warning",
};

function computeDuration(createdAt: number, updatedAt: number): string {
  const diffSec = Math.max(0, Math.round(updatedAt - createdAt));
  if (diffSec < 60) return `${diffSec}s`;
  const mins = Math.floor(diffSec / 60);
  const secs = diffSec % 60;
  if (mins < 60) return `${mins}m ${secs}s`;
  const hours = Math.floor(mins / 60);
  const remMins = mins % 60;
  return `${hours}h ${remMins}m`;
}

function computeActionDuration(action: ActionResult): string | null {
  if (!action.started_at || !action.finished_at) return null;
  const start = new Date(action.started_at).getTime();
  const end = new Date(action.finished_at).getTime();
  const diffMs = Math.max(0, end - start);
  if (diffMs < 1000) return `${diffMs}ms`;
  const secs = Math.round(diffMs / 1000);
  if (secs < 60) return `${secs}s`;
  const mins = Math.floor(secs / 60);
  return `${mins}m ${secs % 60}s`;
}

const approvalDescriptions: Record<ApprovalDecision, string> = {
  approve: "Allow this action to execute with its current parameters.",
  reject: "Block this action from executing. The plan will handle the rejection based on its on_error policy.",
  skip: "Skip this action entirely and continue executing the rest of the plan.",
  modify: "Approve with modified parameters. You can change the action parameters before execution.",
  approve_always: "Approve this action and automatically approve all future occurrences of this action type.",
};

export default function PlanDetailPage() {
  const { planId } = useParams<{ planId: string }>();
  const router = useRouter();
  const queryClient = useQueryClient();

  const [approvalModalOpen, setApprovalModalOpen] = useState(false);
  const [approvalActionId, setApprovalActionId] = useState<string | null>(null);
  const [approvalDecision, setApprovalDecision] = useState<ApprovalDecision>("approve");
  const [approvalReason, setApprovalReason] = useState("");

  const {
    data: plan,
    isLoading,
    error,
  } = useQuery<PlanResponse>({
    queryKey: ["plans", planId],
    queryFn: () => api.get<PlanResponse>(`/plans/${planId}`),
    refetchInterval: 3000,
  });

  // SSE for real-time updates
  useSSE(`/plans/${planId}/stream`, {
    enabled: plan?.status === "running" || plan?.status === "pending",
    onEvent: useCallback(
      (eventType: string) => {
        if (
          eventType === "action_result_ready" ||
          eventType === "plan_completed" ||
          eventType === "plan_failed"
        ) {
          queryClient.invalidateQueries({ queryKey: ["plans", planId] });
        }
      },
      [planId, queryClient],
    ),
  });

  const cancelMutation = useMutation({
    mutationFn: () => api.delete(`/plans/${planId}`),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["plans", planId] });
    },
  });

  const approveMutation = useMutation({
    mutationFn: (params: { actionId: string; body: ApprovePlanActionRequest }) =>
      api.post(`/plans/${planId}/actions/${params.actionId}/approve`, params.body),
    onSuccess: () => {
      setApprovalModalOpen(false);
      queryClient.invalidateQueries({ queryKey: ["plans", planId] });
    },
  });

  const handleApproval = (actionId: string) => {
    setApprovalActionId(actionId);
    setApprovalDecision("approve");
    setApprovalReason("");
    setApprovalModalOpen(true);
  };

  const submitApproval = () => {
    if (!approvalActionId) return;
    approveMutation.mutate({
      actionId: approvalActionId,
      body: {
        decision: approvalDecision,
        reason: approvalReason || undefined,
      },
    });
  };

  const stats = useMemo(() => {
    const actions = plan?.actions ?? [];
    return {
      total: actions.length,
      completed: actions.filter((a) => a.status === "completed").length,
      failed: actions.filter((a) => a.status === "failed").length,
      awaiting: actions.filter((a) => a.status === "awaiting_approval").length,
      duration: plan ? computeDuration(plan.created_at, plan.updated_at) : "-",
    };
  }, [plan]);

  if (isLoading) {
    return (
      <div style={{ textAlign: "center", padding: 80 }}>
        <Spin size="large" />
      </div>
    );
  }

  if (error || !plan) {
    return (
      <Alert
        type="error"
        message="Failed to load plan"
        description={error instanceof Error ? error.message : "Unknown error"}
        showIcon
      />
    );
  }

  const hasAwaitingApproval = (plan.actions ?? []).some(
    (a) => a.status === "awaiting_approval"
  );

  return (
    <Space direction="vertical" size="large" style={{ width: "100%" }}>
      <PageHeader
        icon={<FileTextOutlined />}
        title={plan.plan_id}
        subtitle={
          `Created ${formatDate(plan.created_at)} | Updated ${timeAgo(plan.updated_at)}`
        }
        tags={<StatusBadge type="plan" status={plan.status} />}
        extra={
          <>
            <Button
              icon={<ArrowLeftOutlined />}
              onClick={() => router.push("/plans")}
            >
              Back to Plans
            </Button>
            {(plan.status === "running" || plan.status === "pending") && (
              <Button
                danger
                type="primary"
                icon={<StopOutlined />}
                loading={cancelMutation.isPending}
                onClick={() => cancelMutation.mutate()}
              >
                Cancel Plan
              </Button>
            )}
          </>
        }
      />

      {/* Approval Alert Banner */}
      {hasAwaitingApproval && (
        <Alert
          type="warning"
          showIcon
          icon={<ExclamationCircleOutlined />}
          message="Actions Awaiting Approval"
          description={`${stats.awaiting} action${stats.awaiting !== 1 ? "s" : ""} require${stats.awaiting === 1 ? "s" : ""} your review before the plan can continue executing.`}
          banner
        />
      )}

      {/* Stat Cards */}
      <Row gutter={[16, 16]}>
        <Col xs={12} sm={6}>
          <StatCard
            title="Total Actions"
            value={stats.total}
            prefix={<ThunderboltOutlined />}
            color="#1677ff"
            footer={<Text type="secondary" style={{ fontSize: 12 }}>In this plan</Text>}
          />
        </Col>
        <Col xs={12} sm={6}>
          <StatCard
            title="Completed"
            value={stats.completed}
            prefix={<CheckCircleOutlined />}
            color="#52c41a"
            valueStyle={{ color: "#52c41a" }}
            footer={
              stats.total > 0 ? (
                <Text type="secondary" style={{ fontSize: 12 }}>
                  {Math.round((stats.completed / stats.total) * 100)}% of total
                </Text>
              ) : undefined
            }
          />
        </Col>
        <Col xs={12} sm={6}>
          <StatCard
            title="Failed"
            value={stats.failed}
            prefix={<CloseCircleOutlined />}
            color="#ff4d4f"
            valueStyle={{ color: stats.failed > 0 ? "#ff4d4f" : undefined }}
            footer={
              stats.failed > 0 ? (
                <Text type="danger" style={{ fontSize: 12 }}>Requires attention</Text>
              ) : (
                <Text type="secondary" style={{ fontSize: 12 }}>No errors</Text>
              )
            }
          />
        </Col>
        <Col xs={12} sm={6}>
          <StatCard
            title="Duration"
            value={stats.duration}
            prefix={<FieldTimeOutlined />}
            color="#722ed1"
            footer={
              <Text type="secondary" style={{ fontSize: 12 }}>
                {plan.status === "running" ? "Still running" : "Total elapsed"}
              </Text>
            }
          />
        </Col>
      </Row>

      {/* Plan Info */}
      <Card
        title={
          <Space>
            <FileTextOutlined />
            <span>Plan Details</span>
          </Space>
        }
      >
        <Descriptions
          column={{ xs: 1, sm: 2, lg: 3 }}
          bordered
          size="small"
        >
          <Descriptions.Item label="Plan ID">
            <Text code style={{ fontSize: 12 }}>{plan.plan_id}</Text>
          </Descriptions.Item>
          <Descriptions.Item label="Status">
            <StatusBadge type="plan" status={plan.status} showDot />
          </Descriptions.Item>
          <Descriptions.Item label="Description">
            {plan.description || (
              <Text type="secondary" italic>No description provided</Text>
            )}
          </Descriptions.Item>
          <Descriptions.Item label="Created">
            <Tooltip title={formatDate(plan.created_at)}>
              {timeAgo(plan.created_at)}
            </Tooltip>
          </Descriptions.Item>
          <Descriptions.Item label="Last Updated">
            <Tooltip title={formatDate(plan.updated_at)}>
              {timeAgo(plan.updated_at)}
            </Tooltip>
          </Descriptions.Item>
          <Descriptions.Item label="Actions Count">
            <Tag color="blue">{plan.actions?.length ?? 0}</Tag>
          </Descriptions.Item>
        </Descriptions>
      </Card>

      {/* Rejection Details */}
      {plan.rejection_details && (
        <Alert
          type="error"
          message="Plan Rejected"
          description={<JsonViewer data={plan.rejection_details} maxHeight={200} />}
          showIcon
        />
      )}

      {/* Action Timeline */}
      <Card
        title={
          <Space>
            <ClockCircleOutlined />
            <span>Action Timeline</span>
            <Tag>{plan.actions?.length ?? 0} actions</Tag>
          </Space>
        }
      >
        <Timeline
          items={(plan.actions ?? []).map((action: ActionResult) => {
            const duration = computeActionDuration(action);
            return {
              dot: actionStatusIcon[action.status],
              children: (
                <Card
                  size="small"
                  style={{
                    borderLeft: `3px solid ${
                      action.status === "completed"
                        ? "#52c41a"
                        : action.status === "failed"
                        ? "#ff4d4f"
                        : action.status === "running"
                        ? "#1677ff"
                        : action.status === "awaiting_approval"
                        ? "#faad14"
                        : "#d9d9d9"
                    }`,
                    marginBottom: 4,
                  }}
                  styles={{ body: { padding: "12px 16px" } }}
                >
                  <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start" }}>
                    <div>
                      <Space size="small" wrap>
                        <Text strong style={{ fontSize: 14 }}>
                          {action.action}
                        </Text>
                        <Tag color="geekblue">{action.module}</Tag>
                        <Badge
                          status={actionStatusBadgeMap[action.status] ?? "default"}
                          text={
                            <Text style={{ fontSize: 12 }}>
                              {action.status.replace(/_/g, " ").replace(/\b\w/g, (l) => l.toUpperCase())}
                            </Text>
                          }
                        />
                      </Space>
                      <div style={{ marginTop: 4 }}>
                        <Text type="secondary" style={{ fontSize: 11, fontFamily: "monospace" }}>
                          {action.action_id}
                        </Text>
                      </div>
                    </div>
                    {duration && (
                      <Tooltip title="Execution duration">
                        <Tag icon={<FieldTimeOutlined />} color="default">
                          {duration}
                        </Tag>
                      </Tooltip>
                    )}
                  </div>

                  {action.status === "awaiting_approval" && (
                    <div style={{ marginTop: 10 }}>
                      <Button
                        type="primary"
                        size="small"
                        icon={<ExclamationCircleOutlined />}
                        onClick={() => handleApproval(action.action_id)}
                      >
                        Review & Approve
                      </Button>
                    </div>
                  )}

                  {action.error && (
                    <Alert
                      type="error"
                      message={action.error}
                      style={{ marginTop: 10 }}
                      showIcon
                    />
                  )}

                  {action.result && (
                    <div style={{ marginTop: 10 }}>
                      <JsonViewer data={action.result} maxHeight={200} />
                    </div>
                  )}

                  {action.started_at && (
                    <div style={{ marginTop: 8, display: "flex", gap: 16 }}>
                      <Text type="secondary" style={{ fontSize: 11 }}>
                        <ClockCircleOutlined style={{ marginRight: 4 }} />
                        Started: {formatDate(action.started_at)}
                      </Text>
                      {action.finished_at && (
                        <Text type="secondary" style={{ fontSize: 11 }}>
                          <CheckCircleOutlined style={{ marginRight: 4 }} />
                          Finished: {formatDate(action.finished_at)}
                        </Text>
                      )}
                    </div>
                  )}
                </Card>
              ),
            };
          })}
        />
      </Card>

      {/* Approval Modal */}
      <Modal
        title={
          <Space>
            <ExclamationCircleOutlined style={{ color: "#faad14" }} />
            <span>Action Approval Required</span>
          </Space>
        }
        open={approvalModalOpen}
        onCancel={() => setApprovalModalOpen(false)}
        onOk={submitApproval}
        confirmLoading={approveMutation.isPending}
        okText="Submit Decision"
        width={520}
      >
        <Space direction="vertical" size="middle" style={{ width: "100%" }}>
          <Text type="secondary">
            Choose how to handle this action. Your decision will affect plan execution.
          </Text>
          <Divider style={{ margin: "8px 0" }} />
          <Radio.Group
            value={approvalDecision}
            onChange={(e) => setApprovalDecision(e.target.value)}
            style={{ width: "100%" }}
          >
            <Space direction="vertical" style={{ width: "100%" }}>
              {(
                [
                  { value: "approve", label: "Approve", icon: <CheckCircleOutlined style={{ color: "#52c41a" }} /> },
                  { value: "reject", label: "Reject", icon: <CloseCircleOutlined style={{ color: "#ff4d4f" }} /> },
                  { value: "skip", label: "Skip", icon: <MinusCircleOutlined style={{ color: "#8c8c8c" }} /> },
                  { value: "modify", label: "Modify", icon: <WarningOutlined style={{ color: "#faad14" }} /> },
                  { value: "approve_always", label: "Approve Always", icon: <CheckCircleOutlined style={{ color: "#722ed1" }} /> },
                ] as const
              ).map((opt) => (
                <Card
                  key={opt.value}
                  size="small"
                  hoverable
                  onClick={() => setApprovalDecision(opt.value)}
                  style={{
                    cursor: "pointer",
                    border: approvalDecision === opt.value
                      ? "1px solid #1677ff"
                      : "1px solid var(--ant-color-border)",
                    background: approvalDecision === opt.value
                      ? "var(--ant-color-primary-bg)"
                      : undefined,
                  }}
                  styles={{ body: { padding: "8px 12px" } }}
                >
                  <Radio value={opt.value}>
                    <Space>
                      {opt.icon}
                      <div>
                        <Text strong>{opt.label}</Text>
                        <br />
                        <Text type="secondary" style={{ fontSize: 12 }}>
                          {approvalDescriptions[opt.value]}
                        </Text>
                      </div>
                    </Space>
                  </Radio>
                </Card>
              ))}
            </Space>
          </Radio.Group>
          <Divider style={{ margin: "8px 0" }} />
          <div>
            <Text strong style={{ display: "block", marginBottom: 4, fontSize: 13 }}>
              Reason (optional)
            </Text>
            <TextArea
              placeholder="Provide a reason for your decision..."
              value={approvalReason}
              onChange={(e) => setApprovalReason(e.target.value)}
              rows={3}
            />
          </div>
        </Space>
      </Modal>
    </Space>
  );
}
