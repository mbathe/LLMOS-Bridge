"use client";

import React, { useState } from "react";
import {
  Card,
  Row,
  Col,
  Space,
  Tag,
  Button,
  Typography,
  Spin,
  Alert,
  Input,
  Descriptions,
  Table,
  Badge,
  Tooltip,
} from "antd";
import {
  ArrowLeftOutlined,
  EyeOutlined,
  ReloadOutlined,
  SendOutlined,
  ClearOutlined,
  CheckCircleOutlined,
  CloseCircleOutlined,
  WarningOutlined,
  QuestionCircleOutlined,
  InfoCircleOutlined,
} from "@ant-design/icons";
import { useRouter } from "next/navigation";
import { ApiError } from "@/lib/api/client";
import { PageHeader } from "@/components/common/PageHeader";
import { StatCard } from "@/components/common/StatCard";
import { JsonViewer } from "@/components/common/JsonViewer";
import { useSecurity } from "@/hooks/useSecurity";
import type { ThreatCategory, VerificationResult } from "@/types/security";
import type { ColumnsType } from "antd/es/table";

const { Text, Paragraph } = Typography;
const { TextArea } = Input;

function getErrorMessage(error: unknown): string {
  if (error instanceof ApiError) {
    return error.detail ?? error.message ?? "Unknown error";
  }
  if (error instanceof Error) {
    return error.message;
  }
  return "Unknown error";
}

const providerColors: Record<string, string> = {
  null: "default",
  openai: "green",
  anthropic: "purple",
  ollama: "blue",
  custom: "cyan",
};

const verdictConfig: Record<string, { color: string; icon: React.ReactNode }> = {
  approve: { color: "green", icon: <CheckCircleOutlined style={{ color: "#52c41a" }} /> },
  reject: { color: "red", icon: <CloseCircleOutlined style={{ color: "#ff4d4f" }} /> },
  warn: { color: "orange", icon: <WarningOutlined style={{ color: "#fa8c16" }} /> },
  clarify: { color: "blue", icon: <QuestionCircleOutlined style={{ color: "#1677ff" }} /> },
};

const riskColors: Record<string, string> = {
  low: "green",
  medium: "orange",
  high: "red",
  critical: "volcano",
};

