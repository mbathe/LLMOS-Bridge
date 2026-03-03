"use client";

import React, { useEffect, useState, useCallback } from "react";
import {
  Tabs,
  Card,
  Select,
  Button,
  Space,
  Divider,
  Typography,
  Tag,
  Spin,
  Alert,
  Table,
  Form,
  Input,
  Radio,
  Row,
  Col,
  InputNumber,
  Tooltip,
  Badge,
  message,
} from "antd";
import {
  LockOutlined,
  AppstoreOutlined,
  DeleteOutlined,
  KeyOutlined,
  PlusOutlined,
  DashboardOutlined,
  SafetyCertificateOutlined,
  ClockCircleOutlined,
  TeamOutlined,
  StopOutlined,
  CheckCircleOutlined,
} from "@ant-design/icons";
import { api, ApiError } from "@/lib/api/client";
import { useApplicationPermissions } from "@/hooks/useApplicationPermissions";
import type { ApplicationResponse, SessionResponse, UpdateApplicationRequest } from "@/types/application";
import type { PermissionGrant } from "@/types/security";
import type { ColumnsType } from "antd/es/table";

const { Text } = Typography;

// ── Permission risk registry ──────────────────────────────────────────────────

const PERMISSION_RISK: Record<string, string> = {
  "filesystem.read": "low",
  "filesystem.write": "medium",
  "filesystem.delete": "high",
  "filesystem.sensitive": "critical",
  "device.camera": "high",
  "device.microphone": "high",
  "device.screen": "medium",
  "device.keyboard": "critical",
  "network.read": "low",
  "network.send": "medium",
  "network.external": "medium",
  "data.database.read": "low",
  "data.database.write": "medium",
  "data.database.delete": "high",
  "data.credentials": "critical",
  "data.personal": "high",
  "os.process.read": "low",
  "os.process.execute": "medium",
  "os.process.kill": "high",
  "os.admin": "critical",
  "os.environment.read": "low",
  "os.environment.write": "medium",
  "app.browser": "medium",
  "app.email.read": "medium",
  "app.email.send": "high",
  "iot.gpio.read": "low",
  "iot.gpio.write": "medium",
  "iot.sensor": "low",
  "iot.actuator": "high",
  "module.read": "low",
  "module.manage": "medium",
  "module.install": "high",
};

const KNOWN_PERMISSIONS = Object.keys(PERMISSION_RISK);

const riskTagColors: Record<string, string> = {
  low: "green",
  medium: "orange",
  high: "red",
  critical: "volcano",
};

function getErrorMessage(e: unknown): string {
  if (e instanceof ApiError) return e.detail ?? e.message ?? "Unknown error";
  if (e instanceof Error) return e.message;
  return "Unknown error";
}

// ── Types ─────────────────────────────────────────────────────────────────────

interface ModuleActionInfo {
  name: string;
  os_permissions?: string[];
}

interface ModuleInfo {
  module_id: string;
  actions: ModuleActionInfo[];
}

interface AppSecurityTabProps {
  app: ApplicationResponse;
  onUpdateApp: (updates: UpdateApplicationRequest) => Promise<void>;
  savingApp: boolean;
}

// ── Tab 1: Module Access ──────────────────────────────────────────────────────

