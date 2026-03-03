"use client";

import React, { useState } from "react";
import {
  Card,
  Table,
  Tag,
  Space,
  Switch,
  Button,
  Typography,
  Popconfirm,
  Input,
  Row,
  Col,
  List,
  message,
} from "antd";
import {
  PlayCircleOutlined,
  ThunderboltOutlined,
  LockOutlined,
  InfoCircleOutlined,
  WarningOutlined,
} from "@ant-design/icons";
import type { ColumnsType } from "antd/es/table";
import type { UseModuleDetailReturn } from "@/hooks/useModuleDetail";
import type { ActionSpec, RiskLevel, ExecutionMode } from "@/types/module";
import { JsonViewer } from "@/components/common/JsonViewer";
import { ActionTryModal } from "./ActionTryModal";

const { Text } = Typography;

// ── Color maps ──

const riskColorMap: Record<string, string> = {
  low: "green",
  medium: "orange",
  high: "red",
  critical: "volcano",
  "": "default",
};

const modeColorMap: Record<string, string> = {
  sync: "default",
  async: "blue",
  background: "purple",
  scheduled: "cyan",
};

// ── Component ──

interface ActionsTabProps {
  hook: UseModuleDetailReturn;
}

export function ActionsTab({ hook }: ActionsTabProps) {
  const [tryAction, setTryAction] = useState<ActionSpec | null>(null);
  const [disableReason, setDisableReason] = useState("");

  const actions: ActionSpec[] = hook.manifest.data?.actions ?? [];
  const disabledActions: Record<string, string> =
    hook.info.data?.disabled_actions ?? {};
  const moduleId = hook.manifest.data?.module_id ?? "";

  const streamingCount = actions.filter((a) => a.streams_progress).length;
  const permissionCount = new Set(
    actions.map((a) => a.permission_required).filter(Boolean),
  ).size;

  const handleEnable = async (actionName: string) => {
    try {
      await hook.enableAction.mutateAsync(actionName);
      message.success(`Action "${actionName}" enabled`);
    } catch (err) {
      message.error(
        err instanceof Error ? err.message : "Failed to enable action",
      );
    }
  };

  const handleDisable = async (actionName: string, reason: string) => {
    try {
      await hook.disableAction.mutateAsync({
        action: actionName,
        reason: reason || undefined,
      });
      message.success(`Action "${actionName}" disabled`);
      setDisableReason("");
    } catch (err) {
      message.error(
        err instanceof Error ? err.message : "Failed to disable action",
      );
    }
  };

  const columns: ColumnsType<ActionSpec> = [
    {
      title: "Name",
      dataIndex: "name",
      key: "name",
      width: 260,
      render: (name: string, record: ActionSpec) => {
        const isDisabled = name in disabledActions;
        return (
          <div>
            <Text
              strong
              style={{ fontFamily: "monospace", fontSize: 13 }}
            >
              {name}
            </Text>
            {record.description && (
              <div>
                <Text type="secondary" style={{ fontSize: 12 }}>
                  {record.description}
                </Text>
              </div>
            )}
            {isDisabled && (
              <div>
                <Text italic type="warning" style={{ fontSize: 11 }}>
                  <WarningOutlined style={{ marginRight: 4 }} />
                  Disabled: {disabledActions[name] || "No reason provided"}
                </Text>
              </div>
            )}
          </div>
        );
      },
    },
    {
      title: "Status",
      key: "status",
      width: 100,
      align: "center",
      render: (_: unknown, record: ActionSpec) => {
        const isEnabled = !(record.name in disabledActions);
        return isEnabled ? (
          <Popconfirm
            title="Disable this action?"
            description={
              <Input
                placeholder="Reason for disabling (optional)"
                value={disableReason}
                onChange={(e) => setDisableReason(e.target.value)}
                style={{ marginTop: 8 }}
              />
            }
            onConfirm={() => handleDisable(record.name, disableReason)}
            onCancel={() => setDisableReason("")}
            okText="Disable"
            cancelText="Cancel"
            okButtonProps={{ danger: true }}
          >
            <Switch
              checked={true}
              loading={hook.disableAction.isPending}
            />
          </Popconfirm>
        ) : (
          <Switch
            checked={false}
            loading={hook.enableAction.isPending}
            onChange={() => handleEnable(record.name)}
          />
        );
      },
    },
    {
      title: "Risk Level",
      dataIndex: "risk_level",
      key: "risk_level",
      width: 110,
      align: "center",
      render: (risk: RiskLevel | undefined) => {
        const level = risk ?? "";
        return (
          <Tag color={riskColorMap[level] ?? "default"}>
            {level || "none"}
          </Tag>
        );
      },
    },
    {
      title: "Permission",
      dataIndex: "permission_required",
      key: "permission",
      width: 170,
      render: (perm: string | null) =>
        perm ? (
          <Tag color="orange" icon={<LockOutlined />}>
            {perm}
          </Tag>
        ) : (
          <Text type="secondary">&mdash;</Text>
        ),
    },
    {
      title: "Streaming",
      dataIndex: "streams_progress",
      key: "streaming",
      width: 100,
      align: "center",
      render: (streams: boolean | undefined) =>
        streams ? (
          <Tag color="blue" icon={<ThunderboltOutlined />}>
            Streams
          </Tag>
        ) : (
          <Text type="secondary">&mdash;</Text>
        ),
    },
    {
      title: "Mode",
      dataIndex: "execution_mode",
      key: "execution_mode",
      width: 110,
      align: "center",
      render: (mode: ExecutionMode | undefined) => {
        const m = mode ?? "sync";
        return <Tag color={modeColorMap[m] ?? "default"}>{m}</Tag>;
      },
    },
    {
      title: "Actions",
      key: "actions",
      width: 80,
      align: "center",
      render: (_: unknown, record: ActionSpec) => (
        <Button
          type="link"
          icon={<PlayCircleOutlined />}
          onClick={() => setTryAction(record)}
          size="small"
        >
          Try
        </Button>
      ),
    },
  ];

  return (
    <>
      <Card
        title={
          <Space>
            <ThunderboltOutlined />
            <span>Module Actions</span>
            <Tag color="blue">{actions.length}</Tag>
          </Space>
        }
        extra={
          <Space size={8}>
            {streamingCount > 0 && (
              <Tag color="cyan">{streamingCount} streaming</Tag>
            )}
            {permissionCount > 0 && (
              <Tag color="orange">{permissionCount} permissions</Tag>
            )}
          </Space>
        }
      >
        <Table<ActionSpec>
          columns={columns}
          dataSource={actions}
          rowKey="name"
          size="middle"
          bordered
          pagination={false}
          loading={hook.manifest.isLoading}
          expandable={{
            expandedRowRender: (record) => (
              <div style={{ padding: "12px 0" }}>
                <Row gutter={[16, 16]}>
                  {/* Params Schema */}
                  {record.params_schema &&
                    Object.keys(record.params_schema).length > 0 && (
                      <Col span={24}>
                        <Card
                          size="small"
                          title={
                            <Space>
                              <InfoCircleOutlined />
                              <Text strong>Params Schema</Text>
                            </Space>
                          }
                        >
                          <JsonViewer
                            data={record.params_schema}
                            maxHeight={300}
                          />
                        </Card>
                      </Col>
                    )}

                  {/* Side Effects */}
                  {record.side_effects &&
                    record.side_effects.length > 0 && (
                      <Col span={24}>
                        <Card
                          size="small"
                          title={<Text strong>Side Effects</Text>}
                        >
                          <Space wrap>
                            {record.side_effects.map((se) => (
                              <Tag key={se} color="volcano">
                                {se}
                              </Tag>
                            ))}
                          </Space>
                        </Card>
                      </Col>
                    )}

                  {/* Output Schema */}
                  {record.output_schema &&
                    Object.keys(record.output_schema).length > 0 && (
                      <Col span={24}>
                        <Card
                          size="small"
                          title={<Text strong>Output Schema</Text>}
                        >
                          <JsonViewer
                            data={record.output_schema}
                            maxHeight={250}
                          />
                        </Card>
                      </Col>
                    )}

                  {/* Examples */}
                  {record.examples && record.examples.length > 0 && (
                    <Col span={24}>
                      <Card
                        size="small"
                        title={<Text strong>Examples</Text>}
                      >
                        <JsonViewer
                          data={record.examples}
                          maxHeight={200}
                        />
                      </Card>
                    </Col>
                  )}

                  {/* Capabilities */}
                  {record.capabilities &&
                    record.capabilities.length > 0 && (
                      <Col span={24}>
                        <Card
                          size="small"
                          title={<Text strong>Capabilities</Text>}
                        >
                          <List
                            size="small"
                            dataSource={record.capabilities}
                            renderItem={(cap) => (
                              <List.Item>
                                <Space>
                                  <Tag color="geekblue">
                                    {cap.permission}
                                  </Tag>
                                  {cap.scope && (
                                    <Text type="secondary">
                                      scope: {cap.scope}
                                    </Text>
                                  )}
                                  {cap.constraints &&
                                    Object.keys(cap.constraints).length >
                                      0 && (
                                      <Text
                                        type="secondary"
                                        style={{ fontSize: 11 }}
                                      >
                                        constraints:{" "}
                                        {JSON.stringify(cap.constraints)}
                                      </Text>
                                    )}
                                </Space>
                              </List.Item>
                            )}
                          />
                        </Card>
                      </Col>
                    )}
                </Row>
              </div>
            ),
            rowExpandable: (record) => {
              const hasSchema =
                record.params_schema &&
                Object.keys(record.params_schema).length > 0;
              const hasSideEffects =
                record.side_effects && record.side_effects.length > 0;
              const hasOutput =
                record.output_schema &&
                Object.keys(record.output_schema).length > 0;
              const hasExamples =
                record.examples && record.examples.length > 0;
              const hasCaps =
                record.capabilities && record.capabilities.length > 0;
              return !!(
                hasSchema ||
                hasSideEffects ||
                hasOutput ||
                hasExamples ||
                hasCaps
              );
            },
          }}
        />
      </Card>

      <ActionTryModal
        action={tryAction}
        moduleId={moduleId}
        onClose={() => setTryAction(null)}
      />
    </>
  );
}
