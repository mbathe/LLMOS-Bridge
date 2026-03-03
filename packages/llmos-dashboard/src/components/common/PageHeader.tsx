"use client";

import React from "react";
import { Typography, Space, Divider } from "antd";

const { Title, Text } = Typography;

interface PageHeaderProps {
  icon: React.ReactNode;
  title: string;
  subtitle?: string;
  tags?: React.ReactNode;
  extra?: React.ReactNode;
}

export function PageHeader({ icon, title, subtitle, tags, extra }: PageHeaderProps) {
  return (
    <>
      <div
        style={{
          display: "flex",
          justifyContent: "space-between",
          alignItems: "flex-start",
          marginBottom: 24,
          padding: "20px 24px",
          background: "linear-gradient(135deg, var(--ant-color-bg-container) 0%, var(--ant-color-bg-layout) 100%)",
          borderRadius: 12,
          border: "1px solid var(--ant-color-border)",
        }}
      >
        <div>
          <Space align="center" size="middle">
            <span style={{ fontSize: 28, color: "#1677ff", display: "flex", alignItems: "center" }}>
              {icon}
            </span>
            <div>
              <Space align="center" size="small">
                <Title level={3} style={{ margin: 0 }}>
                  {title}
                </Title>
                {tags}
              </Space>
              {subtitle && (
                <Text type="secondary" style={{ display: "block", marginTop: 2, fontSize: 13 }}>
                  {subtitle}
                </Text>
              )}
            </div>
          </Space>
        </div>
        {extra && (
          <Space size="small" style={{ flexShrink: 0 }}>
            {extra}
          </Space>
        )}
      </div>
    </>
  );
}