function ModuleAccessTab({
  app,
  onUpdateApp,
  savingApp,
}: AppSecurityTabProps) {
  const [allModules, setAllModules] = useState<string[]>([]);
  const [moduleActions, setModuleActions] = useState<Record<string, string[]>>({});
  const [loadingModules, setLoadingModules] = useState(false);
  const [selectedModules, setSelectedModules] = useState<string[]>([]);
  const [actionRules, setActionRules] = useState<Record<string, string[]>>({});
  const [dirty, setDirty] = useState(false);

  useEffect(() => {
    setLoadingModules(true);
    api
      .get<ModuleInfo[]>("/modules")
      .then((mods) => setAllModules(mods.map((m) => m.module_id)))
      .catch(() => message.error("Failed to load module list"))
      .finally(() => setLoadingModules(false));
  }, []);

  useEffect(() => {
    const mods = app.allowed_modules ?? [];
    setSelectedModules(mods);
    setActionRules(app.allowed_actions ?? {});
    setDirty(false);
    [...mods, ...Object.keys(app.allowed_actions ?? {})].forEach(loadModuleActions);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [app.app_id]);

  const loadModuleActions = useCallback(async (moduleId: string) => {
    if (!moduleId) return;
    setModuleActions((prev) => {
      if (prev[moduleId] !== undefined) return prev;
      return { ...prev, [moduleId]: null as unknown as string[] };
    });
    try {
      const mod = await api.get<ModuleInfo>(`/modules/${moduleId}`);
      setModuleActions((prev) => ({
        ...prev,
        [moduleId]: mod.actions?.map((a) => a.name) ?? [],
      }));
    } catch {
      setModuleActions((prev) => ({ ...prev, [moduleId]: [] }));
    }
  }, []);

  const handleModulesChange = (values: string[]) => {
    setSelectedModules(values);
    setDirty(true);
    for (const m of values) {
      loadModuleActions(m);
      setActionRules((prev) => (m in prev ? prev : { ...prev, [m]: [] }));
    }
    setActionRules((prev) => {
      const next = { ...prev };
      for (const m of Object.keys(next)) {
        if (!values.includes(m) && (next[m] ?? []).length === 0) delete next[m];
      }
      return next;
    });
  };

  const handleSave = async () => {
    const allowedActions: Record<string, string[]> = {};
    for (const [mod, actions] of Object.entries(actionRules)) {
      if ((actions ?? []).length > 0) allowedActions[mod] = actions;
    }
    await onUpdateApp({ allowed_modules: selectedModules, allowed_actions: allowedActions });
    setDirty(false);
  };

  const extraRuleModules = Object.keys(actionRules).filter((m) => !selectedModules.includes(m));
  const ruleModules = [...selectedModules, ...extraRuleModules];
  const addableModules = allModules.filter((m) => !ruleModules.includes(m));

  return (
    <Spin spinning={loadingModules}>
      <Space direction="vertical" size="large" style={{ width: "100%" }}>

        {/* Allowed Modules */}
        <div>
          <Space style={{ marginBottom: 6 }}>
            <AppstoreOutlined />
            <Text strong>Allowed Modules</Text>
          </Space>
          <Text type="secondary" style={{ display: "block", fontSize: 12, marginBottom: 8 }}>
            Which modules this application can invoke. Leave empty to allow all modules.
          </Text>
          <Select
            mode="multiple"
            style={{ width: "100%" }}
            placeholder="All modules allowed (no restriction)"
            value={selectedModules}
            onChange={handleModulesChange}
            options={allModules.map((id) => ({ label: id, value: id }))}
            allowClear
            showSearch
            filterOption={(input, opt) =>
              (opt?.label ?? "").toLowerCase().includes(input.toLowerCase())
            }
          />
        </div>

        <Divider style={{ margin: "4px 0" }} />

        {/* Allowed Actions per Module */}
        <div>
          <Space style={{ marginBottom: 6 }}>
            <LockOutlined />
            <Text strong>Allowed Actions per Module</Text>
          </Space>
          <Text type="secondary" style={{ display: "block", fontSize: 12, marginBottom: 12 }}>
            For each module, choose which actions are allowed.{" "}
            <strong>Leave the list empty to allow all actions</strong> of that module.
          </Text>

          <Space direction="vertical" size={8} style={{ width: "100%" }}>
            {ruleModules.map((modId) => {
              const availableActions = moduleActions[modId];
              const loadingActions = availableActions === null;
              const selected = actionRules[modId] ?? [];
              const isFromModuleFilter = selectedModules.includes(modId);

              return (
                <Card
                  key={modId}
                  size="small"
                  style={{ background: "var(--ant-color-bg-layout)" }}
                  title={
                    <Space size={4}>
                      <Tag color={isFromModuleFilter ? "blue" : "default"} style={{ borderRadius: 4 }}>
                        {modId}
                      </Tag>
                      {!isFromModuleFilter && (
                        <Tag style={{ borderRadius: 4, fontSize: 11 }}>extra rule</Tag>
                      )}
                    </Space>
                  }
                  extra={
                    !isFromModuleFilter && (
                      <Button
                        type="text"
                        size="small"
                        danger
                        icon={<DeleteOutlined />}
                        onClick={() => {
                          setActionRules((prev) => {
                            const next = { ...prev };
                            delete next[modId];
                            return next;
                          });
                          setDirty(true);
                        }}
                      />
                    )
                  }
                >
                  <Select
                    mode="multiple"
                    style={{ width: "100%" }}
                    placeholder="All actions allowed (leave empty)"
                    value={selected}
                    onChange={(val) => {
                      setActionRules((prev) => ({ ...prev, [modId]: val }));
                      setDirty(true);
                    }}
                    onFocus={() => loadModuleActions(modId)}
                    options={(availableActions ?? []).map((a) => ({ label: a, value: a }))}
                    loading={loadingActions}
                    allowClear
                    showSearch
                  />
                  {selected.length === 0 && (
                    <Text type="secondary" style={{ fontSize: 11, marginTop: 4, display: "block" }}>
                      All actions of <strong>{modId}</strong> are allowed
                    </Text>
                  )}
                </Card>
              );
            })}
          </Space>

          {addableModules.length > 0 && (
            <Select
              style={{ width: "100%", marginTop: 8 }}
              placeholder="+ Restrict actions for another module..."
              value={null}
              onSelect={(val: string | null) => {
                if (!val) return;
                loadModuleActions(val);
                setActionRules((prev) => ({ ...prev, [val]: [] }));
                setDirty(true);
              }}
              options={addableModules.map((m) => ({ label: m, value: m }))}
              showSearch
              filterOption={(input, opt) =>
                (opt?.label ?? "").toLowerCase().includes(input.toLowerCase())
              }
            />
          )}

          {ruleModules.length === 0 && (
            <Text type="secondary" style={{ fontSize: 12 }}>
              All modules and actions are allowed. Select modules above to configure restrictions.
            </Text>
          )}
        </div>

        <Button
          type="primary"
          loading={savingApp}
          onClick={handleSave}
          disabled={!dirty}
        >
          Save Module Access
        </Button>
      </Space>
    </Spin>
  );
}

// ── Tab 2: OS Permissions ─────────────────────────────────────────────────────

function OsPermissionsTab({ app }: { app: ApplicationResponse }) {
  const [grantForm] = Form.useForm();
  const [allModules, setAllModules] = useState<string[]>([]);
  // Cache: module_id → list of OS permissions it actually uses (from @requires_permission)
  const [modulePermCache, setModulePermCache] = useState<Record<string, string[]>>({});
  const [selectedModuleId, setSelectedModuleId] = useState<string | null>(null);
  const { permissions, grantPermission, revokePermission } = useApplicationPermissions(app.app_id);
  const grants = permissions.data?.grants ?? [];

  useEffect(() => {
    api
      .get<ModuleInfo[]>("/modules")
      .then((mods) => setAllModules(mods.map((m) => m.module_id)))
      .catch(() => {/* silent */});
  }, []);

  // When a module is selected, fetch its manifest (if not cached) and extract unique OS permissions
  const handleModuleSelect = async (moduleId: string) => {
    setSelectedModuleId(moduleId);
    grantForm.setFieldValue("permission", undefined);

    if (modulePermCache[moduleId] !== undefined) return;

    try {
      const mod = await api.get<ModuleInfo>(`/modules/${moduleId}`);
      const perms = [...new Set(
        (mod.actions ?? []).flatMap((a) => a.os_permissions ?? [])
      )];
      setModulePermCache((prev) => ({ ...prev, [moduleId]: perms }));
    } catch {
      setModulePermCache((prev) => ({ ...prev, [moduleId]: [] }));
    }
  };

  // Permissions to show in the dropdown for the currently selected module.
  // - undefined  → cache miss (still loading) → show all
  // - []         → module has no @requires_permission actions → show all as fallback
  // - [...]      → use the module-specific list
  const cachedPerms = selectedModuleId ? modulePermCache[selectedModuleId] : undefined;
  const availablePermissions: string[] = cachedPerms?.length ? cachedPerms : KNOWN_PERMISSIONS;

  const handleGrant = () => {
    grantForm.validateFields().then((values) => {
      grantPermission.mutate(
        {
          permission: values.permission,
          module_id: values.module_id,
          scope: values.scope,
          reason: values.reason || "",
        },
        {
          onSuccess: () => {
            grantForm.resetFields();
            setSelectedModuleId(null);
            message.success("Permission granted");
          },
          onError: (err) => message.error(getErrorMessage(err)),
        },
      );
    });
  };

  const handleRevoke = (grant: PermissionGrant) => {
    revokePermission.mutate(
      { permission: grant.permission, module_id: grant.module_id },
      {
        onSuccess: () => message.success("Permission revoked"),
        onError: (err) => message.error(getErrorMessage(err)),
      },
    );
  };

  const columns: ColumnsType<PermissionGrant> = [
    {
      title: "Permission",
      dataIndex: "permission",
      key: "permission",
      render: (perm: string) => (
        <Space>
          <KeyOutlined style={{ color: "#1677ff" }} />
          <Text strong style={{ fontFamily: "monospace", fontSize: 12 }}>{perm}</Text>
        </Space>
      ),
    },
    {
      title: "Risk",
      key: "risk",
      width: 90,
      render: (_: unknown, record: PermissionGrant) => {
        const risk = PERMISSION_RISK[record.permission] ?? "medium";
        return (
          <Tag
            color={riskTagColors[risk]}
            style={{ borderRadius: 4, textTransform: "capitalize" as const }}
          >
            {risk}
          </Tag>
        );
      },
    },
    {
      title: "Module",
      dataIndex: "module_id",
      key: "module_id",
      render: (m: string) => <Tag color="purple" style={{ borderRadius: 4 }}>{m}</Tag>,
    },
    {
      title: "Scope",
      dataIndex: "scope",
      key: "scope",
      width: 130,
      render: (scope: string) => (
        <Badge
          status={scope === "permanent" ? "processing" : "warning"}
          text={
            <Tag color={scope === "permanent" ? "purple" : "cyan"} style={{ borderRadius: 4 }}>
              {scope === "permanent" ? (
                <Space size={4}><LockOutlined style={{ fontSize: 10 }} /><span>Permanent</span></Space>
              ) : (
                <Space size={4}><ClockCircleOutlined style={{ fontSize: 10 }} /><span>Session</span></Space>
              )}
            </Tag>
          }
        />
      ),
    },
    {
      title: "Reason",
      dataIndex: "reason",
      key: "reason",
      ellipsis: true,
      render: (v: string) =>
        v ? (
          <Tooltip title={v}>
            <Text type="secondary" style={{ fontSize: 12 }}>{v}</Text>
          </Tooltip>
        ) : (
          <Text type="secondary" style={{ fontSize: 12 }}>—</Text>
        ),
    },
    {
      title: "",
      key: "ops",
      width: 80,
      render: (_: unknown, record: PermissionGrant) => (
        <Tooltip title="Revoke this permission">
          <Button
            type="text"
            size="small"
            danger
            icon={<DeleteOutlined />}
            loading={revokePermission.isPending}
            onClick={() => handleRevoke(record)}
          >
            Revoke
          </Button>
        </Tooltip>
      ),
    },
  ];

  return (
    <Space direction="vertical" size="large" style={{ width: "100%" }}>
      <Alert
        type="info"
        showIcon
        message="OS-level permission grants"
        description={
          <span>
            These grants allow modules used by <strong>{app.name}</strong> to access OS resources
            (filesystem, network, devices, etc.). Grants are scoped exclusively to this application
            and do not affect other applications.
          </span>
        }
        style={{ borderRadius: 8 }}
      />

      {/* Grant form */}
      <Card
        size="small"
        title={<Space><PlusOutlined /><span>Grant New Permission</span></Space>}
        style={{ background: "var(--ant-color-bg-layout)" }}
      >
        <Form form={grantForm} layout="vertical">
          <Row gutter={16}>
            {/* Step 1: select module first */}
            <Col xs={24} sm={8}>
              <Form.Item
                name="module_id"
                label="Module"
                rules={[{ required: true, message: "Select a module" }]}
                extra="Select the module that needs this permission."
              >
                <Select
                  showSearch
                  placeholder="Select module…"
                  options={allModules.map((m) => ({ label: m, value: m }))}
                  onChange={handleModuleSelect}
                  filterOption={(input, option) =>
                    (option?.value as string)?.toLowerCase().includes(input.toLowerCase())
                  }
                />
              </Form.Item>
            </Col>

            {/* Step 2: permission filtered by module */}
            <Col xs={24} sm={8}>
              <Form.Item
                name="permission"
                label="Permission"
                rules={[{ required: true, message: "Select a permission" }]}
                extra={
                  !selectedModuleId
                    ? "Select a module first to filter relevant permissions."
                    : cachedPerms === undefined
                      ? "Loading module permissions…"
                      : cachedPerms.length === 0
                        ? "No declared permissions for this module — showing all."
                        : `${cachedPerms.length} permission(s) declared by this module.`
                }
              >
                <Select
                  showSearch
                  placeholder={selectedModuleId ? "Select permission…" : "Select a module first"}
                  disabled={!selectedModuleId}
                  options={availablePermissions.map((p) => ({
                    label: (
                      <Space>
                        <Text style={{ fontFamily: "monospace", fontSize: 12 }}>{p}</Text>
                        <Tag color={riskTagColors[PERMISSION_RISK[p] ?? "medium"]} style={{ fontSize: 10 }}>
                          {PERMISSION_RISK[p] ?? "medium"}
                        </Tag>
                      </Space>
                    ),
                    value: p,
                  }))}
                  filterOption={(input, option) =>
                    (option?.value as string)?.toLowerCase().includes(input.toLowerCase())
                  }
                />
              </Form.Item>
            </Col>

            <Col xs={24} sm={8}>
              <Form.Item name="scope" label="Scope" initialValue="permanent">
                <Radio.Group>
                  <Radio value="session">Session</Radio>
                  <Radio value="permanent">Permanent</Radio>
                </Radio.Group>
              </Form.Item>
            </Col>
          </Row>
          <Row gutter={16}>
            <Col xs={24} sm={16}>
              <Form.Item name="reason" label="Reason">
                <Input placeholder="Why is this permission needed?" />
              </Form.Item>
            </Col>
            <Col xs={24} sm={8} style={{ display: "flex", alignItems: "flex-end" }}>
              <Form.Item style={{ width: "100%" }}>
                <Button
                  type="primary"
                  icon={<PlusOutlined />}
                  onClick={handleGrant}
                  loading={grantPermission.isPending}
                  block
                >
                  Grant Permission
                </Button>
              </Form.Item>
            </Col>
          </Row>
        </Form>
      </Card>

      {/* Grants Table */}
      <Card
        title={
          <Space>
            <SafetyCertificateOutlined />
            <span>Active Permissions</span>
            <Tag color="blue">{grants.length}</Tag>
          </Space>
        }
      >
        {permissions.isLoading ? (
          <div style={{ textAlign: "center", padding: 40 }}>
            <Spin />
          </div>
        ) : grants.length === 0 ? (
          <Text type="secondary">
            No OS permissions granted for this application. All module actions that require
            elevated permissions will be blocked unless granted here.
          </Text>
        ) : (
          <Table
            columns={columns}
            dataSource={grants}
            rowKey={(record) => `${record.module_id}:${record.permission}`}
            pagination={false}
            size="small"
          />
        )}
      </Card>
    </Space>
  );
}

// ── Tab 3: Quotas ─────────────────────────────────────────────────────────────

function QuotasTab({ app, onUpdateApp, savingApp }: AppSecurityTabProps) {
  const [form] = Form.useForm();

  useEffect(() => {
    form.setFieldsValue({
      max_concurrent_plans: app.max_concurrent_plans,
      max_actions_per_plan: app.max_actions_per_plan,
    });
  }, [app.app_id, app.max_concurrent_plans, app.max_actions_per_plan, form]);

  const handleSave = async () => {
    const values = await form.validateFields();
    await onUpdateApp({
      max_concurrent_plans: values.max_concurrent_plans,
      max_actions_per_plan: values.max_actions_per_plan,
    });
  };

  return (
    <Space direction="vertical" size="large" style={{ width: "100%" }}>
      <Alert
        type="info"
        showIcon
        message="Resource quotas"
        description="Quotas prevent any single application from exhausting the daemon's execution capacity."
        style={{ borderRadius: 8 }}
      />
      <Form form={form} layout="vertical" style={{ maxWidth: 480 }}>
        <Form.Item
          name="max_concurrent_plans"
          label="Max Concurrent Plans"
          rules={[{ required: true }, { type: "number", min: 1, max: 100 }]}
          extra="Maximum number of IML plans this application can run simultaneously (1–100)."
        >
          <InputNumber min={1} max={100} style={{ width: "100%" }} />
        </Form.Item>
        <Form.Item
          name="max_actions_per_plan"
          label="Max Actions per Plan"
          rules={[{ required: true }, { type: "number", min: 1, max: 500 }]}
          extra="Maximum number of actions allowed in a single plan submitted by this application (1–500)."
        >
          <InputNumber min={1} max={500} style={{ width: "100%" }} />
        </Form.Item>
        <Form.Item>
          <Button type="primary" icon={<DashboardOutlined />} loading={savingApp} onClick={handleSave}>
            Save Quotas
          </Button>
        </Form.Item>
      </Form>
    </Space>
  );
}

// ── Tab 4: Sessions ───────────────────────────────────────────────────────────

function formatRelativeTime(ts: number): string {
  const diff = Date.now() / 1000 - ts;
  if (diff < 60) return "just now";
  if (diff < 3600) return `${Math.round(diff / 60)}m ago`;
  if (diff < 86400) return `${Math.round(diff / 3600)}h ago`;
  return new Date(ts * 1000).toLocaleDateString();
}

function formatExpiry(s: SessionResponse): React.ReactNode {
  if (s.expired) return <Tag color="red">Expired</Tag>;
  if (!s.expires_at && !s.idle_timeout_seconds) return <Text type="secondary">—</Text>;
  const parts: string[] = [];
  if (s.expires_at) {
    const remaining = s.expires_at - Date.now() / 1000;
    if (remaining < 0) return <Tag color="red">Expired</Tag>;
    if (remaining < 3600) parts.push(`${Math.round(remaining / 60)}m left`);
    else parts.push(new Date(s.expires_at * 1000).toLocaleString());
  }
  if (s.idle_timeout_seconds) parts.push(`idle: ${s.idle_timeout_seconds}s`);
  return <Text style={{ fontSize: 12 }}>{parts.join(" · ")}</Text>;
}

function SessionsTab({ app }: { app: ApplicationResponse }) {
  const [sessions, setSessions] = useState<SessionResponse[]>([]);
  const [loading, setLoading] = useState(false);
  const [creating, setCreating] = useState(false);
  const [showForm, setShowForm] = useState(false);
  const [form] = Form.useForm();
  const [allModules, setAllModules] = useState<string[]>([]);

  const loadSessions = useCallback(async () => {
    setLoading(true);
    try {
      const data = await api.get<SessionResponse[]>(`/applications/${app.app_id}/sessions`);
      setSessions(data);
    } catch {
      message.error("Failed to load sessions");
    } finally {
      setLoading(false);
    }
  }, [app.app_id]);

  useEffect(() => {
    loadSessions();
    api.get<{ module_id: string }[]>("/modules")
      .then((mods) => setAllModules(mods.map((m) => m.module_id)))
      .catch(() => {/* silent */});
  }, [loadSessions]);

  const handleCreate = async () => {
    const values = await form.validateFields();
    setCreating(true);
    try {
      const body: Record<string, unknown> = {};
      if (values.expires_in_seconds) body.expires_in_seconds = Number(values.expires_in_seconds);
      if (values.idle_timeout_seconds) body.idle_timeout_seconds = Number(values.idle_timeout_seconds);
      if (values.allowed_modules?.length) body.allowed_modules = values.allowed_modules;
      if (values.permission_grants?.length) body.permission_grants = values.permission_grants;
      if (values.permission_denials?.length) body.permission_denials = values.permission_denials;

      await api.post<SessionResponse>(`/applications/${app.app_id}/sessions`, body);
      message.success("Session created");
      form.resetFields();
      setShowForm(false);
      loadSessions();
    } catch (err) {
      message.error(getErrorMessage(err));
    } finally {
      setCreating(false);
    }
  };

  const handleDelete = async (sessionId: string) => {
    try {
      await api.delete(`/applications/${app.app_id}/sessions/${sessionId}`);
      message.success("Session deleted");
      setSessions((prev) => prev.filter((s) => s.session_id !== sessionId));
    } catch (err) {
      message.error(getErrorMessage(err));
    }
  };

  const columns: ColumnsType<SessionResponse> = [
    {
      title: "Session ID",
      dataIndex: "session_id",
      key: "session_id",
      render: (id: string) => (
        <Text style={{ fontFamily: "monospace", fontSize: 11 }}>{id.slice(0, 8)}…</Text>
      ),
    },
    {
      title: "Status",
      key: "status",
      width: 90,
      render: (_: unknown, s: SessionResponse) =>
        s.expired ? (
          <Tag color="red" icon={<StopOutlined />}>Expired</Tag>
        ) : (
          <Tag color="green" icon={<CheckCircleOutlined />}>Active</Tag>
        ),
    },
    {
      title: "Last Active",
      dataIndex: "last_active",
      key: "last_active",
      render: (ts: number) => (
        <Tooltip title={new Date(ts * 1000).toLocaleString()}>
          <Text style={{ fontSize: 12 }}>{formatRelativeTime(ts)}</Text>
        </Tooltip>
      ),
    },
    {
      title: "Expiry",
      key: "expiry",
      render: (_: unknown, s: SessionResponse) => formatExpiry(s),
    },
    {
      title: "Module Restrictions",
      key: "modules",
      render: (_: unknown, s: SessionResponse) =>
        s.allowed_modules.length === 0 ? (
          <Text type="secondary" style={{ fontSize: 11 }}>Inherits app</Text>
        ) : (
          <Space size={2} wrap>
            {s.allowed_modules.map((m) => (
              <Tag key={m} color="blue" style={{ fontSize: 10, borderRadius: 4 }}>{m}</Tag>
            ))}
          </Space>
        ),
    },
    {
      title: "Perm Denials",
      key: "denials",
      render: (_: unknown, s: SessionResponse) =>
        s.permission_denials.length === 0 ? (
          <Text type="secondary" style={{ fontSize: 11 }}>—</Text>
        ) : (
          <Space size={2} wrap>
            {s.permission_denials.map((p) => (
              <Tag key={p} color="volcano" style={{ fontSize: 10, borderRadius: 4 }}>{p}</Tag>
            ))}
          </Space>
        ),
    },
    {
      title: "",
      key: "ops",
      width: 80,
      render: (_: unknown, s: SessionResponse) => (
        <Button
          type="text"
          size="small"
          danger
          icon={<DeleteOutlined />}
          onClick={() => handleDelete(s.session_id)}
        >
          Delete
        </Button>
      ),
    },
  ];

  return (
    <Space direction="vertical" size="large" style={{ width: "100%" }}>
      <Alert
        type="info"
        showIcon
        message="Session security constraints"
        description={
          <span>
            Sessions for <strong>{app.name}</strong> can further restrict the application&apos;s permissions:
            limit which modules are accessible, block specific OS permissions, or set expiry timers.
            A session can only <em>restrict</em> — it cannot expand the application&apos;s grants.
          </span>
        }
        style={{ borderRadius: 8 }}
      />

      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
        <Text strong>
          <TeamOutlined /> Sessions ({sessions.length})
        </Text>
        <Space>
          <Button size="small" onClick={loadSessions} loading={loading}>Refresh</Button>
          <Button
            type="primary"
            size="small"
            icon={<PlusOutlined />}
            onClick={() => setShowForm(!showForm)}
          >
            New Session
          </Button>
        </Space>
      </div>

      {showForm && (
        <Card
          size="small"
          title={<Space><PlusOutlined /><span>Create Session</span></Space>}
          style={{ background: "var(--ant-color-bg-layout)" }}
          extra={
            <Button type="text" size="small" onClick={() => setShowForm(false)}>Cancel</Button>
          }
        >
          <Form form={form} layout="vertical">
            <Row gutter={16}>
              <Col xs={24} sm={12}>
                <Form.Item
                  name="expires_in_seconds"
                  label="Expires In (seconds)"
                  extra="Absolute expiry from now. Leave blank for no expiry."
                >
                  <InputNumber min={1} style={{ width: "100%" }} placeholder="e.g. 3600 (1 hour)" />
                </Form.Item>
              </Col>
              <Col xs={24} sm={12}>
                <Form.Item
                  name="idle_timeout_seconds"
                  label="Idle Timeout (seconds)"
                  extra="Auto-expire if inactive. Leave blank for no idle timeout."
                >
                  <InputNumber min={1} style={{ width: "100%" }} placeholder="e.g. 900 (15 min)" />
                </Form.Item>
              </Col>
            </Row>
            <Form.Item
              name="allowed_modules"
              label="Restrict Modules"
              extra="Subset of the application's allowed modules. Empty = inherit all."
            >
              <Select
                mode="multiple"
                placeholder="Inherit all modules from application"
                options={
                  (app.allowed_modules.length > 0 ? app.allowed_modules : allModules).map((m) => ({
                    label: m,
                    value: m,
                  }))
                }
                allowClear
                showSearch
              />
            </Form.Item>
            <Row gutter={16}>
              <Col xs={24} sm={12}>
                <Form.Item
                  name="permission_grants"
                  label="Additional OS Permission Grants"
                  extra="Temporary OS permissions for this session only."
                >
                  <Select
                    mode="multiple"
                    placeholder="No additional grants"
                    options={KNOWN_PERMISSIONS.map((p) => ({ label: p, value: p }))}
                    allowClear showSearch
                  />
                </Form.Item>
              </Col>
              <Col xs={24} sm={12}>
                <Form.Item
                  name="permission_denials"
                  label="OS Permission Denials"
                  extra="Explicitly block these OS permissions (override all grants)."
                >
                  <Select
                    mode="multiple"
                    placeholder="No denials"
                    options={KNOWN_PERMISSIONS.map((p) => ({ label: p, value: p }))}
                    allowClear showSearch
                  />
                </Form.Item>
              </Col>
            </Row>
            <Button type="primary" icon={<PlusOutlined />} loading={creating} onClick={handleCreate}>
              Create Session
            </Button>
          </Form>
        </Card>
      )}

      <Table
        columns={columns}
        dataSource={sessions}
        rowKey="session_id"
        loading={loading}
        pagination={{ pageSize: 10, hideOnSinglePage: true }}
        size="small"
        locale={{ emptyText: "No sessions found for this application." }}
      />
    </Space>
  );
}

// ── Main component ────────────────────────────────────────────────────────────

export function AppSecurityTab({ app, onUpdateApp, savingApp }: AppSecurityTabProps) {
  return (
    <Card
      title={
        <Space>
          <LockOutlined />
          <span>Security</span>
        </Space>
      }
    >
      <Tabs
        defaultActiveKey="modules"
        items={[
          {
            key: "modules",
            label: (
              <Space size={4}>
                <AppstoreOutlined />
                <span>Module Access</span>
              </Space>
            ),
            children: (
              <ModuleAccessTab app={app} onUpdateApp={onUpdateApp} savingApp={savingApp} />
            ),
          },
          {
            key: "permissions",
            label: (
              <Space size={4}>
                <KeyOutlined />
                <span>OS Permissions</span>
              </Space>
            ),
            children: <OsPermissionsTab app={app} />,
          },
          {
            key: "quotas",
            label: (
              <Space size={4}>
                <DashboardOutlined />
                <span>Quotas</span>
              </Space>
            ),
            children: (
              <QuotasTab app={app} onUpdateApp={onUpdateApp} savingApp={savingApp} />
            ),
          },
          {
            key: "sessions",
            label: (
              <Space size={4}>
                <TeamOutlined />
                <span>Sessions</span>
              </Space>
            ),
            children: <SessionsTab app={app} />,
          },
        ]}
      />
    </Card>
  );
}
