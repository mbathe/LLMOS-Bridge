"use client";

import React, { useEffect, useState } from "react";
import {
  Modal,
  Form,
  Button,
  Typography,
  Space,
  Alert,
  Spin,
  Divider,
  Result,
} from "antd";
import {
  PlayCircleOutlined,
  CheckCircleOutlined,
  CloseCircleOutlined,
  LoadingOutlined,
} from "@ant-design/icons";
import { useMutation, useQuery } from "@tanstack/react-query";
import type { ActionSpec, JSONSchemaProperty } from "@/types/module";
import { api } from "@/lib/api/client";
import { JsonViewer } from "@/components/common/JsonViewer";
import { SchemaFormField } from "./SchemaFormField";

const { Text, Paragraph } = Typography;

// ── Types ──

interface ActionTryModalProps {
  action: ActionSpec | null;
  moduleId: string;
  onClose: () => void;
}

interface PlanSubmission {
  plan_id: string;
  protocol_version: string;
  description: string;
  actions: Array<{
    id: string;
    action: string;
    module: string;
    params: Record<string, unknown>;
  }>;
}

interface PlanStatusResponse {
  plan_id: string;
  status: string;
  actions?: Array<{
    id: string;
    status: string;
    result?: unknown;
    error?: string;
  }>;
  [key: string]: unknown;
}

// ── Component ──

export function ActionTryModal({
  action,
  moduleId,
  onClose,
}: ActionTryModalProps) {
  const [form] = Form.useForm();
  const [submittedPlanId, setSubmittedPlanId] = useState<string | null>(null);
  const [result, setResult] = useState<PlanStatusResponse | null>(null);
  const [error, setError] = useState<string | null>(null);

  const isOpen = action !== null;

  // Extract schema properties from the action
  const schemaProperties: Record<string, JSONSchemaProperty> =
    (action?.params_schema?.properties as Record<string, JSONSchemaProperty>) ??
    {};
  const requiredFields: string[] =
    (action?.params_schema?.required as string[]) ?? [];
  const hasProperties = Object.keys(schemaProperties).length > 0;

  // ── Plan submission mutation ──
  const submitPlan = useMutation<PlanStatusResponse, Error, PlanSubmission>({
    mutationFn: (plan) => api.post<PlanStatusResponse>("/plans", plan),
    onSuccess: (data) => {
      setSubmittedPlanId(data.plan_id);
    },
    onError: (err) => {
      setError(err.message);
    },
  });

  // ── Polling query for plan status ──
  const planStatus = useQuery<PlanStatusResponse>({
    queryKey: ["plan-try", submittedPlanId],
    queryFn: () =>
      api.get<PlanStatusResponse>(`/plans/${submittedPlanId}`),
    enabled: submittedPlanId !== null && result === null,
    refetchInterval: 1000,
  });

  // Watch the polling query and resolve when done
  useEffect(() => {
    if (!planStatus.data) return;
    const status = planStatus.data.status;
    if (status !== "running" && status !== "pending") {
      setResult(planStatus.data);
    }
  }, [planStatus.data]);

  // ── Cleanup on close ──
  const handleClose = () => {
    form.resetFields();
    setSubmittedPlanId(null);
    setResult(null);
    setError(null);
    submitPlan.reset();
    onClose();
  };

  // ── Execute action ──
  const handleExecute = async () => {
    try {
      const values = hasProperties ? await form.validateFields() : {};
      setError(null);
      setResult(null);

      const planId = `dashboard-try-${Date.now()}`;
      const plan: PlanSubmission = {
        plan_id: planId,
        protocol_version: "2.0",
        description: `Dashboard test: ${moduleId}.${action!.name}`,
        actions: [
          {
            id: "try_1",
            action: action!.name,
            module: moduleId,
            params: values,
          },
        ],
      };

      submitPlan.mutate(plan);
    } catch {
      // form validation failed, antd will show field errors
    }
  };

  const isLoading = submitPlan.isPending || (submittedPlanId !== null && result === null);

  return (
    <Modal
      open={isOpen}
      title={
        <Space>
          <PlayCircleOutlined />
          <span>
            Try Action:{" "}
            <Text code>
              {moduleId}.{action?.name}
            </Text>
          </span>
        </Space>
      }
      width={700}
      onCancel={handleClose}
      footer={[
        <Button key="cancel" onClick={handleClose}>
          Close
        </Button>,
        <Button
          key="execute"
          type="primary"
          icon={<PlayCircleOutlined />}
          onClick={handleExecute}
          loading={isLoading}
          disabled={result !== null}
        >
          Execute
        </Button>,
      ]}
      destroyOnClose
    >
      {action && (
        <Space direction="vertical" size="middle" style={{ width: "100%" }}>
          {/* Description */}
          {action.description && (
            <Paragraph type="secondary">{action.description}</Paragraph>
          )}

          {/* Parameters Form */}
          {hasProperties ? (
            <Form
              form={form}
              layout="vertical"
              disabled={isLoading || result !== null}
              initialValues={getDefaultValues(schemaProperties)}
            >
              {Object.entries(schemaProperties).map(([name, property]) => (
                <SchemaFormField
                  key={name}
                  name={name}
                  property={property}
                  required={requiredFields.includes(name)}
                />
              ))}
            </Form>
          ) : (
            <Alert
              message="This action takes no parameters"
              type="info"
              showIcon
            />
          )}

          {/* Loading State */}
          {isLoading && (
            <>
              <Divider />
              <div style={{ textAlign: "center", padding: 16 }}>
                <Space direction="vertical" align="center">
                  <Spin
                    indicator={<LoadingOutlined style={{ fontSize: 24 }} spin />}
                  />
                  <Text type="secondary">
                    {submitPlan.isPending
                      ? "Submitting plan..."
                      : "Waiting for execution..."}
                  </Text>
                </Space>
              </div>
            </>
          )}

          {/* Error */}
          {error && (
            <>
              <Divider />
              <Alert
                message="Execution Failed"
                description={error}
                type="error"
                showIcon
                icon={<CloseCircleOutlined />}
              />
            </>
          )}

          {/* Result */}
          {result && (
            <>
              <Divider />
              {result.status === "completed" ? (
                <Result
                  status="success"
                  title="Action Completed"
                  subTitle={`Plan ${result.plan_id} finished successfully`}
                  icon={<CheckCircleOutlined />}
                  style={{ padding: "16px 0" }}
                />
              ) : (
                <Result
                  status="error"
                  title={`Plan ${result.status}`}
                  subTitle={
                    result.actions?.[0]?.error ??
                    "The action did not complete successfully"
                  }
                  icon={<CloseCircleOutlined />}
                  style={{ padding: "16px 0" }}
                />
              )}
              <JsonViewer data={result} maxHeight={300} />
            </>
          )}
        </Space>
      )}
    </Modal>
  );
}

// ── Helpers ──

function getDefaultValues(
  properties: Record<string, JSONSchemaProperty>,
): Record<string, unknown> {
  const defaults: Record<string, unknown> = {};
  for (const [name, prop] of Object.entries(properties)) {
    if (prop.default !== undefined) {
      defaults[name] = prop.default;
    }
  }
  return defaults;
}
