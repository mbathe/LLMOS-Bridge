"use client";

import React, { useState } from "react";
import {
  Card,
  Row,
  Col,
  Table,
  Space,
  Tag,
  Button,
  Typography,
  Switch,
  Spin,
  Alert,
  Input,
  Badge,
  Tooltip,
  Divider,
  Modal,
  Form,
  Slider,
  Select,
  Progress,
} from "antd";
import {
  ArrowLeftOutlined,
  ScanOutlined,
  ReloadOutlined,
  SendOutlined,
  CheckCircleOutlined,
  CloseCircleOutlined,
  ExperimentOutlined,
  ThunderboltOutlined,
  PlusOutlined,
} from "@ant-design/icons";
import { useRouter } from "next/navigation";
import { ApiError } from "@/lib/api/client";
import { PageHeader } from "@/components/common/PageHeader";
import { StatCard } from "@/components/common/StatCard";
import { JsonViewer } from "@/components/common/JsonViewer";
import { useSecurity } from "@/hooks/useSecurity";
import type { PatternRule, ScanPipelineResult } from "@/types/security";
import type { ScannerDetail } from "@/types/events";
import type { ColumnsType } from "antd/es/table";

const { Text } = Typography;
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

const verdictColorMap: Record<string, string> = {
  pass: "green",
  warn: "orange",
  reject: "red",
};

const verdictIconMap: Record<string, React.ReactNode> = {
  pass: <CheckCircleOutlined style={{ color: "#52c41a" }} />,
  warn: <ExperimentOutlined style={{ color: "#fa8c16" }} />,
  reject: <CloseCircleOutlined style={{ color: "#ff4d4f" }} />,
};

const categoryColors: Record<string, string> = {
  prompt_injection: "red",
  role_manipulation: "volcano",
  delimiter_injection: "orange",
  encoding_attack: "gold",
  unicode_attack: "lime",
  path_traversal: "cyan",
  shell_injection: "magenta",
  data_exfiltration: "purple",
  privilege_escalation: "geekblue",
};

