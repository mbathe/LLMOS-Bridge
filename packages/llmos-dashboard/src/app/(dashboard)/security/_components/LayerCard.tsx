"use client";

import React from "react";
import { Card, Tag, Space, Typography, Tooltip } from "antd";
import {
  CheckCircleOutlined,
  CloseCircleOutlined,
} from "@ant-design/icons";
import type { SecurityLayer } from "@/types/security";

const { Text } = Typography;

const layerColors: Record<string, string> = {
  scanner_pipeline: "#1677ff",
  intent_verifier: "#722ed1",
  permission_system: "#fa8c16",
  output_sanitizer: "#52c41a",
};

interface LayerCardProps {
  layer: SecurityLayer;
  onClick?: () => void;
  icon?: React.ReactNode;
  statLabel?: string;
  statValue?: string | number;
}

export function LayerCard({
  layer,
  onClick,
  icon,
  statLabel,
  statValue,
}: LayerCardProps) {
  const color = layerColors[layer.id] ?? "#8c8c8c";

  return (
    <Card
      hoverable={!!onClick}
      onClick={onClick}
      size="small"
      style={{
        borderLeft: `4px solid ${color}`,
        borderRadius: 8,
        cursor: onClick ? "pointer" : "default",
      }}
    >
      <Space direction="vertical" size={4} style={{ width: "100%" }}>
        <Space style={{ width: "100%", justifyContent: "space-between" }}>
          <Space size={8}>
            {icon}
            <Text strong style={{ fontSize: 14 }}>{layer.name}</Text>
          </Space>
          <Tag
            color={layer.enabled ? "green" : "default"}
            icon={
              layer.enabled ? (
                <CheckCircleOutlined />
              ) : (
                <CloseCircleOutlined />
              )
            }
            style={{ borderRadius: 4 }}
          >
            {layer.enabled ? "Active" : "Inactive"}
          </Tag>
        </Space>
        <Tooltip title={layer.description}>
          <Text type="secondary" style={{ fontSize: 12 }} ellipsis>
            {layer.description}
          </Text>
        </Tooltip>
        {statLabel && statValue !== undefined && (
          <Text style={{ fontSize: 12, color }}>
            {statLabel}: <Text strong style={{ color }}>{statValue}</Text>
          </Text>
        )}
      </Space>
    </Card>
  );
}
