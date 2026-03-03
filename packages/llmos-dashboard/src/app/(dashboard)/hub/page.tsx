"use client";

import React, { useState, useMemo } from "react";
import { useRouter } from "next/navigation";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import {
  Card,
  Row,
  Col,
  Typography,
  Space,
  Button,
  Tag,
  Input,
  Spin,
  Alert,
  Tooltip,
  Table,
  Switch,
  Modal,
  Popconfirm,
  List,
  Avatar,
  Tabs,
  message,
} from "antd";
import type { ColumnsType } from "antd/es/table";
import {
  SearchOutlined,
  DownloadOutlined,
  DeleteOutlined,
  CheckCircleOutlined,
  SafetyCertificateOutlined,
  ReloadOutlined,
  AppstoreOutlined,
  ShopOutlined,
  FolderOpenOutlined,
  UploadOutlined,
  PoweroffOutlined,
  CloudDownloadOutlined,
  HddOutlined,
  CrownOutlined,
  ScanOutlined,
  ExclamationCircleOutlined,
} from "@ant-design/icons";
import { api, ApiError } from "@/lib/api/client";
import { PageHeader } from "@/components/common/PageHeader";
import { StatCard } from "@/components/common/StatCard";
import { EmptyState } from "@/components/common/EmptyState";
import { FolderPicker } from "@/components/common/FolderPicker";
import { useHub } from "@/hooks/useHub";
import type { InstalledModuleInfo, HubSearchResult, InstallResult } from "@/types/module";

const { Text } = Typography;

type UseHubReturn = ReturnType<typeof useHub>;

function getErrorMessage(error: unknown): string {
  if (error instanceof ApiError) {
    return error.detail ?? error.message ?? "Unknown error";
  }
  if (error instanceof Error) return error.message;
  return "Unknown error";
}

