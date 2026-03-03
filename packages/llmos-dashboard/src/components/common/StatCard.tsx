"use client";

import React from "react";
import { Card, Statistic, Typography } from "antd";

const { Text } = Typography;

interface StatCardProps {
  title: string;
  value: string | number;
  prefix?: React.ReactNode;
  suffix?: React.ReactNode;
  valueStyle?: React.CSSProperties;
  footer?: React.ReactNode;
  onClick?: () => void;
  color?: string;
}

export function StatCard({ title, value, prefix, suffix, valueStyle, footer, onClick, color }: StatCardProps) {
  return (
    <Card
      hoverable={!!onClick}
      onClick={onClick}
      style={{
        borderRadius: 10,
        borderTop: color ? `3px solid ${color}` : undefined,
        cursor: onClick ? "pointer" : undefined,
      }}
      styles={{ body: { padding: "16px 20px" } }}
    >
      <Statistic
        title={<Text type="secondary" style={{ fontSize: 13 }}>{title}</Text>}
        value={value}
        prefix={prefix}
        suffix={suffix}
        valueStyle={{ fontSize: 24, fontWeight: 600, ...valueStyle }}
      />
      {footer && (
        <div style={{ marginTop: 8, borderTop: "1px solid var(--ant-color-border)", paddingTop: 8 }}>
          {footer}
        </div>
      )}
    </Card>
  );
}
