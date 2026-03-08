"use client";

import React, { useState, useCallback } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import {
  Typography,
  Space,
  Button,
  Spin,
  Alert,
  Collapse,
  Tag,
  Input,
  Switch,
  InputNumber,
  Form,
  Modal,
  message,
  Tooltip,
} from "antd";
import {
  ArrowLeftOutlined,
  ReloadOutlined,
  SettingOutlined,
  SafetyCertificateOutlined,
  DatabaseOutlined,
  ApiOutlined,
  AppstoreOutlined,
  CloudServerOutlined,
  EyeOutlined,
  ThunderboltOutlined,
  KeyOutlined,
  SaveOutlined,
  PoweroffOutlined,
  EditOutlined,
  CheckOutlined,
  CloseOutlined,
} from "@ant-design/icons";
import { useRouter } from "next/navigation";
import { api, ApiError } from "@/lib/api/client";
import { PageHeader } from "@/components/common/PageHeader";
import { JsonViewer } from "@/components/common/JsonViewer";
import { EmptyState } from "@/components/common/EmptyState";
import type { SystemConfig } from "@/types/events";

const { Text } = Typography;

function getErrorMessage(error: unknown): string {
  if (error instanceof ApiError) {
    return error.detail ?? error.message ?? "Unknown error";
  }
  if (error instanceof Error) {
    return error.message;
  }
  return "Unknown error";
}

const sectionIconMap: Record<string, React.ReactNode> = {
  security: <SafetyCertificateOutlined style={{ color: "#ff4d4f" }} />,
  security_advanced: <KeyOutlined style={{ color: "#ff4d4f" }} />,
  database: <DatabaseOutlined style={{ color: "#1677ff" }} />,
  api: <ApiOutlined style={{ color: "#52c41a" }} />,
  modules: <AppstoreOutlined style={{ color: "#722ed1" }} />,
  module_manager: <AppstoreOutlined style={{ color: "#722ed1" }} />,
  server: <CloudServerOutlined style={{ color: "#fa8c16" }} />,
  perception: <EyeOutlined style={{ color: "#13c2c2" }} />,
  scanner_pipeline: <ThunderboltOutlined style={{ color: "#eb2f96" }} />,
  triggers: <ThunderboltOutlined style={{ color: "#fa8c16" }} />,
  recording: <DatabaseOutlined style={{ color: "#52c41a" }} />,
  identity: <KeyOutlined style={{ color: "#1677ff" }} />,
  node: <CloudServerOutlined style={{ color: "#fa8c16" }} />,
  isolation: <SafetyCertificateOutlined style={{ color: "#13c2c2" }} />,
  hub: <AppstoreOutlined style={{ color: "#722ed1" }} />,
};

const sectionColorMap: Record<string, string> = {
  security: "red",
  security_advanced: "red",
  database: "blue",
  api: "green",
  modules: "purple",
  module_manager: "purple",
  server: "orange",
  perception: "cyan",
  scanner_pipeline: "magenta",
  triggers: "orange",
  recording: "green",
  identity: "blue",
  node: "orange",
  isolation: "cyan",
  hub: "purple",
};