function formatDate(ts: number): string {
  if (!ts) return "\u2014";
  return new Date(ts * 1000).toLocaleDateString(undefined, {
    year: "numeric",
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

interface HubSearchResponse {
  results: HubSearchResult[];
  total: number;
}

// ─── Hub Remote Search Tab ───

function HubSearchTab() {
  const queryClient = useQueryClient();
  const [searchQuery, setSearchQuery] = useState("");
  const [debouncedQuery, setDebouncedQuery] = useState("");
  const debounceRef = React.useRef<ReturnType<typeof setTimeout> | null>(null);

  const handleSearch = (value: string) => {
    setSearchQuery(value);
    if (debounceRef.current) clearTimeout(debounceRef.current);
    debounceRef.current = setTimeout(() => setDebouncedQuery(value), 400);
  };

  const {
    data: searchResults,
    isLoading: searchLoading,
    error: searchError,
  } = useQuery<HubSearchResponse>({
    queryKey: ["hub-search", debouncedQuery],
    queryFn: () =>
      api.get<HubSearchResponse>("/admin/hub/search", { q: debouncedQuery }),
    enabled: debouncedQuery.length > 0,
    retry: false,
  });

  const installedQuery = useQuery({
    queryKey: ["hub-hub-installed"],
    queryFn: () =>
      api.get<{ modules: { module_id: string }[] }>("/admin/hub/installed"),
    retry: false,
  });

  const installedIds = new Set(
    (installedQuery.data?.modules ?? []).map((m) => m.module_id),
  );

  const installMutation = useMutation({
    mutationFn: (moduleId: string) =>
      api.post("/admin/hub/install", { module_id: moduleId }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["hub-hub-installed"] });
      queryClient.invalidateQueries({ queryKey: ["hub-search"] });
      queryClient.invalidateQueries({ queryKey: ["hub-installed"] });
    },
  });

  const uninstallMutation = useMutation({
    mutationFn: (moduleId: string) =>
      api.post("/admin/hub/uninstall", { module_id: moduleId }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["hub-hub-installed"] });
      queryClient.invalidateQueries({ queryKey: ["hub-search"] });
      queryClient.invalidateQueries({ queryKey: ["hub-installed"] });
    },
  });

  if (searchError && !debouncedQuery) {
    return (
      <Alert
        type="info"
        showIcon
        message="Remote hub not available"
        description="The remote module hub is not enabled on this instance. You can still install modules from local paths using the Local Install tab."
      />
    );
  }

  return (
    <Space direction="vertical" size="middle" style={{ width: "100%" }}>
      <Card styles={{ body: { padding: "16px 20px" } }}>
        <Input
          size="large"
          prefix={<SearchOutlined style={{ color: "#bfbfbf" }} />}
          placeholder="Search modules in the remote hub..."
          value={searchQuery}
          onChange={(e) => handleSearch(e.target.value)}
          allowClear
          style={{ borderRadius: 8 }}
        />
      </Card>

      {searchError && debouncedQuery && (
        <Alert
          type="info"
          showIcon
          message="Hub search unavailable"
          description="Remote hub is not enabled. Use the Local Install tab to install modules from local paths."
          closable
        />
      )}

      {debouncedQuery.length > 0 && !searchError && (
        <Card
          title={
            <Space>
              <SearchOutlined />
              <span>Search Results</span>
              {searchResults && (
                <Tag color="blue">{searchResults.total} found</Tag>
              )}
            </Space>
          }
          loading={searchLoading}
        >
          {!searchResults || searchResults.results.length === 0 ? (
            <EmptyState description="No modules found for your search." />
          ) : (
            <List
              dataSource={searchResults.results}
              renderItem={(item) => (
                <List.Item
                  actions={[
                    installedIds.has(item.module_id) ? (
                      <Button
                        key="uninstall"
                        danger
                        size="small"
                        icon={<DeleteOutlined />}
                        loading={uninstallMutation.isPending}
                        onClick={() => uninstallMutation.mutate(item.module_id)}
                      >
                        Uninstall
                      </Button>
                    ) : (
                      <Button
                        key="install"
                        type="primary"
                        size="small"
                        icon={<DownloadOutlined />}
                        loading={installMutation.isPending}
                        onClick={() => installMutation.mutate(item.module_id)}
                      >
                        Install
                      </Button>
                    ),
                  ]}
                >
                  <List.Item.Meta
                    avatar={
                      <Avatar
                        style={{ backgroundColor: "#e6f4ff", color: "#1677ff" }}
                        icon={<AppstoreOutlined />}
                        size={44}
                      />
                    }
                    title={
                      <Space size="small">
                        <Text strong style={{ fontSize: 15 }}>{item.module_id}</Text>
                        <Tag color="geekblue">v{item.version}</Tag>
                        {item.verified && (
                          <Tooltip title="Verified module">
                            <Tag color="success" icon={<SafetyCertificateOutlined />}>
                              Verified
                            </Tag>
                          </Tooltip>
                        )}
                        {installedIds.has(item.module_id) && (
                          <Tag color="green" icon={<CheckCircleOutlined />}>
                            Installed
                          </Tag>
                        )}
                      </Space>
                    }
                    description={
                      <Space direction="vertical" size={4}>
                        <Text type="secondary">{item.description}</Text>
                        <Space size="large">
                          <Text type="secondary" style={{ fontSize: 12 }}>
                            by <Text strong style={{ fontSize: 12 }}>{item.author}</Text>
                          </Text>
                          <Tooltip title="Total downloads">
                            <Text type="secondary" style={{ fontSize: 12 }}>
                              <DownloadOutlined style={{ marginRight: 4 }} />
                              {item.downloads.toLocaleString()}
                            </Text>
                          </Tooltip>
                        </Space>
                      </Space>
                    }
                  />
                </List.Item>
              )}
            />
          )}
        </Card>
      )}
    </Space>
  );
}

// ─── Local Install Tab ───

interface LocalInstallTabProps {
  hub: UseHubReturn;
}