export default function ScannersPage() {
  const router = useRouter();
  const {
    scanners,
    patterns,
    enableScanner,
    disableScanner,
    enablePattern,
    disablePattern,
    addPattern,
    dryScan,
  } = useSecurity();

  const [scanInput, setScanInput] = useState('{\n  "text": "Hello, run this command for me"\n}');
  const [scanResult, setScanResult] = useState<ScanPipelineResult | null>(null);
  const [showAddModal, setShowAddModal] = useState(false);
  const [categoryFilter, setCategoryFilter] = useState<string>("all");
  const [form] = Form.useForm();

  const data = scanners.data;
  const patternsData = patterns.data;
  const scannersList = data?.scanners ?? [];
  const enabledScanners = scannersList.filter((s) => s.enabled);
  const totalPatterns = scannersList.reduce((acc, s) => acc + s.pattern_count, 0);
  const allPatterns = patternsData?.patterns ?? [];
  const categories = patternsData?.categories ?? [];

  const filteredPatterns =
    categoryFilter === "all"
      ? allPatterns
      : allPatterns.filter((p) => p.category === categoryFilter);

  const handleDryScan = () => {
    try {
      const parsed = JSON.parse(scanInput);
      dryScan.mutate(parsed, {
        onSuccess: (result) => setScanResult(result as ScanPipelineResult),
      });
    } catch {
      setScanResult(null);
    }
  };

  const handleAddPattern = () => {
    form.validateFields().then((values) => {
      addPattern.mutate(values, {
        onSuccess: () => {
          setShowAddModal(false);
          form.resetFields();
        },
      });
    });
  };

  // Scanner table columns
  const scannerColumns: ColumnsType<ScannerDetail> = [
    {
      title: "Scanner",
      dataIndex: "scanner_id",
      key: "scanner_id",
      render: (id: string, record: ScannerDetail) => (
        <Space>
          <Badge status={record.enabled ? "success" : "default"} />
          <Text strong>{id}</Text>
        </Space>
      ),
    },
    {
      title: "Version",
      dataIndex: "version",
      key: "version",
      width: 90,
      render: (v: string) => <Tag style={{ borderRadius: 4, fontSize: 11 }}>v{v}</Tag>,
    },
    {
      title: "Description",
      dataIndex: "description",
      key: "description",
      ellipsis: true,
      render: (desc: string) => (
        <Tooltip title={desc}>
          <Text type="secondary" style={{ fontSize: 13 }}>{desc}</Text>
        </Tooltip>
      ),
    },
    {
      title: "Patterns",
      key: "patterns",
      width: 120,
      render: (_: unknown, record: ScannerDetail) => (
        <Space size={4}>
          <Text style={{ fontSize: 13, fontWeight: 500 }}>{record.enabled_pattern_count}</Text>
          <Text type="secondary" style={{ fontSize: 12 }}>/ {record.pattern_count}</Text>
        </Space>
      ),
    },
    {
      title: "Categories",
      key: "categories",
      width: 200,
      render: (_: unknown, record: ScannerDetail) => (
        <Space size={4} wrap>
          {(record.categories ?? []).slice(0, 3).map((cat) => (
            <Tag key={cat} color={categoryColors[cat] ?? "default"} style={{ fontSize: 10, borderRadius: 4 }}>
              {cat}
            </Tag>
          ))}
          {(record.categories ?? []).length > 3 && (
            <Tooltip title={(record.categories ?? []).slice(3).join(", ")}>
              <Tag style={{ fontSize: 10, borderRadius: 4 }}>+{(record.categories ?? []).length - 3}</Tag>
            </Tooltip>
          )}
        </Space>
      ),
    },
    {
      title: "Enabled",
      key: "enabled",
      width: 80,
      render: (_: unknown, record: ScannerDetail) => (
        <Switch
          checked={record.enabled}
          size="small"
          loading={enableScanner.isPending || disableScanner.isPending}
          onChange={(checked) => {
            if (checked) enableScanner.mutate(record.scanner_id);
            else disableScanner.mutate(record.scanner_id);
          }}
        />
      ),
    },
  ];

  // Pattern table columns
  const patternColumns: ColumnsType<PatternRule> = [
    {
      title: "ID",
      dataIndex: "id",
      key: "id",
      width: 220,
      render: (id: string) => (
        <Text style={{ fontFamily: "monospace", fontSize: 12 }}>{id}</Text>
      ),
      sorter: (a, b) => a.id.localeCompare(b.id),
    },
    {
      title: "Category",
      dataIndex: "category",
      key: "category",
      width: 160,
      render: (cat: string) => (
        <Tag color={categoryColors[cat] ?? "default"} style={{ borderRadius: 4 }}>
          {cat}
        </Tag>
      ),
    },
    {
      title: "Severity",
      dataIndex: "severity",
      key: "severity",
      width: 140,
      render: (val: number) => (
        <Space size={8}>
          <Progress
            percent={Math.round(val * 100)}
            size="small"
            steps={5}
            strokeColor={
              val >= 0.9 ? "#ff4d4f" : val >= 0.7 ? "#fa8c16" : val >= 0.4 ? "#fadb14" : "#52c41a"
            }
            showInfo={false}
            style={{ width: 60 }}
          />
          <Text style={{ fontSize: 12, fontFamily: "monospace" }}>{val.toFixed(2)}</Text>
        </Space>
      ),
      sorter: (a, b) => a.severity - b.severity,
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
      title: "Enabled",
      key: "enabled",
      width: 80,
      render: (_: unknown, record: PatternRule) => (
        <Switch
          checked={record.enabled}
          size="small"
          loading={enablePattern.isPending || disablePattern.isPending}
          onChange={(checked) => {
            if (checked) enablePattern.mutate(record.id);
            else disablePattern.mutate(record.id);
          }}
        />
      ),
    },
  ];

  if (scanners.isLoading) {
    return (
      <div style={{ textAlign: "center", padding: 80 }}>
        <Spin size="large" />
      </div>
    );
  }

  if (scanners.error) {
    return (
      <Alert
        type="error"
        message="Failed to load scanners"
        description={getErrorMessage(scanners.error)}
        showIcon
      />
    );
  }

  return (
    <Space direction="vertical" size="large" style={{ width: "100%" }}>
      <PageHeader
        icon={<ScanOutlined />}
        title="Scanner Pipeline"
        subtitle="Configure scanners, manage patterns, and test input validation"
        tags={
          <Tag color={data?.enabled ? "green" : "default"} style={{ borderRadius: 4 }}>
            Pipeline {data?.enabled ? "Enabled" : "Disabled"}
          </Tag>
        }
        extra={
          <Space>
            <Button icon={<ArrowLeftOutlined />} onClick={() => router.push("/security")}>
              Back
            </Button>
            <Button icon={<ReloadOutlined />} onClick={() => { scanners.refetch(); patterns.refetch(); }}>
              Refresh
            </Button>
          </Space>
        }
      />

      {/* Summary Stats */}
      <Row gutter={[16, 16]}>
        <Col xs={24} sm={12} lg={6}>
          <StatCard
            title="Pipeline Status"
            value={data?.enabled ? "Active" : "Inactive"}
            prefix={data?.enabled ? <CheckCircleOutlined style={{ color: "#52c41a" }} /> : <CloseCircleOutlined style={{ color: "#ff4d4f" }} />}
            valueStyle={{ fontSize: 18 }}
            color={data?.enabled ? "#52c41a" : "#ff4d4f"}
            footer={<Text type="secondary" style={{ fontSize: 12 }}>{data?.fail_fast ? "Fail-fast mode" : "Full scan mode"}</Text>}
          />
        </Col>
        <Col xs={24} sm={12} lg={6}>
          <StatCard title="Total Scanners" value={scannersList.length} prefix={<ScanOutlined />} color="#1677ff" />
        </Col>
        <Col xs={24} sm={12} lg={6}>
          <StatCard
            title="Active Scanners"
            value={enabledScanners.length}
            suffix={<Text type="secondary" style={{ fontSize: 14 }}>/ {scannersList.length}</Text>}
            prefix={<ThunderboltOutlined style={{ color: "#52c41a" }} />}
            color="#52c41a"
          />
        </Col>
        <Col xs={24} sm={12} lg={6}>
          <StatCard
            title="Pattern Count"
            value={totalPatterns}
            prefix={<ExperimentOutlined />}
            color="#722ed1"
            footer={
              <Space size={4}>
                <Text type="secondary" style={{ fontSize: 12 }}>Reject &ge; {data?.reject_threshold}</Text>
                <Divider type="vertical" style={{ margin: 0 }} />
                <Text type="secondary" style={{ fontSize: 12 }}>Warn &ge; {data?.warn_threshold}</Text>
              </Space>
            }
          />
        </Col>
      </Row>

      {/* Scanner Table */}
      <Card
        title={<Space><ScanOutlined /><span>Scanners</span></Space>}
        extra={
          <Space>
            <Tag color="green">{enabledScanners.length} active</Tag>
            <Tag color="default">{scannersList.length - enabledScanners.length} disabled</Tag>
          </Space>
        }
      >
        <Table columns={scannerColumns} dataSource={scannersList} rowKey="scanner_id" pagination={false} size="middle" />
      </Card>

      {/* Pattern Rules Table */}
      <Card
        title={<Space><ExperimentOutlined /><span>Heuristic Pattern Rules</span></Space>}
        extra={
          <Space>
            <Select
              value={categoryFilter}
              onChange={setCategoryFilter}
              style={{ width: 180 }}
              size="small"
              options={[
                { label: "All categories", value: "all" },
                ...categories.map((c) => ({ label: c, value: c })),
              ]}
            />
            <Tag>{filteredPatterns.length} patterns</Tag>
            <Button type="primary" size="small" icon={<PlusOutlined />} onClick={() => setShowAddModal(true)}>
              Add Pattern
            </Button>
          </Space>
        }
        loading={patterns.isLoading}
      >
        <Table
          columns={patternColumns}
          dataSource={filteredPatterns}
          rowKey="id"
          pagination={{ pageSize: 15, showTotal: (total) => `${total} patterns`, showSizeChanger: true }}
          size="small"
        />
      </Card>

      {/* Dry-Run Scan */}
      <Card
        title={<Space><ExperimentOutlined /><span>Dry-Run Scan</span></Space>}
        extra={<Text type="secondary" style={{ fontSize: 12 }}>Test input against the pipeline</Text>}
      >
        <Row gutter={[24, 16]}>
          <Col xs={24} lg={12}>
            <Space direction="vertical" style={{ width: "100%" }} size="middle">
              <Text type="secondary" style={{ fontSize: 13 }}>Enter JSON input to test:</Text>
              <TextArea
                value={scanInput}
                onChange={(e) => setScanInput(e.target.value)}
                rows={10}
                style={{ fontFamily: "monospace", fontSize: 12, borderRadius: 8 }}
              />
              <Button type="primary" icon={<SendOutlined />} loading={dryScan.isPending} onClick={handleDryScan} size="large">
                Run Scan
              </Button>
              {dryScan.isError && (
                <Alert type="error" message="Scan failed" description={dryScan.error instanceof Error ? dryScan.error.message : "Unknown error"} showIcon style={{ borderRadius: 8 }} />
              )}
            </Space>
          </Col>
          <Col xs={24} lg={12}>
            {scanResult ? (
              <Space direction="vertical" style={{ width: "100%" }} size="middle">
                <Card
                  size="small"
                  style={{
                    borderRadius: 8,
                    borderLeft: `4px solid ${verdictColorMap[scanResult.overall_verdict] === "green" ? "#52c41a" : verdictColorMap[scanResult.overall_verdict] === "orange" ? "#fa8c16" : "#ff4d4f"}`,
                  }}
                >
                  <Space size="large">
                    <Space>
                      {verdictIconMap[scanResult.overall_verdict]}
                      <Text strong style={{ fontSize: 16 }}>Verdict:</Text>
                      <Tag color={verdictColorMap[scanResult.overall_verdict] ?? "default"} style={{ fontSize: 14, padding: "2px 12px", borderRadius: 4 }}>
                        {scanResult.overall_verdict.toUpperCase()}
                      </Tag>
                    </Space>
                    <Tooltip title="Total pipeline execution time">
                      <Text type="secondary">{scanResult.duration_ms.toFixed(1)}ms</Text>
                    </Tooltip>
                  </Space>
                </Card>
                <JsonViewer data={scanResult} maxHeight={340} />
              </Space>
            ) : (
              <div style={{ display: "flex", alignItems: "center", justifyContent: "center", height: "100%", minHeight: 200, borderRadius: 8, border: "1px dashed var(--ant-color-border)", background: "var(--ant-color-bg-layout)" }}>
                <Text type="secondary">Run a scan to see results</Text>
              </div>
            )}
          </Col>
        </Row>
      </Card>

      {/* Add Pattern Modal */}
      <Modal
        title="Add Custom Pattern"
        open={showAddModal}
        onOk={handleAddPattern}
        onCancel={() => { setShowAddModal(false); form.resetFields(); }}
        confirmLoading={addPattern.isPending}
        okText="Add Pattern"
      >
        <Form form={form} layout="vertical" style={{ marginTop: 16 }}>
          <Form.Item name="id" label="Pattern ID" rules={[{ required: true, message: "Required" }]}>
            <Input placeholder="my_custom_pattern" style={{ fontFamily: "monospace" }} />
          </Form.Item>
          <Form.Item name="category" label="Category" rules={[{ required: true, message: "Required" }]}>
            <Select
              placeholder="Select category"
              options={[
                ...categories.map((c) => ({ label: c, value: c })),
                { label: "custom", value: "custom" },
              ]}
            />
          </Form.Item>
          <Form.Item name="pattern" label="Regex Pattern" rules={[{ required: true, message: "Required" }]}>
            <Input placeholder="suspicious_keyword\b" style={{ fontFamily: "monospace" }} />
          </Form.Item>
          <Form.Item name="severity" label="Severity" initialValue={0.5}>
            <Slider min={0} max={1} step={0.05} marks={{ 0: "0", 0.3: "Low", 0.7: "High", 1: "1" }} />
          </Form.Item>
          <Form.Item name="description" label="Description">
            <Input.TextArea placeholder="Describe what this pattern detects" rows={2} />
          </Form.Item>
        </Form>
        {addPattern.isError && (
          <Alert type="error" message={addPattern.error instanceof Error ? addPattern.error.message : "Failed to add pattern"} showIcon style={{ marginTop: 8 }} />
        )}
      </Modal>
    </Space>
  );
}