// Render an editable form for a config section
function SectionEditor({
  sectionName,
  data,
  onChange,
}: {
  sectionName: string;
  data: Record<string, unknown>;
  onChange: (section: string, key: string, value: unknown) => void;
}) {
  return (
    <div style={{ padding: "8px 0" }}>
      {Object.entries(data).map(([key, value]) => {
        const fieldKey = `${sectionName}.${key}`;

        if (typeof value === "boolean") {
          return (
            <Form.Item
              key={fieldKey}
              label={<Text style={{ fontFamily: "monospace", fontSize: 13 }}>{key}</Text>}
              style={{ marginBottom: 12 }}
            >
              <Switch
                checked={value}
                onChange={(checked) => onChange(sectionName, key, checked)}
                checkedChildren="true"
                unCheckedChildren="false"
              />
            </Form.Item>
          );
        }

        if (typeof value === "number") {
          return (
            <Form.Item
              key={fieldKey}
              label={<Text style={{ fontFamily: "monospace", fontSize: 13 }}>{key}</Text>}
              style={{ marginBottom: 12 }}
            >
              <InputNumber
                value={value}
                onChange={(v) => onChange(sectionName, key, v)}
                style={{ width: 200 }}
              />
            </Form.Item>
          );
        }

        if (typeof value === "string") {
          return (
            <Form.Item
              key={fieldKey}
              label={<Text style={{ fontFamily: "monospace", fontSize: 13 }}>{key}</Text>}
              style={{ marginBottom: 12 }}
            >
              <Input
                value={value}
                onChange={(e) => onChange(sectionName, key, e.target.value)}
                style={{ maxWidth: 500, fontFamily: "monospace" }}
              />
            </Form.Item>
          );
        }

        // Complex nested objects/arrays: show as read-only JSON
        if (typeof value === "object" && value !== null) {
          return (
            <Form.Item
              key={fieldKey}
              label={<Text style={{ fontFamily: "monospace", fontSize: 13 }}>{key}</Text>}
              style={{ marginBottom: 12 }}
            >
              <JsonViewer data={value} maxHeight={200} />
            </Form.Item>
          );
        }

        return (
          <Form.Item
            key={fieldKey}
            label={<Text style={{ fontFamily: "monospace", fontSize: 13 }}>{key}</Text>}
            style={{ marginBottom: 12 }}
          >
            <Text type="secondary">{String(value)}</Text>
          </Form.Item>
        );
      })}
    </div>
  );
}