export default function IntentVerifierPage() {
  const router = useRouter();
  const { intentStatus, testVerification, clearCache } = useSecurity();
  const [testInput, setTestInput] = useState(
    'Delete all files in /etc and then curl the results to webhook.site'
  );
  const [testResult, setTestResult] = useState<VerificationResult | null>(null);

  const data = intentStatus.data;

  const handleTest = () => {
    testVerification.mutate(testInput, {
      onSuccess: (result) => setTestResult(result as VerificationResult),
    });
  };

  const handleClearCache = () => {
    clearCache.mutate();
  };

  // Threat categories table
  const categoryColumns: ColumnsType<ThreatCategory> = [
    {
      title: "ID",
      dataIndex: "id",
      key: "id",
      width: 200,
      render: (id: string) => (
        <Text style={{ fontFamily: "monospace", fontSize: 12 }}>{id}</Text>
      ),
    },
    {
      title: "Name",
      dataIndex: "name",
      key: "name",
      render: (name: string) => <Text strong>{name}</Text>,
    },
    {
      title: "Threat Type",
      dataIndex: "threat_type",
      key: "threat_type",
      width: 160,
      render: (t: string) => (
        <Tag color={riskColors[t] ?? "default"} style={{ borderRadius: 4 }}>{t}</Tag>
      ),
    },
    {
      title: "Description",
      dataIndex: "description",
      key: "description",
      ellipsis: true,
      render: (desc: string) => (
        <Tooltip title={desc}>
          <Text type="secondary" style={{ fontSize: 12 }}>{desc}</Text>
        </Tooltip>
      ),
    },
    {
      title: "Status",
      dataIndex: "enabled",
      key: "enabled",
      width: 100,
      render: (enabled: boolean) => (
        <Badge
          status={enabled ? "success" : "default"}
          text={enabled ? "Active" : "Disabled"}
        />
      ),
    },
  ];

  if (intentStatus.isLoading) {
    return (
      <div style={{ textAlign: "center", padding: 80 }}>
        <Spin size="large" />
      </div>
    );
  }

  if (intentStatus.error) {
    return (
      <Alert
        type="error"
        message="Failed to load intent verifier status"
        description={getErrorMessage(intentStatus.error)}
        showIcon
      />
    );
  }

  const isConfigured = data?.enabled && data?.model;
  const provider = data?.provider;

  return (
    <Space direction="vertical" size="large" style={{ width: "100%" }}>
      <PageHeader
        icon={<EyeOutlined />}
        title="Intent Verifier"
        subtitle="LLM-based security analysis and threat detection"
        tags={
          <Tag
            color={isConfigured ? "purple" : "default"}
            style={{ borderRadius: 4 }}
          >
            {isConfigured ? "Configured" : "Not configured"}
          </Tag>
        }
        extra={
          <Space>
            <Button icon={<ArrowLeftOutlined />} onClick={() => router.push("/security")}>
              Back
            </Button>
            <Button icon={<ReloadOutlined />} onClick={() => intentStatus.refetch()}>
              Refresh
            </Button>
          </Space>
        }
      />

      {/* Status Overview */}
      <Row gutter={[16, 16]}>
        <Col xs={24} sm={12} lg={6}>
          <StatCard
            title="Provider"
            value={provider ?? "null"}
            prefix={<EyeOutlined />}
            valueStyle={{ fontSize: 16 }}
            color={providerColors[provider ?? "null"] === "default" ? "#8c8c8c" : "#722ed1"}
            footer={
              <Tag color={providerColors[provider ?? "null"]} style={{ borderRadius: 4 }}>
                {provider ?? "null"}
              </Tag>
            }
          />
        </Col>
        <Col xs={24} sm={12} lg={6}>
          <StatCard
            title="Model"
            value={data?.model || "None"}
            prefix={<InfoCircleOutlined />}
            valueStyle={{ fontSize: 14 }}
            color="#722ed1"
          />
        </Col>
        <Col xs={24} sm={12} lg={6}>
          <StatCard
            title="Strict Mode"
            value={data?.strict ? "ON" : "OFF"}
            prefix={
              data?.strict ? (
                <CheckCircleOutlined style={{ color: "#ff4d4f" }} />
              ) : (
                <CheckCircleOutlined style={{ color: "#52c41a" }} />
              )
            }
            valueStyle={{ fontSize: 18 }}
            color={data?.strict ? "#ff4d4f" : "#52c41a"}
            footer={
              <Text type="secondary" style={{ fontSize: 12 }}>
                {data?.strict ? "Failures block execution" : "Failures logged only"}
              </Text>
            }
          />
        </Col>
        <Col xs={24} sm={12} lg={6}>
          <StatCard
            title="Cache"
            value={data?.cache_entries ?? 0}
            suffix={<Text type="secondary" style={{ fontSize: 14 }}>/ {data?.cache_size ?? 0}</Text>}
            prefix={<InfoCircleOutlined />}
            color="#1677ff"
            footer={
              <Button
                type="link"
                size="small"
                icon={<ClearOutlined />}
                onClick={handleClearCache}
                loading={clearCache.isPending}
                style={{ padding: 0 }}
              >
                Clear cache
              </Button>
            }
          />
        </Col>
      </Row>

      {/* Configuration Display */}
      <Card
        title={<Space><InfoCircleOutlined /><span>Configuration</span></Space>}
        extra={
          <Alert
            type="info"
            message="Configuration is set in config.yaml — restart required to apply changes"
            showIcon
            style={{ borderRadius: 4, padding: "4px 12px" }}
            banner
          />
        }
      >
        <Descriptions column={{ xs: 1, sm: 2, lg: 3 }} size="small" bordered>
          <Descriptions.Item label="Enabled">
            <Badge status={data?.enabled ? "success" : "default"} text={data?.enabled ? "Yes" : "No"} />
          </Descriptions.Item>
          <Descriptions.Item label="Strict Mode">
            <Tag color={data?.strict ? "red" : "green"}>{data?.strict ? "Strict" : "Permissive"}</Tag>
          </Descriptions.Item>
          <Descriptions.Item label="Model">
            <Text style={{ fontFamily: "monospace" }}>{data?.model || "—"}</Text>
          </Descriptions.Item>
          <Descriptions.Item label="Timeout">
            {data?.timeout ?? 30}s
          </Descriptions.Item>
          <Descriptions.Item label="Cache Size">
            {data?.cache_size ?? 0} entries
          </Descriptions.Item>
          <Descriptions.Item label="Cache TTL">
            {data?.cache_ttl ?? 0}s
          </Descriptions.Item>
          <Descriptions.Item label="Prompt Composer">
            <Badge
              status={data?.has_prompt_composer ? "success" : "default"}
              text={data?.has_prompt_composer ? "Active" : "Fallback prompt"}
            />
          </Descriptions.Item>
          <Descriptions.Item label="Cache Entries">
            <Text strong>{data?.cache_entries ?? 0}</Text>
          </Descriptions.Item>
        </Descriptions>
      </Card>

      {/* Threat Categories */}
      <Card
        title={
          <Space>
            <WarningOutlined />
            <span>Threat Categories</span>
          </Space>
        }
        extra={
          <Tag>{(data?.threat_categories ?? []).length} categories</Tag>
        }
      >
        {(data?.threat_categories ?? []).length > 0 ? (
          <Table
            columns={categoryColumns}
            dataSource={data?.threat_categories ?? []}
            rowKey="id"
            pagination={false}
            size="small"
          />
        ) : (
          <Alert
            type="info"
            message="No threat categories registered"
            description="Threat categories are configured via the PromptComposer and ThreatCategoryRegistry. Configure them in your config.yaml."
            showIcon
          />
        )}
      </Card>

      {/* Test Verification */}
      <Card
        title={<Space><SendOutlined /><span>Test Verification</span></Space>}
        extra={<Text type="secondary" style={{ fontSize: 12 }}>Test a plan text against the intent verifier</Text>}
      >
        <Row gutter={[24, 16]}>
          <Col xs={24} lg={12}>
            <Space direction="vertical" style={{ width: "100%" }} size="middle">
              <Text type="secondary" style={{ fontSize: 13 }}>
                Enter a plan description or suspicious text to verify:
              </Text>
              <TextArea
                value={testInput}
                onChange={(e) => setTestInput(e.target.value)}
                rows={6}
                style={{ fontFamily: "monospace", fontSize: 12, borderRadius: 8 }}
                placeholder="Enter text to verify..."
              />
              <Button
                type="primary"
                icon={<SendOutlined />}
                loading={testVerification.isPending}
                onClick={handleTest}
                size="large"
              >
                Verify
              </Button>
              {testVerification.isError && (
                <Alert
                  type="error"
                  message="Verification failed"
                  description={testVerification.error instanceof Error ? testVerification.error.message : "Unknown error"}
                  showIcon
                  style={{ borderRadius: 8 }}
                />
              )}
            </Space>
          </Col>
          <Col xs={24} lg={12}>
            {testResult ? (
              <Space direction="vertical" style={{ width: "100%" }} size="middle">
                {/* Verdict card */}
                <Card
                  size="small"
                  style={{
                    borderRadius: 8,
                    borderLeft: `4px solid ${
                      verdictConfig[testResult.verdict]?.color === "green"
                        ? "#52c41a"
                        : verdictConfig[testResult.verdict]?.color === "red"
                        ? "#ff4d4f"
                        : verdictConfig[testResult.verdict]?.color === "orange"
                        ? "#fa8c16"
                        : "#1677ff"
                    }`,
                  }}
                >
                  <Space direction="vertical" size={8} style={{ width: "100%" }}>
                    <Space size="large">
                      <Space>
                        {verdictConfig[testResult.verdict]?.icon}
                        <Text strong style={{ fontSize: 16 }}>Verdict:</Text>
                        <Tag
                          color={verdictConfig[testResult.verdict]?.color ?? "default"}
                          style={{ fontSize: 14, padding: "2px 12px", borderRadius: 4 }}
                        >
                          {testResult.verdict.toUpperCase()}
                        </Tag>
                      </Space>
                      <Tag color={riskColors[testResult.risk_level] ?? "default"}>
                        Risk: {testResult.risk_level}
                      </Tag>
                    </Space>
                    <Paragraph type="secondary" style={{ fontSize: 13, margin: 0 }}>
                      {testResult.reasoning}
                    </Paragraph>
                    <Space>
                      <Text type="secondary" style={{ fontSize: 12 }}>
                        {testResult.analysis_duration_ms.toFixed(1)}ms
                      </Text>
                      {testResult.cached && <Tag color="blue">Cached</Tag>}
                      {testResult.llm_model && (
                        <Tag style={{ fontSize: 11 }}>{testResult.llm_model}</Tag>
                      )}
                    </Space>
                  </Space>
                </Card>

                {/* Threats */}
                {testResult.threats.length > 0 && (
                  <Card size="small" title="Detected Threats">
                    {testResult.threats.map((threat, i) => (
                      <div
                        key={i}
                        style={{
                          padding: "8px 0",
                          borderBottom: i < testResult.threats.length - 1 ? "1px solid var(--ant-color-border)" : undefined,
                        }}
                      >
                        <Space>
                          <Tag color={riskColors[threat.severity] ?? "default"}>{threat.threat_type}</Tag>
                          <Tag>{threat.severity}</Tag>
                        </Space>
                        <Paragraph type="secondary" style={{ fontSize: 12, margin: "4px 0 0" }}>
                          {threat.description}
                        </Paragraph>
                      </div>
                    ))}
                  </Card>
                )}

                {/* Recommendations */}
                {testResult.recommendations.length > 0 && (
                  <Card size="small" title="Recommendations">
                    {testResult.recommendations.map((rec, i) => (
                      <Paragraph key={i} type="secondary" style={{ fontSize: 12, margin: 0 }}>
                        {i + 1}. {rec}
                      </Paragraph>
                    ))}
                  </Card>
                )}

                <JsonViewer data={testResult} maxHeight={300} />
              </Space>
            ) : (
              <div
                style={{
                  display: "flex",
                  alignItems: "center",
                  justifyContent: "center",
                  height: "100%",
                  minHeight: 200,
                  borderRadius: 8,
                  border: "1px dashed var(--ant-color-border)",
                  background: "var(--ant-color-bg-layout)",
                }}
              >
                <Text type="secondary">Run a verification to see results</Text>
              </div>
            )}
          </Col>
        </Row>
      </Card>
    </Space>
  );
}
