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
  Tabs,
  Button,
  Popconfirm,
  Tooltip,
  Badge,
  Form,
  Input,
  Select,
  Modal,
  Steps,
  Upload,
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
  CodeOutlined,
  PlayCircleOutlined,
  CheckCircleOutlined,
  ThunderboltOutlined,
  UploadOutlined,
  FileTextOutlined,
  CloseCircleOutlined,
  LockOutlined,
  RobotOutlined,
  SafetyCertificateOutlined,
  StopOutlined,
  ApiOutlined,
  SyncOutlined,
  DownloadOutlined,
} from "@ant-design/icons";
import { useParams, useRouter } from "next/navigation";
import { ApiError, api } from "@/lib/api/client";
import { PageHeader } from "@/components/common/PageHeader";
import { StatCard } from "@/components/common/StatCard";
import { useApplicationDetail } from "@/hooks/useApplications";
import { useApps, useYamlParsed } from "@/hooks/useApps";
import { AppSecurityTab } from "./_components/AppSecurityTab";
import { timeAgo, formatTimestamp, truncateId } from "@/lib/utils/formatters";
import type { AgentResponse, SessionResponse, CreateAgentRequest, ApiKeyResponse, UpdateApplicationRequest } from "@/types/application";
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

const roleColors: Record<string, string> = {
  admin: "red",
  app_admin: "orange",
  operator: "blue",
  viewer: "green",
  agent: "purple",
};

const yamlStatusColors: Record<string, string> = {
  registered: "blue",
  prepared: "cyan",
  running: "green",
  stopped: "default",
  error: "red",
};