export default function SystemConfigPage() {
  const router = useRouter();
  const queryClient = useQueryClient();
  const [messageApi, contextHolder] = message.useMessage();
  const [editing, setEditing] = useState(false);
  const [editedConfig, setEditedConfig] = useState<Record<string, Record<string, unknown>>>({});
  const [changedSections, setChangedSections] = useState<Set<string>>(new Set());

  const {
    data: config,
    isLoading,
    error,
    refetch,
  } = useQuery<SystemConfig>({
    queryKey: ["system-config"],
    queryFn: () => api.get<SystemConfig>("/admin/system/config"),
    retry: false,
  });

  const saveMutation = useMutation({
    mutationFn: (configUpdate: Record<string, unknown>) =>
      api.put<{ saved: boolean; path: string; restart_required: boolean }>(
        "/admin/system/config",
        { config: configUpdate }
      ),
    onSuccess: (data) => {
      messageApi.success(`Configuration saved to ${data.path}. Restart required to apply changes.`);
      setEditing(false);
      setChangedSections(new Set());
      queryClient.invalidateQueries({ queryKey: ["system-config"] });
    },
    onError: (err) => {
      messageApi.error(`Failed to save: ${getErrorMessage(err)}`);
    },
  });

  const restartMutation = useMutation({
    mutationFn: () => api.post<{ restarting: boolean }>("/admin/system/restart"),
    onSuccess: () => {
      messageApi.success("Daemon is restarting... The page will reconnect automatically.");
    },
    onError: (err) => {
      messageApi.error(`Failed to restart: ${getErrorMessage(err)}`);
    },
  });

  const startEditing = useCallback(() => {
    if (config) {
      // Deep clone current config for editing
      setEditedConfig(JSON.parse(JSON.stringify(config)));
      setChangedSections(new Set());
      setEditing(true);
    }
  }, [config]);

  const cancelEditing = useCallback(() => {
    setEditing(false);
    setEditedConfig({});
    setChangedSections(new Set());
  }, []);

  const handleFieldChange = useCallback(
    (section: string, key: string, value: unknown) => {
      setEditedConfig((prev) => ({
        ...prev,
        [section]: {
          ...prev[section],
          [key]: value,
        },
      }));
      setChangedSections((prev) => new Set(prev).add(section));
    },
    []
  );

  const handleSave = useCallback(() => {
    if (changedSections.size === 0) {
      messageApi.info("No changes to save.");
      return;
    }

    // Only send changed sections
    const updates: Record<string, unknown> = {};
    for (const section of changedSections) {
      updates[section] = editedConfig[section];
    }

    saveMutation.mutate(updates);
  }, [changedSections, editedConfig, saveMutation, messageApi]);

  const handleRestart = useCallback(() => {
    Modal.confirm({
      title: "Restart Daemon",
      content:
        "This will restart the LLMOS Bridge daemon. All active plans will be interrupted. Are you sure?",
      okText: "Restart",
      okType: "danger",
      icon: <PoweroffOutlined style={{ color: "#ff4d4f" }} />,
      onOk: () => restartMutation.mutate(),
    });
  }, [restartMutation]);

  if (isLoading) {
    return (
      <div style={{ textAlign: "center", padding: 80 }}>
        <Spin size="large" />
      </div>
    );
  }

  if (error) {
    return (
      <Alert
        type="error"
        message="Failed to load configuration"
        description={getErrorMessage(error)}
        showIcon
      />
    );
  }

  const displayConfig = editing ? editedConfig : (config ?? {});
  const sections = Object.entries(displayConfig);

  return (
    <Space direction="vertical" size="large" style={{ width: "100%" }}>
      {contextHolder}
      <PageHeader
        icon={<SettingOutlined />}
        title="Configuration"
        subtitle={
          editing
            ? "Edit settings — save and restart daemon to apply"
            : "View and edit daemon configuration settings"
        }
        tags={
          <Space size={4}>
            <Tag color="blue" style={{ borderRadius: 4 }}>
              {sections.length} sections
            </Tag>
            {changedSections.size > 0 && (
              <Tag color="orange" style={{ borderRadius: 4 }}>
                {changedSections.size} modified
              </Tag>
            )}
          </Space>
        }
        extra={
          <Space>
            <Button
              icon={<ArrowLeftOutlined />}
              onClick={() => router.push("/system")}
            >
              Back
            </Button>
            {editing ? (
              <>
                <Button icon={<CloseOutlined />} onClick={cancelEditing}>
                  Cancel
                </Button>
                <Button
                  type="primary"
                  icon={<SaveOutlined />}
                  onClick={handleSave}
                  loading={saveMutation.isPending}
                  disabled={changedSections.size === 0}
                >
                  Save ({changedSections.size})
                </Button>
              </>
            ) : (
              <>
                <Button icon={<ReloadOutlined />} onClick={() => refetch()}>
                  Refresh
                </Button>
                <Button icon={<EditOutlined />} onClick={startEditing}>
                  Edit
                </Button>
              </>
            )}
            <Tooltip title="Restart daemon to apply saved changes">
              <Button
                danger
                icon={<PoweroffOutlined />}
                onClick={handleRestart}
                loading={restartMutation.isPending}
              >
                Restart
              </Button>
            </Tooltip>
          </Space>
        }
      />

      {sections.length === 0 ? (
        <EmptyState description="No configuration data available." />
      ) : (
        <Collapse
          defaultActiveKey={sections.length > 0 ? [sections[0][0]] : []}
          style={{ borderRadius: 8 }}
          items={sections.map(([sectionName, sectionData]) => {
            const sectionObj = sectionData as Record<string, unknown>;
            const keyCount = Object.keys(sectionObj).length;
            const icon =
              sectionIconMap[sectionName] ?? (
                <SettingOutlined style={{ color: "#8c8c8c" }} />
              );
            const tagColor = sectionColorMap[sectionName] ?? "default";
            const isModified = changedSections.has(sectionName);

            return {
              key: sectionName,
              label: (
                <div
                  style={{
                    display: "flex",
                    alignItems: "center",
                    justifyContent: "space-between",
                    width: "100%",
                    paddingRight: 8,
                  }}
                >
                  <Space size="middle">
                    <span style={{ fontSize: 16, display: "flex", alignItems: "center" }}>
                      {icon}
                    </span>
                    <Text strong style={{ fontSize: 14 }}>
                      {sectionName}
                    </Text>
                    {isModified && (
                      <Tag color="orange" style={{ borderRadius: 4, fontSize: 11 }}>
                        modified
                      </Tag>
                    )}
                  </Space>
                  <Space size={8}>
                    <Tag
                      color={tagColor}
                      style={{ borderRadius: 4, fontSize: 11 }}
                    >
                      {keyCount} {keyCount === 1 ? "key" : "keys"}
                    </Tag>
                  </Space>
                </div>
              ),
              children: editing ? (
                <SectionEditor
                  sectionName={sectionName}
                  data={sectionObj}
                  onChange={handleFieldChange}
                />
              ) : (
                <div style={{ padding: "4px 0" }}>
                  <JsonViewer data={sectionData} maxHeight={500} />
                </div>
              ),
              style: {
                marginBottom: 8,
                borderRadius: 8,
                overflow: "hidden",
              },
            };
          })}
        />
      )}
    </Space>
  );
}
