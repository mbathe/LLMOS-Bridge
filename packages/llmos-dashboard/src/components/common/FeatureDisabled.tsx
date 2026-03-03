"use client";

import React from "react";
import { Card, Typography, Button, Space } from "antd";
import { StopOutlined, SettingOutlined } from "@ant-design/icons";
import { useRouter } from "next/navigation";

const { Title, Text, Paragraph } = Typography;

interface FeatureDisabledProps {
  feature: string;
  description: string;
  configHint?: string;
  icon?: React.ReactNode;
}

export function FeatureDisabled({ feature, description, configHint, icon }: FeatureDisabledProps) {
  const router = useRouter();

  return (
    <div
      style={{
        display: "flex",
        justifyContent: "center",
        alignItems: "center",
        minHeight: 400,
      }}
    >
      <Card
        style={{
          maxWidth: 520,
          textAlign: "center",
          borderRadius: 12,
          border: "1px dashed var(--ant-color-border)",
        }}
        styles={{ body: { padding: "40px 32px" } }}
      >
        <Space direction="vertical" size="large" style={{ width: "100%" }}>
          <div
            style={{
              width: 80,
              height: 80,
              borderRadius: "50%",
              background: "var(--ant-color-bg-layout)",
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
              margin: "0 auto",
              fontSize: 36,
              color: "var(--ant-color-text-quaternary)",
            }}
          >
            {icon ?? <StopOutlined />}
          </div>
          <div>
            <Title level={4} style={{ marginBottom: 8 }}>
              {feature} is not enabled
            </Title>
            <Paragraph type="secondary" style={{ marginBottom: 0, fontSize: 14 }}>
              {description}
            </Paragraph>
          </div>
          {configHint && (
            <Card
              size="small"
              style={{
                background: "var(--ant-color-bg-layout)",
                borderColor: "var(--ant-color-border)",
                textAlign: "left",
              }}
            >
              <Text code style={{ fontSize: 12 }}>
                {configHint}
              </Text>
            </Card>
          )}
          <Button
            icon={<SettingOutlined />}
            onClick={() => router.push("/system/config")}
          >
            View Configuration
          </Button>
        </Space>
      </Card>
    </div>
  );
}