function LocalInstallTab({ hub }: LocalInstallTabProps) {
  const [messageApi, contextHolder] = message.useMessage();
  const [installPath, setInstallPath] = useState("");
  const [folderPickerOpen, setFolderPickerOpen] = useState(false);
  const [lastResult, setLastResult] = useState<InstallResult | null>(null);
  const [lastError, setLastError] = useState<string | null>(null);

  const handleInstall = () => {
    if (!installPath.trim()) return;
    setLastResult(null);
    setLastError(null);
    hub.installFromPath.mutate(installPath.trim(), {
      onSuccess: (result) => {
        messageApi.success(
          `Installed ${result.module_id} v${result.version} (${result.installed_deps.length} deps)`,
        );
        setLastResult(result);
        setInstallPath("");
      },
      onError: (err) => {
        const msg = getErrorMessage(err);
        messageApi.error(msg);
        setLastError(msg);
      },
    });
  };

  const handleFolderSelect = (path: string) => {
    setInstallPath(path);
    setFolderPickerOpen(false);
  };

  return (
    <>
      {contextHolder}
      <Card
        title={
          <Space>
            <FolderOpenOutlined />
            <span>Install from Local Path</span>
          </Space>
        }
      >
        <Space direction="vertical" size="middle" style={{ width: "100%" }}>
          <Text type="secondary">
            Provide the absolute path to a module directory containing an{" "}
            <Text code>llmos-module.toml</Text> file, or use the folder browser.
          </Text>
          <Space.Compact style={{ width: "100%" }}>
            <Input
              size="large"
              placeholder="/home/user/my_module"
              prefix={<FolderOpenOutlined style={{ color: "#bfbfbf" }} />}
              value={installPath}
              onChange={(e) => setInstallPath(e.target.value)}
              onPressEnter={handleInstall}
              allowClear
            />
            <Button
              size="large"
              icon={<FolderOpenOutlined />}
              onClick={() => setFolderPickerOpen(true)}
            >
              Browse
            </Button>
            <Button
              size="large"
              type="primary"
              icon={<DownloadOutlined />}
              loading={hub.installFromPath.isPending}
              onClick={handleInstall}
              disabled={!installPath.trim()}
            >
              Install
            </Button>
          </Space.Compact>

          {lastResult && (
            <Alert
              type="success"
              showIcon
              message={`Installed ${lastResult.module_id} v${lastResult.version}`}
              description={
                <Space direction="vertical" size={2}>
                  {lastResult.scan_score >= 0 && (
                    <Text type="secondary">
                      Security scan: {Math.round(lastResult.scan_score)}/100
                      {lastResult.trust_tier && ` \u2022 Trust: ${lastResult.trust_tier}`}
                      {lastResult.scan_findings_count > 0 &&
                        ` \u2022 ${lastResult.scan_findings_count} finding(s)`}
                    </Text>
                  )}
                  {lastResult.installed_deps.length > 0 && (
                    <Text type="secondary">
                      Dependencies: {lastResult.installed_deps.join(", ")}
                    </Text>
                  )}
                  {lastResult.validation_warnings.length > 0 && (
                    <Text type="warning">
                      Warnings: {lastResult.validation_warnings.join("; ")}
                    </Text>
                  )}
                </Space>
              }
              closable
              onClose={() => setLastResult(null)}
            />
          )}

          {lastError && (
            <Alert
              type="error"
              showIcon
              message="Installation failed"
              description={lastError}
              closable
              onClose={() => setLastError(null)}
            />
          )}
        </Space>
      </Card>

      <FolderPicker
        open={folderPickerOpen}
        onCancel={() => setFolderPickerOpen(false)}
        onSelect={handleFolderSelect}
      />
    </>
  );
}

// ─── Main Hub Page ───