export default function ApplicationDetailPage() {
  const params = useParams<{ appId: string }>();
  const router = useRouter();
  const appId = params.appId;
  const [messageApi, contextHolder] = message.useMessage();

  // ── Identity hooks ──
  const [showCreateAgent, setShowCreateAgent] = useState(false);
  const [generatedKey, setGeneratedKey] = useState<ApiKeyResponse | null>(null);
  const [agentForm] = Form.useForm<CreateAgentRequest>();

  const {
    app,
    agents,
    sessions,
    secrets,
    updateApp,
    createAgent,
    deleteAgent,
    generateKey,
    setSecret,
    deleteSecret,
  } = useApplicationDetail(appId);

  // ── YAML App hooks ──
  const [registerModalOpen, setRegisterModalOpen] = useState(false);
  const [yamlText, setYamlText] = useState("");
  const [runModalOpen, setRunModalOpen] = useState(false);
  const [runInput, setRunInput] = useState("");
  const [runResult, setRunResult] = useState<string | null>(null);
  const [isStreaming, setIsStreaming] = useState(false);
  const abortRef = React.useRef<AbortController | null>(null);
  const [newSecretKey, setNewSecretKey] = useState("");
  const [newSecretValue, setNewSecretValue] = useState("");

  const {
    apps: yamlAppsQuery,
    registerApp,
    deleteApp: deleteYamlApp,
    prepareApp,
    runApp,
  } = useApps();

  // Find the linked YAML app for this application
  const linkedYamlApp = (yamlAppsQuery.data ?? []).find(
    (a) => a.application_id === appId
  );

  // YAML parsed config + sync status
  const { parsed: yamlParsedQuery, syncFromYaml, syncToYaml } = useYamlParsed(linkedYamlApp?.id);
  const yamlParsed = yamlParsedQuery.data ?? null;

  const handleSyncFromYaml = async () => {
    if (!linkedYamlApp) return;
    try {
      await syncFromYaml.mutateAsync(linkedYamlApp.id);
      messageApi.success("Synced from YAML — identity updated");
    } catch (err: unknown) {
      const msg = err instanceof ApiError
        ? (err.detail ?? err.message ?? "Sync failed")
        : err instanceof Error ? err.message : "Sync failed";
      messageApi.error(`Sync failed: ${msg}`);
    }
  };

  const handleSyncToYaml = async () => {
    if (!linkedYamlApp) return;
    await syncToYaml.mutateAsync(linkedYamlApp.id);
  };

  // ── Identity handlers ──
  const handleUpdateApp = async (updates: UpdateApplicationRequest) => {
    await updateApp.mutateAsync(updates);
  };

  const handleCreateAgent = async (values: CreateAgentRequest) => {
    try {
      await createAgent.mutateAsync(values);
      messageApi.success(`Agent '${values.name}' created`);
      agentForm.resetFields();
      setShowCreateAgent(false);
    } catch (err) {
      messageApi.error(getErrorMessage(err));
    }
  };

  const handleDeleteAgent = async (agentId: string) => {
    try {
      await deleteAgent.mutateAsync(agentId);
      messageApi.success("Agent deleted");
    } catch (err) {
      messageApi.error(getErrorMessage(err));
    }
  };

  const handleGenerateKey = async (agentId: string) => {
    try {
      const key = await generateKey.mutateAsync(agentId);
      setGeneratedKey(key);
    } catch (err) {
      messageApi.error(getErrorMessage(err));
    }
  };

  // ── Secrets handlers ──
  const handleAddSecret = async () => {
    const k = newSecretKey.trim();
    const v = newSecretValue;
    if (!k || !v) {
      messageApi.error("Both key and value are required.");
      return;
    }
    try {
      await setSecret.mutateAsync({ key: k, value: v });
      messageApi.success(`Secret '${k}' saved`);
      setNewSecretKey("");
      setNewSecretValue("");
    } catch (err) {
      messageApi.error(getErrorMessage(err));
    }
  };

  const handleDeleteSecret = async (key: string) => {
    try {
      await deleteSecret.mutateAsync(key);
      messageApi.success(`Secret '${key}' deleted`);
    } catch (err) {
      messageApi.error(getErrorMessage(err));
    }
  };

  // ── YAML App handlers ──
  const handleRegister = async () => {
    if (!yamlText) {
      messageApi.error("Upload a file or paste YAML content.");
      return;
    }
    try {
      await registerApp.mutateAsync({ yaml_text: yamlText, application_id: appId });
      messageApi.success("YAML App registered and linked");
      setRegisterModalOpen(false);
      setYamlText("");
    } catch (err: unknown) {
      messageApi.error(err instanceof Error ? err.message : "Registration failed");
    }
  };

  const handlePrepare = async () => {
    if (!linkedYamlApp) return;
    try {
      const result = await prepareApp.mutateAsync(linkedYamlApp.id);
      if (result.ready) {
        messageApi.success(
          `Prepared: ${result.tools_resolved} tools, LLM ${result.llm_warmed ? "warmed" : "cold"} (${Math.round(result.duration_ms)}ms)`
        );
      } else {
        messageApi.warning(`Incomplete: missing modules: ${result.modules_missing.join(", ")}`);
      }
    } catch (err: unknown) {
      messageApi.error(err instanceof Error ? err.message : "Prepare failed");
    }
  };

  const handleRun = async () => {
    if (!linkedYamlApp || !runInput.trim()) {
      messageApi.error("Provide input text.");
      return;
    }

    setRunResult("");
    setIsStreaming(true);

    try {
      const result = await runApp.mutateAsync({
        appId: linkedYamlApp.id,
        body: { input: runInput },
      });
      setRunResult(result.output || result.error || "No output");
      if (result.success) {
        messageApi.success(`Completed in ${Math.round(result.duration_ms)}ms`);
      } else {
        messageApi.error(result.error || "App run failed");
      }
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : "Run failed";
      messageApi.error(msg);
      setRunResult(`[ERROR] ${msg}`);
    } finally {
      setIsStreaming(false);
    }
  };

  // ── Table columns ──
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
          <Text type="secondary" style={{ fontSize: 12 }}>{timeAgo(ts)}</Text>
        </Tooltip>
      ),
    },
    {
      title: "Status",
      dataIndex: "enabled",
      key: "enabled",
      render: (enabled: boolean) => (
        <Badge status={enabled ? "success" : "default"} text={enabled ? "Active" : "Disabled"} />
      ),
    },
    {
      title: "Actions",
      key: "actions",
      render: (_: unknown, record: AgentResponse) => (
        <Space size={4}>
          <Tooltip title="Generate API key">
            <Button size="small" icon={<KeyOutlined />} onClick={() => handleGenerateKey(record.agent_id)} loading={generateKey.isPending} />
          </Tooltip>
          <Popconfirm title={`Delete agent '${record.name}'?`} onConfirm={() => handleDeleteAgent(record.agent_id)} okText="Yes" cancelText="No">
            <Button size="small" danger icon={<DeleteOutlined />} loading={deleteAgent.isPending} />
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
          <Text style={{ fontSize: 12, fontFamily: "monospace" }} copyable={{ text: id }}>{truncateId(id, 12)}</Text>
        </Tooltip>
      ),
    },
    {
      title: "Agent",
      dataIndex: "agent_id",
      key: "agent_id",
      render: (id: string | null) =>
        id ? (
          <Tooltip title={id}><Text type="secondary" style={{ fontSize: 12 }}>{truncateId(id, 12)}</Text></Tooltip>
        ) : <Text type="secondary">--</Text>,
    },
    {
      title: "Created",
      dataIndex: "created_at",
      key: "created_at",
      sorter: (a, b) => a.created_at - b.created_at,
      render: (ts: number) => (
        <Tooltip title={formatTimestamp(ts)}><Text type="secondary" style={{ fontSize: 12 }}>{timeAgo(ts)}</Text></Tooltip>
      ),
    },
    {
      title: "Last Active",
      dataIndex: "last_active",
      key: "last_active",
      defaultSortOrder: "descend" as const,
      sorter: (a, b) => a.last_active - b.last_active,
      render: (ts: number) => (
        <Tooltip title={formatTimestamp(ts)}><Text type="secondary" style={{ fontSize: 12 }}>{timeAgo(ts)}</Text></Tooltip>
      ),
    },
  ];

  // ── Loading / Error ──
  if (app.isLoading) {
    return <div style={{ textAlign: "center", padding: 80 }}><Spin size="large" /></div>;
  }

  if (app.error) {
    return (
      <Alert
        type="error"
        message={`Failed to load application '${appId}'`}
        description={getErrorMessage(app.error)}
        showIcon
        action={<Button onClick={() => router.push("/applications")}>Back to Applications</Button>}
      />
    );
  }

  if (!app.data) {
    return (
      <Alert
        type="warning"
        message="Application not found"
        showIcon
        action={<Button onClick={() => router.push("/applications")}>Back to Applications</Button>}
      />
    );
  }

  const appData = app.data;
  const isYamlApp = appData.tags?.yaml_app === "true";

  // Determine lifecycle step for the linked YAML app
  const getLifecycleStep = () => {
    if (!linkedYamlApp) return 0;
    if (linkedYamlApp.status === "running") return 3;
    if (linkedYamlApp.prepared) return 2;
    return 1;
  };

  return (
    <Space direction="vertical" size="large" style={{ width: "100%" }}>
      {contextHolder}

      <PageHeader
        icon={<TeamOutlined />}
        title={appData.name}
        subtitle={appData.description || `Application ${truncateId(appData.app_id)}`}
        tags={
          <Space size={4}>
            <Badge status={appData.enabled ? "success" : "default"} text={appData.enabled ? "Enabled" : "Disabled"} />
            {appData.name === "default" && <Tag color="blue" style={{ borderRadius: 4 }}>default</Tag>}
            {isYamlApp && <Tag color="purple" style={{ borderRadius: 4 }}>YAML App</Tag>}
          </Space>
        }
        extra={
          <Button icon={<ArrowLeftOutlined />} onClick={() => router.push("/applications")}>Back</Button>
        }
      />

      {/* Stat Cards */}
      <Row gutter={[16, 16]}>
        <Col xs={24} sm={12} lg={6}>
          <StatCard title="Agents" value={appData.agent_count} prefix={<UserOutlined />} color="#722ed1" />
        </Col>
        <Col xs={24} sm={12} lg={6}>
          <StatCard title="Sessions" value={appData.session_count} prefix={<FieldTimeOutlined />} color="#fa8c16" />
        </Col>
        <Col xs={24} sm={12} lg={6}>
          <StatCard title="Max Plans" value={appData.max_concurrent_plans} prefix={<AppstoreOutlined />} color="#1677ff" />
        </Col>
        <Col xs={24} sm={12} lg={6}>
          <StatCard title="Max Actions" value={appData.max_actions_per_plan} prefix={<AppstoreOutlined />} color="#13c2c2" />
        </Col>
      </Row>

      {/* Application Info */}
      <Card
        title={<Space><TeamOutlined /><span>Application Details</span></Space>}
        extra={<Tag color={appData.enabled ? "green" : "default"} style={{ borderRadius: 4 }}>{appData.enabled ? "Enabled" : "Disabled"}</Tag>}
      >
        <Descriptions column={{ xs: 1, sm: 2, lg: 3 }} bordered size="small">
          <Descriptions.Item label="App ID">
            <Text copyable style={{ fontSize: 12, fontFamily: "monospace" }}>{appData.app_id}</Text>
          </Descriptions.Item>
          <Descriptions.Item label="Name"><Text strong>{appData.name}</Text></Descriptions.Item>
          <Descriptions.Item label="Description">{appData.description || <Text type="secondary">--</Text>}</Descriptions.Item>
          <Descriptions.Item label="Created">
            <Tooltip title={formatTimestamp(appData.created_at)}><Text>{timeAgo(appData.created_at)}</Text></Tooltip>
          </Descriptions.Item>
          <Descriptions.Item label="Updated">
            <Tooltip title={formatTimestamp(appData.updated_at)}><Text>{timeAgo(appData.updated_at)}</Text></Tooltip>
          </Descriptions.Item>
          <Descriptions.Item label="Status">
            <Badge status={appData.enabled ? "success" : "default"} text={appData.enabled ? "Enabled" : "Disabled"} />
          </Descriptions.Item>
          {Object.keys(appData.tags).length > 0 && (
            <Descriptions.Item label="Tags" span={3}>
              <Space size={[4, 4]} wrap>
                {Object.entries(appData.tags).map(([k, v]) => (
                  <Tag key={k} style={{ borderRadius: 4 }}>{k}={v}</Tag>
                ))}
              </Space>
            </Descriptions.Item>
          )}
        </Descriptions>
      </Card>

      {/* ── YAML App Section ── */}
      <Card
        title={
          <Space>
            <CodeOutlined />
            <span>YAML App</span>
            {linkedYamlApp && (
              <Tag color={yamlStatusColors[linkedYamlApp.status] ?? "default"}>{linkedYamlApp.status}</Tag>
            )}
          </Space>
        }
        extra={
          linkedYamlApp ? (
            <Space>
              <Button
                size="small"
                icon={<SyncOutlined />}
                loading={syncFromYaml.isPending}
                onClick={handleSyncFromYaml}
                type={yamlParsed && !yamlParsed.in_sync ? "primary" : "default"}
              >
                Sync from YAML
              </Button>
              <Tooltip title="Download the registered YAML file">
                <Button
                  size="small"
                  icon={<DownloadOutlined />}
                  href={`${api.daemonUrl}/apps/${linkedYamlApp.id}/yaml`}
                  target="_blank"
                  rel="noopener noreferrer"
                />
              </Tooltip>
              {!linkedYamlApp.prepared && (
                <Button size="small" icon={<ThunderboltOutlined />} onClick={handlePrepare} loading={prepareApp.isPending}>
                  Prepare
                </Button>
              )}
              <Tooltip title={!linkedYamlApp.prepared ? "Prepare the app first before running" : undefined}>
                <Button
                  size="small"
                  type="primary"
                  icon={<PlayCircleOutlined />}
                  disabled={!linkedYamlApp.prepared}
                  onClick={() => { setRunInput(""); setRunResult(null); setRunModalOpen(true); }}
                >
                  Run
                </Button>
              </Tooltip>
              <Popconfirm title="Unlink and delete this YAML app?" onConfirm={() => deleteYamlApp.mutate(linkedYamlApp.id)}>
                <Button size="small" danger icon={<DeleteOutlined />} />
              </Popconfirm>
            </Space>
          ) : (
            <Button size="small" type="primary" icon={<PlusOutlined />} onClick={() => setRegisterModalOpen(true)}>
              Register YAML App
            </Button>
          )
        }
      >
        {linkedYamlApp ? (
          <Space direction="vertical" size="middle" style={{ width: "100%" }}>
            {/* Lifecycle Steps */}
            <Steps
              size="small"
              current={getLifecycleStep()}
              items={[
                { title: "Upload YAML", description: "Compile & validate" },
                { title: "Registered", description: linkedYamlApp ? `v${linkedYamlApp.version}` : "" },
                { title: "Prepared", description: linkedYamlApp.prepared ? "Resources loaded" : "Not yet" },
                { title: "Running", description: linkedYamlApp.status === "running" ? "Active" : "Idle" },
              ]}
            />

            {/* App Details */}
            <Descriptions size="small" column={2} bordered>
              <Descriptions.Item label="Name"><Text strong>{linkedYamlApp.name}</Text></Descriptions.Item>
              <Descriptions.Item label="Version">{linkedYamlApp.version}</Descriptions.Item>
              <Descriptions.Item label="Author">{linkedYamlApp.author || "--"}</Descriptions.Item>
              <Descriptions.Item label="Status">
                <Space>
                  <Tag color={yamlStatusColors[linkedYamlApp.status] ?? "default"}>{linkedYamlApp.status}</Tag>
                  {linkedYamlApp.prepared && <CheckCircleOutlined style={{ color: "#52c41a" }} />}
                </Space>
              </Descriptions.Item>
              <Descriptions.Item label="Run Count">{linkedYamlApp.run_count}</Descriptions.Item>
              <Descriptions.Item label="Last Run">
                {linkedYamlApp.last_run_at > 0 ? timeAgo(linkedYamlApp.last_run_at) : "--"}
              </Descriptions.Item>
              <Descriptions.Item label="Description" span={2}>{linkedYamlApp.description || "--"}</Descriptions.Item>
              {linkedYamlApp.error_message && (
                <Descriptions.Item label="Error" span={2}>
                  <Text type="danger">{linkedYamlApp.error_message}</Text>
                </Descriptions.Item>
              )}
            </Descriptions>
          </Space>
        ) : (
          <Alert
            type="info"
            message="No YAML App linked"
            description="Register a .app.yaml file to link it to this application. The YAML app will inherit this application's security settings (allowed modules, sessions, RBAC)."
            showIcon
            icon={<FileTextOutlined />}
          />
        )}
      </Card>

      {/* ── YAML Configuration Details ── */}
      {linkedYamlApp && yamlParsed && (
        <Card
          title={
            <Space>
              <CodeOutlined />
              <span>YAML Configuration</span>
              {yamlParsed.in_sync ? (
                <Tag color="success" icon={<CheckCircleOutlined />} style={{ borderRadius: 4 }}>In sync</Tag>
              ) : (
                <Tag color="warning" icon={<SyncOutlined spin />} style={{ borderRadius: 4 }}>Out of sync</Tag>
              )}
            </Space>
          }
          extra={
            <Button
              size="small"
              icon={<SyncOutlined />}
              loading={syncFromYaml.isPending}
              onClick={handleSyncFromYaml}
              type={yamlParsed.in_sync ? "default" : "primary"}
            >
              Sync from YAML
            </Button>
          }
        >
          <Tabs
            size="small"
            items={[
              // ── Agent ──
              {
                key: "agent",
                label: <Space size={4}><RobotOutlined /><span>Agent</span></Space>,
                children: yamlParsed.yaml_agent ? (
                  <Descriptions size="small" bordered column={2}>
                    <Descriptions.Item label="Provider">
                      <Tag color="blue" style={{ borderRadius: 4 }}>{yamlParsed.yaml_agent.provider}</Tag>
                    </Descriptions.Item>
                    <Descriptions.Item label="Model">
                      <Text code>{yamlParsed.yaml_agent.model}</Text>
                    </Descriptions.Item>
                    {yamlParsed.yaml_agent.temperature != null && (
                      <Descriptions.Item label="Temperature">
                        {yamlParsed.yaml_agent.temperature}
                      </Descriptions.Item>
                    )}
                    {yamlParsed.yaml_agent.max_tokens != null && (
                      <Descriptions.Item label="Max Tokens">
                        {yamlParsed.yaml_agent.max_tokens.toLocaleString()}
                      </Descriptions.Item>
                    )}
                  </Descriptions>
                ) : (
                  <Text type="secondary">No agent configuration in YAML.</Text>
                ),
              },

              // ── Security ──
              {
                key: "security",
                label: <Space size={4}><SafetyCertificateOutlined /><span>Security</span></Space>,
                children: (
                  <Space direction="vertical" size="middle" style={{ width: "100%" }}>
                    {yamlParsed.yaml_security_profile && (
                      <div>
                        <Text type="secondary" style={{ fontSize: 12 }}>Profile</Text>
                        <div style={{ marginTop: 4 }}>
                          <Tag color="purple" style={{ borderRadius: 4 }}>{yamlParsed.yaml_security_profile}</Tag>
                        </div>
                      </div>
                    )}
                    {yamlParsed.yaml_sandbox_paths.length > 0 && (
                      <div>
                        <Text type="secondary" style={{ fontSize: 12 }}>Sandbox Paths</Text>
                        <Space size={[4, 4]} wrap style={{ marginTop: 4 }}>
                          {yamlParsed.yaml_sandbox_paths.map((p) => (
                            <Tag key={p} style={{ borderRadius: 4, fontFamily: "monospace", fontSize: 11 }}>{p}</Tag>
                          ))}
                        </Space>
                      </div>
                    )}
                    {!yamlParsed.yaml_security_profile && yamlParsed.yaml_sandbox_paths.length === 0 && (
                      <Text type="secondary">No explicit security configuration in YAML.</Text>
                    )}
                  </Space>
                ),
              },

              // ── Deny rules ──
              {
                key: "deny",
                label: (
                  <Space size={4}>
                    <StopOutlined />
                    <span>Deny Rules</span>
                    {yamlParsed.yaml_deny.length > 0 && (
                      <Tag color="red" style={{ borderRadius: 4, fontSize: 10 }}>{yamlParsed.yaml_deny.length}</Tag>
                    )}
                  </Space>
                ),
                children: yamlParsed.yaml_deny.length > 0 ? (
                  <Table
                    size="small"
                    pagination={false}
                    dataSource={yamlParsed.yaml_deny}
                    rowKey={(r, i) => `${r.module}-${r.action}-${i}`}
                    columns={[
                      {
                        title: "Module",
                        dataIndex: "module",
                        key: "module",
                        render: (m: string) => m ? <Tag color="blue" style={{ borderRadius: 4 }}>{m}</Tag> : <Text type="secondary">—</Text>,
                      },
                      {
                        title: "Action",
                        dataIndex: "action",
                        key: "action",
                        render: (a: string) => a ? <Text code style={{ fontSize: 11 }}>{a}</Text> : <Text type="secondary">all</Text>,
                      },
                      {
                        title: "Reason",
                        dataIndex: "reason",
                        key: "reason",
                        render: (r: string) => r ? <Text type="secondary" style={{ fontSize: 12 }}>{r}</Text> : <Text type="secondary">—</Text>,
                      },
                      {
                        title: "Condition",
                        dataIndex: "when",
                        key: "when",
                        render: (w: string) => w ? <Text code style={{ fontSize: 10 }}>{w}</Text> : <Text type="secondary">—</Text>,
                      },
                    ]}
                  />
                ) : (
                  <Text type="secondary">No deny rules declared in YAML.</Text>
                ),
              },

              // ── Approval required ──
              {
                key: "approval",
                label: (
                  <Space size={4}>
                    <LockOutlined />
                    <span>Approval</span>
                    {yamlParsed.yaml_approval_required.length > 0 && (
                      <Tag color="orange" style={{ borderRadius: 4, fontSize: 10 }}>{yamlParsed.yaml_approval_required.length}</Tag>
                    )}
                  </Space>
                ),
                children: yamlParsed.yaml_approval_required.length > 0 ? (
                  <Table
                    size="small"
                    pagination={false}
                    dataSource={yamlParsed.yaml_approval_required}
                    rowKey={(r, i) => `${r.module}-${r.action}-${i}`}
                    columns={[
                      {
                        title: "Module",
                        dataIndex: "module",
                        key: "module",
                        render: (m: string) => m ? <Tag color="blue" style={{ borderRadius: 4 }}>{m}</Tag> : <Text type="secondary">—</Text>,
                      },
                      {
                        title: "Action",
                        dataIndex: "action",
                        key: "action",
                        render: (a: string) => a ? <Text code style={{ fontSize: 11 }}>{a}</Text> : <Text type="secondary">all</Text>,
                      },
                      {
                        title: "Message",
                        dataIndex: "message",
                        key: "message",
                        ellipsis: true,
                        render: (m: string) => m ? <Text style={{ fontSize: 12 }}>{m}</Text> : <Text type="secondary">—</Text>,
                      },
                      {
                        title: "Timeout",
                        dataIndex: "timeout",
                        key: "timeout",
                        render: (t: string) => t ? <Tag style={{ borderRadius: 4 }}>{t}</Tag> : <Text type="secondary">—</Text>,
                      },
                      {
                        title: "On Timeout",
                        dataIndex: "on_timeout",
                        key: "on_timeout",
                        render: (o: string) => o ? (
                          <Tag color={o === "reject" ? "red" : "green"} style={{ borderRadius: 4 }}>{o}</Tag>
                        ) : <Text type="secondary">—</Text>,
                      },
                    ]}
                  />
                ) : (
                  <Text type="secondary">No approval rules declared in YAML.</Text>
                ),
              },

              // ── Triggers ──
              {
                key: "triggers",
                label: (
                  <Space size={4}>
                    <ApiOutlined />
                    <span>Triggers</span>
                    {yamlParsed.yaml_triggers.length > 0 && (
                      <Tag color="cyan" style={{ borderRadius: 4, fontSize: 10 }}>{yamlParsed.yaml_triggers.length}</Tag>
                    )}
                  </Space>
                ),
                children: yamlParsed.yaml_triggers.length > 0 ? (
                  <Space size={[8, 8]} wrap>
                    {yamlParsed.yaml_triggers.map((t) => (
                      <Tag key={t.id} color="cyan" style={{ borderRadius: 4 }}>
                        <strong>{t.type}</strong>{t.id ? ` · ${t.id}` : ""}
                      </Tag>
                    ))}
                  </Space>
                ) : (
                  <Text type="secondary">No triggers declared in YAML.</Text>
                ),
              },

              // ── Variables ──
              {
                key: "variables",
                label: <Space size={4}><AppstoreOutlined /><span>Variables</span></Space>,
                children: Object.keys(yamlParsed.yaml_variables).length > 0 ? (
                  <Table
                    size="small"
                    pagination={false}
                    dataSource={Object.entries(yamlParsed.yaml_variables).map(([k, v]) => ({ key: k, value: String(v) }))}
                    rowKey="key"
                    columns={[
                      {
                        title: "Variable",
                        dataIndex: "key",
                        key: "key",
                        render: (k: string) => <Text code style={{ fontSize: 12 }}>{k}</Text>,
                      },
                      {
                        title: "Default value",
                        dataIndex: "value",
                        key: "value",
                        render: (v: string) => <Text style={{ fontSize: 12, fontFamily: "monospace" }}>{v}</Text>,
                      },
                    ]}
                  />
                ) : (
                  <Text type="secondary">No variables declared in YAML.</Text>
                ),
              },
            ]}
          />
        </Card>
      )}

      {/* ── Secrets / Environment Variables ── */}
      <Card
        title={<Space><LockOutlined /><span>Secrets & Environment Variables</span></Space>}
        extra={<Text type="secondary" style={{ fontSize: 12 }}>Injected at runtime as env vars + {"{{secret.KEY}}"}</Text>}
      >
        <Space direction="vertical" size="middle" style={{ width: "100%" }}>
          {/* Existing secrets */}
          {secrets.isLoading ? (
            <Spin size="small" />
          ) : (secrets.data ?? []).length > 0 ? (
            <Table
              dataSource={secrets.data}
              rowKey="key"
              size="small"
              pagination={false}
              columns={[
                {
                  title: "Key",
                  dataIndex: "key",
                  key: "key",
                  render: (k: string) => <Text code>{k}</Text>,
                },
                {
                  title: "Value",
                  key: "value",
                  render: () => <Text type="secondary">••••••••</Text>,
                },
                {
                  title: "Updated",
                  dataIndex: "updated_at",
                  key: "updated_at",
                  render: (ts: number) => (
                    <Tooltip title={formatTimestamp(ts)}>
                      <Text type="secondary" style={{ fontSize: 12 }}>{timeAgo(ts)}</Text>
                    </Tooltip>
                  ),
                },
                {
                  title: "",
                  key: "actions",
                  width: 60,
                  render: (_: unknown, record: { key: string }) => (
                    <Popconfirm
                      title={`Delete secret '${record.key}'?`}
                      onConfirm={() => handleDeleteSecret(record.key)}
                      okText="Yes"
                      cancelText="No"
                    >
                      <Button size="small" danger icon={<DeleteOutlined />} />
                    </Popconfirm>
                  ),
                },
              ]}
            />
          ) : (
            <Alert
              type="info"
              message="No secrets configured"
              description="Add environment variables like ANTHROPIC_API_KEY, OPENAI_API_KEY, etc. They will be injected into the app at runtime."
              showIcon
              icon={<LockOutlined />}
            />
          )}

          {/* Add secret form */}
          <Card size="small" style={{ background: "var(--ant-color-bg-layout)" }}>
            <Space size="middle" style={{ width: "100%" }} align="end">
              <div style={{ flex: 1 }}>
                <Text type="secondary" style={{ fontSize: 12 }}>Key</Text>
                <Input
                  placeholder="e.g. ANTHROPIC_API_KEY"
                  value={newSecretKey}
                  onChange={(e) => setNewSecretKey(e.target.value.toUpperCase().replace(/[^A-Z0-9_]/g, "_"))}
                  style={{ fontFamily: "monospace" }}
                />
              </div>
              <div style={{ flex: 2 }}>
                <Text type="secondary" style={{ fontSize: 12 }}>Value</Text>
                <Input.Password
                  placeholder="sk-ant-..."
                  value={newSecretValue}
                  onChange={(e) => setNewSecretValue(e.target.value)}
                />
              </div>
              <Button
                type="primary"
                icon={<PlusOutlined />}
                onClick={handleAddSecret}
                loading={setSecret.isPending}
                disabled={!newSecretKey.trim() || !newSecretValue}
              >
                Add
              </Button>
            </Space>
          </Card>
        </Space>
      </Card>

      {/* Security (Module Access, OS Permissions, Quotas) */}
      <AppSecurityTab
        app={appData}
        onUpdateApp={handleUpdateApp}
        savingApp={updateApp.isPending}
        yamlParsed={yamlParsed}
        onSyncFromYaml={handleSyncFromYaml}
        syncingFromYaml={syncFromYaml.isPending}
        linkedYamlAppId={linkedYamlApp?.id}
        onSyncToYaml={linkedYamlApp ? handleSyncToYaml : undefined}
      />

      {/* Agents */}
      <Card
        title={<Space><UserOutlined /><span>Agents</span></Space>}
        extra={
          <Space>
            <Text type="secondary" style={{ fontSize: 12 }}>{agents.data?.length ?? 0} agents</Text>
            <Button size="small" icon={<PlusOutlined />} onClick={() => setShowCreateAgent(true)}>Create Agent</Button>
          </Space>
        }
      >
        <Table columns={agentColumns} dataSource={agents.data ?? []} rowKey="agent_id" loading={agents.isLoading} pagination={false} size="small" />
      </Card>

      {/* Sessions */}
      <Card
        title={<Space><FieldTimeOutlined /><span>Active Sessions</span></Space>}
        extra={<Text type="secondary" style={{ fontSize: 12 }}>{sessions.data?.length ?? 0} sessions</Text>}
      >
        <Table
          columns={sessionColumns}
          dataSource={sessions.data ?? []}
          rowKey="session_id"
          loading={sessions.isLoading}
          pagination={{ pageSize: 20, showTotal: (total) => `${total} sessions` }}
          size="small"
        />
      </Card>

      {/* ── Register YAML App Modal ── */}
      <Modal
        title="Register YAML App"
        open={registerModalOpen}
        onCancel={() => setRegisterModalOpen(false)}
        onOk={handleRegister}
        confirmLoading={registerApp.isPending}
        okText="Compile & Register"
        width={700}
      >
        <Space direction="vertical" size="middle" style={{ width: "100%" }}>
          <Alert type="info" message="Upload a .app.yaml file or paste the YAML content. It will be automatically linked to this application." showIcon />
          <Upload.Dragger
            accept=".yaml,.yml"
            maxCount={1}
            showUploadList={false}
            beforeUpload={(file) => {
              const reader = new FileReader();
              reader.onload = (e) => {
                const content = e.target?.result;
                if (typeof content === "string") {
                  setYamlText(content);
                  messageApi.success(`Loaded ${file.name}`);
                }
              };
              reader.readAsText(file);
              return false;
            }}
          >
            <p style={{ fontSize: 14, margin: "8px 0" }}>
              <UploadOutlined style={{ fontSize: 20, color: "#1677ff", marginRight: 8 }} />
              Click or drag a <strong>.app.yaml</strong> file here
            </p>
          </Upload.Dragger>
          <div>
            <Text strong>Or paste YAML content</Text>
            <TextArea
              rows={10}
              placeholder={`app:\n  name: my-app\n  version: "1.0"\n\nagent:\n  brain:\n    provider: anthropic\n    model: claude-sonnet-4-20250514\n  tools:\n    - module: filesystem\n      action: read_file`}
              value={yamlText}
              onChange={(e) => setYamlText(e.target.value)}
              style={{ fontFamily: "monospace", fontSize: 12, marginTop: 4 }}
            />
          </div>
        </Space>
      </Modal>

      {/* ── Run App Modal ── */}
      <Modal
        title={<Space><PlayCircleOutlined /><span>Run App</span>{isStreaming && <Spin size="small" />}</Space>}
        open={runModalOpen}
        onCancel={() => {
          abortRef.current?.abort();
          setRunModalOpen(false);
          setRunResult(null);
          setIsStreaming(false);
        }}
        footer={
          <Space>
            {isStreaming ? (
              <Button danger icon={<CloseCircleOutlined />} onClick={() => abortRef.current?.abort()}>Stop</Button>
            ) : (
              <Button onClick={() => setRunModalOpen(false)}>Close</Button>
            )}
            <Button type="primary" icon={<PlayCircleOutlined />} onClick={handleRun} loading={isStreaming} disabled={isStreaming}>
              Run
            </Button>
          </Space>
        }
        width={700}
      >
        <Space direction="vertical" size="middle" style={{ width: "100%" }}>
          <div>
            <Text strong>Input</Text>
            <TextArea
              rows={3}
              placeholder="What do you want the app to do?"
              value={runInput}
              onChange={(e) => setRunInput(e.target.value)}
              disabled={isStreaming}
              style={{ marginTop: 4 }}
            />
          </div>
          {runResult !== null && (
            <div>
              <Space style={{ marginBottom: 4 }}>
                <Text strong>Output</Text>
                {isStreaming && <Badge status="processing" text="Streaming..." />}
              </Space>
              <pre
                ref={(el) => { if (el) el.scrollTop = el.scrollHeight; }}
                style={{
                  background: "#1a1a2e",
                  color: "#e0e0e0",
                  padding: 12,
                  borderRadius: 6,
                  maxHeight: 400,
                  overflow: "auto",
                  fontSize: 12,
                  marginTop: 4,
                  fontFamily: "monospace",
                  whiteSpace: "pre-wrap",
                  wordBreak: "break-word",
                }}
              >
                {runResult || (isStreaming ? "Waiting for output..." : "No output")}
              </pre>
            </div>
          )}
        </Space>
      </Modal>

      {/* Create Agent Modal */}
      <Modal
        title="Create Agent"
        open={showCreateAgent}
        onCancel={() => { setShowCreateAgent(false); agentForm.resetFields(); }}
        footer={null}
        width={420}
      >
        <Form form={agentForm} layout="vertical" onFinish={handleCreateAgent} initialValues={{ role: "agent" }}>
          <Form.Item name="name" label="Agent Name" rules={[{ required: true, message: "Agent name is required" }]}>
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
              <Button type="primary" htmlType="submit" icon={<PlusOutlined />} loading={createAgent.isPending}>Create</Button>
              <Button onClick={() => { setShowCreateAgent(false); agentForm.resetFields(); }}>Cancel</Button>
            </Space>
          </Form.Item>
        </Form>
      </Modal>

      {/* API Key Generated Modal */}
      <Modal
        title={<Space><KeyOutlined /><span>API Key Generated</span></Space>}
        open={generatedKey !== null}
        onCancel={() => setGeneratedKey(null)}
        footer={<Button type="primary" onClick={() => setGeneratedKey(null)}>Done</Button>}
        width={520}
      >
        {generatedKey && (
          <Space direction="vertical" size="middle" style={{ width: "100%" }}>
            <Alert type="warning" icon={<WarningOutlined />} message="Save this API key now" description="This key will only be shown once. It cannot be retrieved later." showIcon />
            <Card size="small" style={{ background: "var(--ant-color-bg-layout)" }}>
              <Paragraph copyable={{ text: generatedKey.api_key ?? "" }} style={{ fontFamily: "monospace", fontSize: 13, marginBottom: 0, wordBreak: "break-all" }}>
                {generatedKey.api_key}
              </Paragraph>
            </Card>
            <Descriptions size="small" column={1}>
              <Descriptions.Item label="Key ID">
                <Text copyable style={{ fontSize: 12, fontFamily: "monospace" }}>{generatedKey.key_id}</Text>
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
