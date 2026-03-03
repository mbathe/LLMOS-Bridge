"use client";

import React, { useState } from "react";
import {
  Card,
  Row,
  Col,
  Typography,
  Space,
  Tag,
  Spin,
  Alert,
  Descriptions,
  Table,
  Button,
  Popconfirm,
  Tooltip,
  Badge,
  Form,
  Input,
  Select,
  Modal,
  message,
} from "antd";
import {
  TeamOutlined,
  ArrowLeftOutlined,
  UserOutlined,
  KeyOutlined,
  DeleteOutlined,
  PlusOutlined,
  FieldTimeOutlined,
  AppstoreOutlined,
  WarningOutlined,
} from "@ant-design/icons";
import { useParams, useRouter } from "next/navigation";
import { ApiError } from "@/lib/api/client";
import { PageHeader } from "@/components/common/PageHeader";
import { StatCard } from "@/components/common/StatCard";
import { useApplicationDetail } from "@/hooks/useApplications";
import { AppSecurityTab } from "./_components/AppSecurityTab";
import { timeAgo, formatTimestamp, truncateId } from "@/lib/utils/formatters";
import type { AgentResponse, SessionResponse, CreateAgentRequest, ApiKeyResponse, UpdateApplicationRequest } from "@/types/application";
import type { ColumnsType } from "antd/es/table";

const { Text, Paragraph } = Typography;

function getErrorMessage(error: unknown): string {
  if (error instanceof ApiError) {
    return error.detail ?? error.message ?? "Unknown error";
  }
  if (error instanceof Error) {
    return error.message;
  }
  return "Unknown error";
}

const roleColors: Record<string, string> = {
  admin: "red",
  app_admin: "orange",
  operator: "blue",
  viewer: "green",
  agent: "purple",
};

