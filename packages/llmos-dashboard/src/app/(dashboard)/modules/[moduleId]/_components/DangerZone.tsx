"use client";

import React, { useState } from "react";
import { Card, Button, Space, Typography, Row, Col, Tag, Popconfirm, message } from "antd";
import {
  WarningOutlined,
  SafetyCertificateOutlined,
  DeleteOutlined,
  CheckCircleOutlined,
  CloseCircleOutlined,
} from "@ant-design/icons";
import { useRouter } from "next/navigation";
import type { UseModuleDetailReturn } from "@/hooks/useModuleDetail";
import type { VerifyResponse } from "@/types/module";

const { Text } = Typography;

interface DangerZoneProps {
  hook: UseModuleDetailReturn;
  moduleId: string;
}

export function DangerZone({ hook, moduleId }: DangerZoneProps) {
  const router = useRouter();
  const [verifyResult, setVerifyResult] = useState<VerifyResponse | null>(null);
  const [messageApi, contextHolder] = message.useMessage();

  const isSystemModule =
    hook.info.data?.type === "system" ||
    hook.manifest.data?.module_type === "system";

  const handleVerify = async () => {
    try {
      const result = await hook.verifyModule.mutateAsync();
      setVerifyResult(result);
      if (result.verified) {
        messageApi.success("Module integrity verified successfully.");
      } else {
        messageApi.warning(result.error ?? "Module verification failed.");
      }
    } catch {
      messageApi.error("Failed to verify module integrity.");
    }
  };

  const handleUninstall = async () => {
    try {
      await hook.uninstallModule.mutateAsync();
      messageApi.success(`Module "${moduleId}" has been uninstalled.`);
      router.push("/modules");
    } catch {
      messageApi.error("Failed to uninstall module.");
    }
  };

  if (isSystemModule) {
    return (
      <>
        {contextHolder}
        <Card
          style={{ marginTop: 24, opacity: 0.7 }}
          styles={{ body: { padding: "16px 24px" } }}
        >
          <Space>
            <SafetyCertificateOutlined style={{ color: "var(--ant-color-text-secondary)" }} />
            <Text type="secondary">
              System modules cannot be uninstalled or removed.
            </Text>
          </Space>
        </Card>
      </>
    );
  }

  return (
    <>
      {contextHolder}
      <Card
        style={{ borderColor: "#ff4d4f", marginTop: 24 }}
        title={
          <Space>
            <WarningOutlined style={{ color: "#ff4d4f" }} />
            <Text strong>Danger Zone</Text>
          </Space>
        }
      >
        <Row gutter={[32, 24]}>
          {/* Verify Integrity */}
          <Col xs={24} md={12}>
            <Space direction="vertical" size="middle">
              <div>
                <Text strong>Verify Integrity</Text>
                <br />
                <Text type="secondary">
                  Check module signature and file integrity.
                </Text>
              </div>
              <Space direction="vertical" size="small">
                <Button
                  icon={<SafetyCertificateOutlined />}
                  loading={hook.verifyModule.isPending}
                  onClick={handleVerify}
                >
                  Verify Integrity
                </Button>
                {verifyResult && (
                  <div>
                    {verifyResult.verified ? (
                      <Tag icon={<CheckCircleOutlined />} color="success">
                        Verified
                      </Tag>
                    ) : (
                      <Tag icon={<CloseCircleOutlined />} color="error">
                        {verifyResult.error ?? "Verification failed"}
                      </Tag>
                    )}
                  </div>
                )}
              </Space>
            </Space>
          </Col>

          {/* Uninstall Module */}
          <Col xs={24} md={12}>
            <Space direction="vertical" size="middle">
              <div>
                <Text strong>Uninstall Module</Text>
                <br />
                <Text type="secondary">
                  Permanently remove this module. This cannot be undone.
                </Text>
              </div>
              <Popconfirm
                title="Uninstall this module?"
                description="Are you sure? This action cannot be undone."
                onConfirm={handleUninstall}
                okText="Uninstall"
                okButtonProps={{ danger: true }}
                cancelText="Cancel"
              >
                <Button
                  danger
                  type="primary"
                  icon={<DeleteOutlined />}
                  loading={hook.uninstallModule.isPending}
                >
                  Uninstall Module
                </Button>
              </Popconfirm>
            </Space>
          </Col>
        </Row>
      </Card>
    </>
  );
}
