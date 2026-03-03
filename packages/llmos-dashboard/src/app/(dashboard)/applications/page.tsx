"use client";

import React, { useState, useMemo } from "react";
import {
  Card,
  Row,
  Col,
  Table,
  Space,
  Tag,
  Button,
  Typography,
  Spin,
  Alert,
  Tooltip,
  Switch,
  Form,
  Input,
  InputNumber,
  Popconfirm,
  Modal,
  message,
  Badge,
} from "antd";
import {
  TeamOutlined,
  PlusOutlined,
  ReloadOutlined,
  UserOutlined,
  AppstoreOutlined,
  SafetyOutlined,
  DeleteOutlined,
  FieldTimeOutlined,
} from "@ant-design/icons";
import { useRouter } from "next/navigation";
import { ApiError } from "@/lib/api/client";
import { PageHeader } from "@/components/common/PageHeader";
import { StatCard } from "@/components/common/StatCard";
import { FeatureDisabled } from "@/components/common/FeatureDisabled";
import { useApplications } from "@/hooks/useApplications";
import { timeAgo, truncateId } from "@/lib/utils/formatters";
import type { ApplicationResponse, CreateApplicationRequest } from "@/types/application";
import type { ColumnsType } from "antd/es/table";

const { Text } = Typography;

function isFeatureDisabledError(error: unknown): boolean {
  if (error instanceof ApiError) {
    const msg = (error.detail ?? error.message ?? "").toLowerCase();
    return (
      msg.includes("not enabled") ||
      msg.includes("not available") ||
      msg.includes("identity") ||
      msg.includes("service unavailable")
    );
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

export default function ApplicationsPage() {
  const router = useRouter();
  const [showCreate, setShowCreate] = useState(false);
  const [form] = Form.useForm<CreateApplicationRequest>();

  const { applications, createApp, updateApp, deleteApp } = useApplications();

  const apps = applications.data ?? [];

  const stats = useMemo(() => {
    const total = apps.length;
    const enabled = apps.filter((a) => a.enabled).length;
    const totalAgents = apps.reduce((sum, a) => sum + a.agent_count, 0);
    const totalSessions = apps.reduce((sum, a) => sum + a.session_count, 0);
    return { total, enabled, totalAgents, totalSessions };
  }, [apps]);

  const handleCreate = async (values: CreateApplicationRequest) => {
    try {
      await createApp.mutateAsync(values);
      message.success(`Application '${values.name}' created`);
      form.resetFields();
      setShowCreate(false);
    } catch (err) {
      message.error(getErrorMessage(err));
    }
  };

  const handleToggleEnabled = async (app: ApplicationResponse) => {
    try {
      await updateApp.mutateAsync({
        appId: app.app_id,
        body: { enabled: !app.enabled },
      });
      message.success(`Application '${app.name}' ${app.enabled ? "disabled" : "enabled"}`);
    } catch (err) {
      message.error(getErrorMessage(err));
    }
  };

  const handleDelete = async (app: ApplicationResponse) => {
    try {
      await deleteApp.mutateAsync({ appId: app.app_id });
      message.success(`Application '${app.name}' deleted`);
    } catch (err) {
      message.error(getErrorMessage(err));
    }
  };

  const columns: ColumnsType<ApplicationResponse> = [
    {
      title: "Name",
      dataIndex: "name",
      key: "name",
      render: (name: string, record) => (
        <Space>
          <Button
            type="link"
            style={{ padding: 0, fontWeight: 500 }}
            onClick={() => router.push(`/applications/${record.app_id}`)}
          >
            {name}
          </Button>
          {name === "default" && (
            <Tag color="blue" style={{ borderRadius: 4 }}>
              default
            </Tag>
          )}
        </Space>
      ),
    },
    {
      title: "App ID",
      dataIndex: "app_id",
      key: "app_id",
      render: (id: string) => (
        <Tooltip title={id}>
          <Text type="secondary" style={{ fontSize: 12 }} copyable={{ text: id }}>
            {truncateId(id, 12)}
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
          text={enabled ? "Enabled" : "Disabled"}
        />
      ),
      filters: [
        { text: "Enabled", value: true },
        { text: "Disabled", value: false },
      ],
      onFilter: (value, record) => record.enabled === value,
    },
    {
      title: "Agents",
      dataIndex: "agent_count",
      key: "agents",
      sorter: (a, b) => a.agent_count - b.agent_count,
      render: (count: number) => (
        <Tag color={count > 0 ? "blue" : "default"} style={{ borderRadius: 4 }}>
          {count}
        </Tag>
      ),
    },
    {
      title: "Sessions",
      dataIndex: "session_count",
      key: "sessions",
      sorter: (a, b) => a.session_count - b.session_count,
      render: (count: number) => (
        <Text type={count > 0 ? undefined : "secondary"}>{count}</Text>
      ),
    },
    {
      title: "Limits",
      key: "limits",
      render: (_: unknown, record: ApplicationResponse) => (
        <Text type="secondary" style={{ fontSize: 12 }}>
          {record.max_concurrent_plans} plans / {record.max_actions_per_plan} actions
        </Text>
      ),
    },
    {
      title: "Created",
      dataIndex: "created_at",
      key: "created_at",
      sorter: (a, b) => a.created_at - b.created_at,
      render: (ts: number) => (
        <Tooltip title={new Date(ts * 1000).toLocaleString()}>
          <Text type="secondary" style={{ fontSize: 12 }}>
            {timeAgo(ts)}
          </Text>
        </Tooltip>
      ),
    },
    {
      title: "Enabled",
      key: "toggle",
      render: (_: unknown, record: ApplicationResponse) => (
        <Switch
          checked={record.enabled}
          size="small"
          disabled={record.name === "default"}
          loading={updateApp.isPending}
          onChange={() => handleToggleEnabled(record)}
        />
      ),
    },
    {
      title: "",
      key: "actions",
      width: 60,
      render: (_: unknown, record: ApplicationResponse) =>
        record.name !== "default" ? (
          <Popconfirm
            title={`Delete application '${record.name}'?`}
            description="This will disable the application."
            onConfirm={() => handleDelete(record)}
            okText="Yes"
            cancelText="No"
          >
            <Button
              type="text"
              size="small"
              danger
              icon={<DeleteOutlined />}
              loading={deleteApp.isPending}
            />
          </Popconfirm>
        ) : null,
    },
  ];

  if (applications.isLoading) {
    return (
      <div style={{ textAlign: "center", padding: 80 }}>
        <Spin size="large" />
      </div>
    );
  }

  if (applications.error) {
    if (isFeatureDisabledError(applications.error)) {
      return (
        <>
          <PageHeader
            icon={<TeamOutlined />}
            title="Applications"
            subtitle="Multi-tenant application management"
          />
          <FeatureDisabled
            feature="Identity System"
            description="The multi-tenant identity system is not active. Enable identity in your configuration to manage applications, agents, and API keys."
            configHint="identity.enabled = true"
            icon={<TeamOutlined />}
          />
        </>
      );
    }
    return (
      <Alert
        type="error"
        message="Failed to load applications"
        description={getErrorMessage(applications.error)}
        showIcon
      />
    );
  }

  return (
    <Space direction="vertical" size="large" style={{ width: "100%" }}>
      <PageHeader
        icon={<TeamOutlined />}
        title="Applications"
        subtitle="Multi-tenant application management"
        tags={<Tag color="blue">{apps.length} total</Tag>}
        extra={
          <Space>
            <Button icon={<ReloadOutlined />} onClick={() => applications.refetch()}>
              Refresh
            </Button>
            <Button
              type="primary"
              icon={<PlusOutlined />}
              onClick={() => setShowCreate(true)}
            >
              Create Application
            </Button>
          </Space>
        }
      />

      {/* Stat Cards */}
      <Row gutter={[16, 16]}>
        <Col xs={24} sm={12} lg={6}>
          <StatCard
            title="Total Applications"
            value={stats.total}
            prefix={<AppstoreOutlined />}
            color="#1677ff"
            footer={
              <Text type="secondary" style={{ fontSize: 12 }}>
                Registered applications
              </Text>
            }
          />
        </Col>
        <Col xs={24} sm={12} lg={6}>
          <StatCard
            title="Enabled"
            value={stats.enabled}
            prefix={<SafetyOutlined />}
            color="#52c41a"
            footer={
              <Text type="secondary" style={{ fontSize: 12 }}>
                Active applications
              </Text>
            }
          />
        </Col>
        <Col xs={24} sm={12} lg={6}>
          <StatCard
            title="Total Agents"
            value={stats.totalAgents}
            prefix={<UserOutlined />}
            color="#722ed1"
            footer={
              <Text type="secondary" style={{ fontSize: 12 }}>
                Across all applications
              </Text>
            }
          />
        </Col>
        <Col xs={24} sm={12} lg={6}>
          <StatCard
            title="Active Sessions"
            value={stats.totalSessions}
            prefix={<FieldTimeOutlined />}
            color="#fa8c16"
            footer={
              <Text type="secondary" style={{ fontSize: 12 }}>
                Current sessions
              </Text>
            }
          />
        </Col>
      </Row>

      {/* Application Table */}
      <Card
        title={
          <Space>
            <TeamOutlined />
            <span>Applications</span>
          </Space>
        }
        extra={
          <Text type="secondary" style={{ fontSize: 12 }}>
            Auto-refreshes every 10s
          </Text>
        }
      >
        <Table
          columns={columns}
          dataSource={apps}
          rowKey="app_id"
          loading={applications.isLoading}
          pagination={{
            pageSize: 20,
            showTotal: (total) => `${total} applications`,
            showSizeChanger: true,
          }}
          size="middle"
          onRow={(record) => ({
            style: { cursor: "pointer" },
            onClick: (e) => {
              const target = e.target as HTMLElement;
              if (
                target.closest("button") ||
                target.closest(".ant-switch") ||
                target.closest(".ant-popover") ||
                target.closest(".ant-popconfirm")
              ) {
                return;
              }
              router.push(`/applications/${record.app_id}`);
            },
          })}
        />
      </Card>

      {/* Create Application Modal */}
      <Modal
        title="Create Application"
        open={showCreate}
        onCancel={() => {
          setShowCreate(false);
          form.resetFields();
        }}
        footer={null}
        width={520}
      >
        <Form
          form={form}
          layout="vertical"
          onFinish={handleCreate}
          initialValues={{
            max_concurrent_plans: 10,
            max_actions_per_plan: 50,
          }}
        >
          <Form.Item
            name="name"
            label="Name"
            rules={[{ required: true, message: "Application name is required" }]}
          >
            <Input placeholder="e.g. my-agent-app" />
          </Form.Item>
          <Form.Item name="description" label="Description">
            <Input.TextArea rows={2} placeholder="Optional description" />
          </Form.Item>
          <Row gutter={16}>
            <Col span={12}>
              <Form.Item
                name="max_concurrent_plans"
                label="Max Concurrent Plans"
                rules={[
                  { type: "number", min: 1, max: 100, message: "Must be 1-100" },
                ]}
              >
                <InputNumber style={{ width: "100%" }} min={1} max={100} />
              </Form.Item>
            </Col>
            <Col span={12}>
              <Form.Item
                name="max_actions_per_plan"
                label="Max Actions per Plan"
                rules={[
                  { type: "number", min: 1, max: 500, message: "Must be 1-500" },
                ]}
              >
                <InputNumber style={{ width: "100%" }} min={1} max={500} />
              </Form.Item>
            </Col>
          </Row>
          <Form.Item>
            <Space>
              <Button
                type="primary"
                htmlType="submit"
                icon={<PlusOutlined />}
                loading={createApp.isPending}
              >
                Create
              </Button>
              <Button
                onClick={() => {
                  setShowCreate(false);
                  form.resetFields();
                }}
              >
                Cancel
              </Button>
            </Space>
          </Form.Item>
        </Form>
      </Modal>
    </Space>
  );
}