export default function ApplicationDetailPage() {
  const params = useParams<{ appId: string }>();
  const router = useRouter();
  const appId = params.appId;

  const [showCreateAgent, setShowCreateAgent] = useState(false);
  const [generatedKey, setGeneratedKey] = useState<ApiKeyResponse | null>(null);
  const [agentForm] = Form.useForm<CreateAgentRequest>();

  const {
    app,
    agents,
    sessions,
    updateApp,
    createAgent,
    deleteAgent,
    generateKey,
  } = useApplicationDetail(appId);

  const handleUpdateApp = async (updates: UpdateApplicationRequest) => {
    await updateApp.mutateAsync(updates);
  };

  const handleCreateAgent = async (values: CreateAgentRequest) => {
    try {
      await createAgent.mutateAsync(values);
      message.success(`Agent '${values.name}' created`);
      agentForm.resetFields();
      setShowCreateAgent(false);
    } catch (err) {
      message.error(getErrorMessage(err));
    }
  };

  const handleDeleteAgent = async (agentId: string) => {
    try {
      await deleteAgent.mutateAsync(agentId);
      message.success("Agent deleted");
    } catch (err) {
      message.error(getErrorMessage(err));
    }
  };

  const handleGenerateKey = async (agentId: string) => {
    try {
      const key = await generateKey.mutateAsync(agentId);
      setGeneratedKey(key);
    } catch (err) {
      message.error(getErrorMessage(err));
    }
  };

  const agentColumns: ColumnsType<AgentResponse> = [
    {
      title: "Name",
      dataIndex: "name",
      key: "name",
      render: (name: string) => <Text strong>{name}</Text>,
    },
    {
      title: "Agent ID",
      dataIndex: "agent_id",
      key: "agent_id",
      render: (id: string) => (
        <Tooltip title={id}>
          <Text type="secondary" style={{ fontSize: 12 }} copyable={{ text: id }}>
            {truncateId(id, 12)}
          </Text>
        </Tooltip>
      ),
    },
    {
      title: "Role",
      dataIndex: "role",
      key: "role",
      render: (role: string) => (
        <Tag color={roleColors[role] ?? "default"} style={{ borderRadius: 4 }}>
          {role}
        </Tag>
      ),
    },
    {
      title: "Created",
      dataIndex: "created_at",
      key: "created_at",
      render: (ts: number) => (
        <Tooltip title={formatTimestamp(ts)}>
          <Text type="secondary" style={{ fontSize: 12 }}>
            {timeAgo(ts)}
          </Text>
        </Tooltip>
      ),
    },
    {
      title: "Status",
      dataIndex: "enabled",
      key: "enabled",
      render: (enabled: boolean) => (
        <Badge
          status={enabled ? "success" : "default"}
          text={enabled ? "Active" : "Disabled"}
        />
      ),
    },
    {
      title: "Actions",
      key: "actions",
      render: (_: unknown, record: AgentResponse) => (
        <Space size={4}>
          <Tooltip title="Generate API key">
            <Button
              size="small"
              icon={<KeyOutlined />}
              onClick={() => handleGenerateKey(record.agent_id)}
              loading={generateKey.isPending}
            />
          </Tooltip>
          <Popconfirm
            title={`Delete agent '${record.name}'?`}
            onConfirm={() => handleDeleteAgent(record.agent_id)}
            okText="Yes"
            cancelText="No"
          >
            <Button
              size="small"
              danger
              icon={<DeleteOutlined />}
              loading={deleteAgent.isPending}
            />
          </Popconfirm>
        </Space>
      ),
    },
  ];

  const sessionColumns: ColumnsType<SessionResponse> = [
    {
      title: "Session ID",
      dataIndex: "session_id",
      key: "session_id",
      render: (id: string) => (
        <Tooltip title={id}>
          <Text style={{ fontSize: 12, fontFamily: "monospace" }} copyable={{ text: id }}>
            {truncateId(id, 12)}
          </Text>
        </Tooltip>
      ),
    },
    {
      title: "Agent",
      dataIndex: "agent_id",
      key: "agent_id",
      render: (id: string | null) =>
        id ? (
          <Tooltip title={id}>
            <Text type="secondary" style={{ fontSize: 12 }}>
              {truncateId(id, 12)}
            </Text>
          </Tooltip>
        ) : (
          <Text type="secondary">—</Text>
        ),
    },
    {
      title: "Created",
      dataIndex: "created_at",
      key: "created_at",
      sorter: (a, b) => a.created_at - b.created_at,
      render: (ts: number) => (
        <Tooltip title={formatTimestamp(ts)}>
          <Text type="secondary" style={{ fontSize: 12 }}>
            {timeAgo(ts)}
          </Text>
        </Tooltip>
      ),
    },
    {
      title: "Last Active",
      dataIndex: "last_active",
      key: "last_active",
      defaultSortOrder: "descend" as const,
      sorter: (a, b) => a.last_active - b.last_active,
      render: (ts: number) => (
        <Tooltip title={formatTimestamp(ts)}>
          <Text type="secondary" style={{ fontSize: 12 }}>
            {timeAgo(ts)}
          </Text>
        </Tooltip>
      ),
    },
  ];

  if (app.isLoading) {
    return (
      <div style={{ textAlign: "center", padding: 80 }}>
        <Spin size="large" />
      </div>
    );
  }

  if (app.error) {
    return (
      <Alert
        type="error"
        message={`Failed to load application '${appId}'`}
        description={getErrorMessage(app.error)}
        showIcon
        action={
          <Button onClick={() => router.push("/applications")}>
            Back to Applications
          </Button>
        }
      />
    );
  }

  if (!app.data) {
    return (
      <Alert
        type="warning"
        message="Application not found"
        showIcon
        action={
          <Button onClick={() => router.push("/applications")}>
            Back to Applications
          </Button>
        }
      />
    );
  }

  const appData = app.data;

  return (
    <Space direction="vertical" size="large" style={{ width: "100%" }}>
      <PageHeader
        icon={<TeamOutlined />}
        title={appData.name}
        subtitle={appData.description || `Application ${truncateId(appData.app_id)}`}
        tags={
          <Space size={4}>
            <Badge
              status={appData.enabled ? "success" : "default"}
              text={appData.enabled ? "Enabled" : "Disabled"}
            />
            {appData.name === "default" && (
              <Tag color="blue" style={{ borderRadius: 4 }}>
                default
              </Tag>
            )}
          </Space>
        }
        extra={
          <Button
            icon={<ArrowLeftOutlined />}
            onClick={() => router.push("/applications")}
          >
            Back
          </Button>
        }
      />

      {/* Stat Cards */}
      <Row gutter={[16, 16]}>
        <Col xs={24} sm={12} lg={6}>
          <StatCard
            title="Agents"
            value={appData.agent_count}
            prefix={<UserOutlined />}
            color="#722ed1"
            footer={
              <Text type="secondary" style={{ fontSize: 12 }}>
                Registered agents
              </Text>
            }
          />
        </Col>
        <Col xs={24} sm={12} lg={6}>
          <StatCard
            title="Sessions"
            value={appData.session_count}
            prefix={<FieldTimeOutlined />}
            color="#fa8c16"
            footer={
              <Text type="secondary" style={{ fontSize: 12 }}>
                Active sessions
              </Text>
            }
          />
        </Col>
        <Col xs={24} sm={12} lg={6}>
          <StatCard
            title="Max Plans"
            value={appData.max_concurrent_plans}
            prefix={<AppstoreOutlined />}
            color="#1677ff"
            footer={
              <Text type="secondary" style={{ fontSize: 12 }}>
                Concurrent plan limit
              </Text>
            }
          />
        </Col>
        <Col xs={24} sm={12} lg={6}>
          <StatCard
            title="Max Actions"
            value={appData.max_actions_per_plan}
            prefix={<AppstoreOutlined />}
            color="#13c2c2"
            footer={
              <Text type="secondary" style={{ fontSize: 12 }}>
                Per-plan action limit
              </Text>
            }
          />
        </Col>
      </Row>

      {/* Application Info */}
      <Card
        title={
          <Space>
            <TeamOutlined />
            <span>Application Details</span>
          </Space>
        }
        extra={
          <Tag
            color={appData.enabled ? "green" : "default"}
            style={{ borderRadius: 4 }}
          >
            {appData.enabled ? "Enabled" : "Disabled"}
          </Tag>
        }
      >
        <Descriptions
          column={{ xs: 1, sm: 2, lg: 3 }}
          bordered
          size="small"
        >
          <Descriptions.Item label="App ID">
            <Text copyable style={{ fontSize: 12, fontFamily: "monospace" }}>
              {appData.app_id}
            </Text>
          </Descriptions.Item>
          <Descriptions.Item label="Name">
            <Text strong>{appData.name}</Text>
          </Descriptions.Item>
          <Descriptions.Item label="Description">
            {appData.description || <Text type="secondary">—</Text>}
          </Descriptions.Item>
          <Descriptions.Item label="Created">
            <Tooltip title={formatTimestamp(appData.created_at)}>
              <Text>{timeAgo(appData.created_at)}</Text>
            </Tooltip>
          </Descriptions.Item>
          <Descriptions.Item label="Updated">
            <Tooltip title={formatTimestamp(appData.updated_at)}>
              <Text>{timeAgo(appData.updated_at)}</Text>
            </Tooltip>
          </Descriptions.Item>
          <Descriptions.Item label="Status">
            <Badge
              status={appData.enabled ? "success" : "default"}
              text={appData.enabled ? "Enabled" : "Disabled"}
            />
          </Descriptions.Item>
          {Object.keys(appData.tags).length > 0 && (
            <Descriptions.Item label="Tags" span={3}>
              <Space size={[4, 4]} wrap>
                {Object.entries(appData.tags).map(([k, v]) => (
                  <Tag key={k} style={{ borderRadius: 4 }}>
                    {k}={v}
                  </Tag>
                ))}
              </Space>
            </Descriptions.Item>
          )}
        </Descriptions>
      </Card>

      {/* Security (Module Access, OS Permissions, Quotas) */}
      <AppSecurityTab
        app={appData}
        onUpdateApp={handleUpdateApp}
        savingApp={updateApp.isPending}
      />

      {/* Agents */}
      <Card
        title={
          <Space>
            <UserOutlined />
            <span>Agents</span>
          </Space>
        }
        extra={
          <Space>
            <Text type="secondary" style={{ fontSize: 12 }}>
              {agents.data?.length ?? 0} agents
            </Text>
            <Button
              size="small"
              icon={<PlusOutlined />}
              onClick={() => setShowCreateAgent(true)}
            >
              Create Agent
            </Button>
          </Space>
        }
      >
        <Table
          columns={agentColumns}
          dataSource={agents.data ?? []}
          rowKey="agent_id"
          loading={agents.isLoading}
          pagination={false}
          size="small"
        />
      </Card>

      {/* Sessions */}
      <Card
        title={
          <Space>
            <FieldTimeOutlined />
            <span>Active Sessions</span>
          </Space>
        }
        extra={
          <Text type="secondary" style={{ fontSize: 12 }}>
            {sessions.data?.length ?? 0} sessions
          </Text>
        }
      >
        <Table
          columns={sessionColumns}
          dataSource={sessions.data ?? []}
          rowKey="session_id"
          loading={sessions.isLoading}
          pagination={{
            pageSize: 20,
            showTotal: (total) => `${total} sessions`,
          }}
          size="small"
        />
      </Card>

      {/* Create Agent Modal */}
      <Modal
        title="Create Agent"
        open={showCreateAgent}
        onCancel={() => {
          setShowCreateAgent(false);
          agentForm.resetFields();
        }}
        footer={null}
        width={420}
      >
        <Form
          form={agentForm}
          layout="vertical"
          onFinish={handleCreateAgent}
          initialValues={{ role: "agent" }}
        >
          <Form.Item
            name="name"
            label="Agent Name"
            rules={[{ required: true, message: "Agent name is required" }]}
          >
            <Input placeholder="e.g. my-langchain-agent" />
          </Form.Item>
          <Form.Item name="role" label="Role">
            <Select
              options={[
                { label: "Admin", value: "admin" },
                { label: "App Admin", value: "app_admin" },
                { label: "Operator", value: "operator" },
                { label: "Viewer", value: "viewer" },
                { label: "Agent", value: "agent" },
              ]}
            />
          </Form.Item>
          <Form.Item>
            <Space>
              <Button
                type="primary"
                htmlType="submit"
                icon={<PlusOutlined />}
                loading={createAgent.isPending}
              >
                Create
              </Button>
              <Button
                onClick={() => {
                  setShowCreateAgent(false);
                  agentForm.resetFields();
                }}
              >
                Cancel
              </Button>
            </Space>
          </Form.Item>
        </Form>
      </Modal>

      {/* API Key Generated Modal */}
      <Modal
        title={
          <Space>
            <KeyOutlined />
            <span>API Key Generated</span>
          </Space>
        }
        open={generatedKey !== null}
        onCancel={() => setGeneratedKey(null)}
        footer={
          <Button type="primary" onClick={() => setGeneratedKey(null)}>
            Done
          </Button>
        }
        width={520}
      >
        {generatedKey && (
          <Space direction="vertical" size="middle" style={{ width: "100%" }}>
            <Alert
              type="warning"
              icon={<WarningOutlined />}
              message="Save this API key now"
              description="This key will only be shown once. It cannot be retrieved later."
              showIcon
            />
            <Card
              size="small"
              style={{ background: "var(--ant-color-bg-layout)" }}
            >
              <Paragraph
                copyable={{ text: generatedKey.api_key ?? "" }}
                style={{
                  fontFamily: "monospace",
                  fontSize: 13,
                  marginBottom: 0,
                  wordBreak: "break-all",
                }}
              >
                {generatedKey.api_key}
              </Paragraph>
            </Card>
            <Descriptions size="small" column={1}>
              <Descriptions.Item label="Key ID">
                <Text copyable style={{ fontSize: 12, fontFamily: "monospace" }}>
                  {generatedKey.key_id}
                </Text>
              </Descriptions.Item>
              <Descriptions.Item label="Prefix">
                <Text code>{generatedKey.prefix}</Text>
              </Descriptions.Item>
            </Descriptions>
          </Space>
        )}
      </Modal>
    </Space>
  );
}