export default function HubPage() {
  const router = useRouter();
  const [messageApi, contextHolder] = message.useMessage();

  // Single hub instance shared with LocalInstallTab
  const hub = useHub();
  const modules = useMemo(
    () => hub.installed.data?.modules ?? [],
    [hub.installed.data?.modules],
  );

  // Upgrade modal
  const [upgradeModal, setUpgradeModal] = useState<{ open: boolean; moduleId: string }>({
    open: false,
    moduleId: "",
  });
  const [upgradePath, setUpgradePath] = useState("");
  const [upgradeFolderOpen, setUpgradeFolderOpen] = useState(false);

  // Stats
  const stats = useMemo(() => {
    const total = modules.length;
    const enabled = modules.filter((m) => m.enabled).length;
    const disabled = total - enabled;
    return { total, enabled, disabled };
  }, [modules]);

  // Enable/disable handler
  const handleToggle = (moduleId: string, checked: boolean) => {
    const mutation = checked ? hub.enableModule : hub.disableModule;
    mutation.mutate(moduleId, {
      onError: (err) => messageApi.error(getErrorMessage(err)),
    });
  };

  // Uninstall handler
  const handleUninstall = (moduleId: string) => {
    hub.uninstallModule.mutate(moduleId, {
      onSuccess: () => messageApi.success(`Uninstalled ${moduleId}`),
      onError: (err) => messageApi.error(getErrorMessage(err)),
    });
  };

  // Upgrade handler
  const handleUpgrade = () => {
    if (!upgradePath.trim() || !upgradeModal.moduleId) return;
    hub.upgradeModule.mutate(
      { moduleId: upgradeModal.moduleId, path: upgradePath.trim() },
      {
        onSuccess: (result) => {
          messageApi.success(`Upgraded ${result.module_id} to v${result.version}`);
          setUpgradeModal({ open: false, moduleId: "" });
          setUpgradePath("");
        },
        onError: (err) => messageApi.error(getErrorMessage(err)),
      },
    );
  };

  // Table columns
  const columns: ColumnsType<InstalledModuleInfo> = [
    {
      title: "Module ID",
      dataIndex: "module_id",
      key: "module_id",
      render: (id: string) => (
        <Button
          type="link"
          style={{ padding: 0, fontWeight: 600 }}
          onClick={() => router.push(`/modules/${id}`)}
        >
          {id}
        </Button>
      ),
    },
    {
      title: "Version",
      dataIndex: "version",
      key: "version",
      width: 100,
      render: (v: string) => <Tag color="geekblue">v{v}</Tag>,
    },
    {
      title: "Status",
      dataIndex: "enabled",
      key: "enabled",
      width: 100,
      render: (enabled: boolean) =>
        enabled ? (
          <Tag color="success">Enabled</Tag>
        ) : (
          <Tag color="default">Disabled</Tag>
        ),
    },
    {
      title: "Trust",
      dataIndex: "trust_tier",
      key: "trust_tier",
      width: 120,
      render: (tier: string) => {
        const config: Record<string, { color: string; icon: React.ReactNode }> = {
          official: { color: "gold", icon: <CrownOutlined /> },
          trusted: { color: "green", icon: <SafetyCertificateOutlined /> },
          verified: { color: "blue", icon: <CheckCircleOutlined /> },
          unverified: { color: "default", icon: <ExclamationCircleOutlined /> },
        };
        const c = config[tier] ?? config.unverified;
        return <Tag color={c.color} icon={c.icon}>{tier ?? "unverified"}</Tag>;
      },
    },
    {
      title: "Scan",
      dataIndex: "scan_score",
      key: "scan_score",
      width: 90,
      render: (score: number) => {
        if (score < 0) return <Text type="secondary">N/A</Text>;
        const color = score >= 70 ? "#52c41a" : score >= 30 ? "#faad14" : "#ff4d4f";
        return (
          <Tooltip title={`Security scan score: ${score}/100`}>
            <Tag color={color} icon={<ScanOutlined />}>
              {Math.round(score)}
            </Tag>
          </Tooltip>
        );
      },
    },
    {
      title: "Sandbox",
      dataIndex: "sandbox_level",
      key: "sandbox_level",
      width: 100,
      render: (level: string) => <Tag>{level}</Tag>,
    },
    {
      title: "Installed At",
      dataIndex: "installed_at",
      key: "installed_at",
      width: 180,
      render: (ts: number) => (
        <Text type="secondary" style={{ fontSize: 12 }}>
          {formatDate(ts)}
        </Text>
      ),
    },
    {
      title: "Actions",
      key: "actions",
      width: 320,
      render: (_: unknown, record: InstalledModuleInfo) => (
        <Space size="small">
          <Tooltip title={record.enabled ? "Disable" : "Enable"}>
            <Switch
              size="small"
              checked={record.enabled}
              loading={hub.enableModule.isPending || hub.disableModule.isPending}
              onChange={(checked) => handleToggle(record.module_id, checked)}
            />
          </Tooltip>
          <Tooltip title="Re-scan source code">
            <Button
              size="small"
              icon={<ScanOutlined />}
              loading={hub.rescanModule.isPending}
              onClick={() =>
                hub.rescanModule.mutate(record.module_id, {
                  onSuccess: (res) =>
                    messageApi.success(
                      `Scanned ${res.module_id}: score ${Math.round(res.scan_score)}/100 (${res.verdict})`,
                    ),
                  onError: (err) => messageApi.error(getErrorMessage(err)),
                })
              }
            />
          </Tooltip>
          <Button
            size="small"
            icon={<UploadOutlined />}
            onClick={() => {
              setUpgradeModal({ open: true, moduleId: record.module_id });
              setUpgradePath("");
            }}
          >
            Upgrade
          </Button>
          <Popconfirm
            title={`Uninstall ${record.module_id}?`}
            description="The module will be removed from the registry."
            onConfirm={() => handleUninstall(record.module_id)}
            okText="Uninstall"
            okButtonProps={{ danger: true }}
          >
            <Button
              size="small"
              danger
              icon={<DeleteOutlined />}
              loading={hub.uninstallModule.isPending}
            >
              Uninstall
            </Button>
          </Popconfirm>
        </Space>
      ),
    },
  ];

  if (hub.installed.isLoading) {
    return (
      <div style={{ textAlign: "center", padding: 80 }}>
        <Spin size="large" />
      </div>
    );
  }

  return (
    <>
      {contextHolder}
      <Space direction="vertical" size="large" style={{ width: "100%" }}>
        <PageHeader
          icon={<ShopOutlined />}
          title="Module Hub"
          subtitle="Install, manage, and monitor community modules"
          tags={<Tag color="blue">{stats.total} installed</Tag>}
          extra={
            <Button
              icon={<ReloadOutlined />}
              onClick={() => hub.invalidate()}
            >
              Refresh
            </Button>
          }
        />

        {/* Stats */}
        <Row gutter={[16, 16]}>
          <Col xs={24} sm={8}>
            <StatCard
              title="Installed Modules"
              value={stats.total}
              prefix={<AppstoreOutlined />}
              color="#1677ff"
              footer={
                <Text type="secondary" style={{ fontSize: 12 }}>
                  Community modules
                </Text>
              }
            />
          </Col>
          <Col xs={24} sm={8}>
            <StatCard
              title="Enabled"
              value={stats.enabled}
              prefix={<CheckCircleOutlined />}
              color="#52c41a"
              footer={
                <Text type="secondary" style={{ fontSize: 12 }}>
                  Active and running
                </Text>
              }
            />
          </Col>
          <Col xs={24} sm={8}>
            <StatCard
              title="Disabled"
              value={stats.disabled}
              prefix={<PoweroffOutlined />}
              color="#faad14"
              footer={
                <Text type="secondary" style={{ fontSize: 12 }}>
                  Installed but not active
                </Text>
              }
            />
          </Col>
        </Row>

        {/* Install Tabs: Local Install + Hub Remote */}
        <Card>
          <Tabs
            defaultActiveKey="local"
            items={[
              {
                key: "local",
                label: (
                  <Space>
                    <HddOutlined />
                    Local Install
                  </Space>
                ),
                children: <LocalInstallTab hub={hub} />,
              },
              {
                key: "hub",
                label: (
                  <Space>
                    <CloudDownloadOutlined />
                    Hub Remote
                  </Space>
                ),
                children: <HubSearchTab />,
              },
            ]}
          />
        </Card>

        {/* Installed Community Modules */}
        <Card
          title={
            <Space>
              <AppstoreOutlined />
              <span>Installed Community Modules</span>
              <Tag color="blue">{modules.length}</Tag>
            </Space>
          }
        >
          {modules.length === 0 ? (
            <EmptyState description="No community modules installed yet. Use the install tabs above to add modules." />
          ) : (
            <Table
              dataSource={modules}
              columns={columns}
              rowKey="module_id"
              pagination={false}
              size="middle"
            />
          )}
        </Card>
      </Space>

      {/* Upgrade Modal */}
      <Modal
        title={`Upgrade ${upgradeModal.moduleId}`}
        open={upgradeModal.open}
        onCancel={() => setUpgradeModal({ open: false, moduleId: "" })}
        onOk={handleUpgrade}
        okText="Upgrade"
        okButtonProps={{
          loading: hub.upgradeModule.isPending,
          disabled: !upgradePath.trim(),
        }}
      >
        <Space direction="vertical" size="middle" style={{ width: "100%" }}>
          <Text type="secondary">
            Provide the path to the directory containing the new version.
          </Text>
          <Space.Compact style={{ width: "100%" }}>
            <Input
              placeholder="Path to new version directory"
              prefix={<FolderOpenOutlined style={{ color: "#bfbfbf" }} />}
              value={upgradePath}
              onChange={(e) => setUpgradePath(e.target.value)}
              onPressEnter={handleUpgrade}
            />
            <Button
              icon={<FolderOpenOutlined />}
              onClick={() => setUpgradeFolderOpen(true)}
            >
              Browse
            </Button>
          </Space.Compact>
          {hub.upgradeModule.isSuccess && hub.upgradeModule.data && (
            <Alert
              type="success"
              showIcon
              message={`Upgraded to v${hub.upgradeModule.data.version}`}
            />
          )}
          {hub.upgradeModule.isError && (
            <Alert
              type="error"
              showIcon
              message="Upgrade failed"
              description={getErrorMessage(hub.upgradeModule.error)}
            />
          )}
        </Space>
      </Modal>

      {/* Folder picker for upgrade */}
      <FolderPicker
        open={upgradeFolderOpen}
        onCancel={() => setUpgradeFolderOpen(false)}
        onSelect={(path) => {
          setUpgradePath(path);
          setUpgradeFolderOpen(false);
        }}
      />
    </>
  );
}
