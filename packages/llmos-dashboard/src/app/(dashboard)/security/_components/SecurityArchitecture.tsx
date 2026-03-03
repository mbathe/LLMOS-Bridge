"use client";

import React from "react";
import { Card, Row, Col, Space, Typography, Divider } from "antd";
import {
  ScanOutlined,
  EyeOutlined,
  KeyOutlined,
  FilterOutlined,
  ArrowRightOutlined,
  SafetyCertificateOutlined,
} from "@ant-design/icons";
import { useRouter } from "next/navigation";
import { LayerCard } from "./LayerCard";
import type { SecurityLayer } from "@/types/security";

const { Text } = Typography;

const layerIcons: Record<string, React.ReactNode> = {
  scanner_pipeline: <ScanOutlined style={{ color: "#1677ff" }} />,
  intent_verifier: <EyeOutlined style={{ color: "#722ed1" }} />,
  permission_system: <KeyOutlined style={{ color: "#fa8c16" }} />,
  output_sanitizer: <FilterOutlined style={{ color: "#52c41a" }} />,
};

const layerRoutes: Record<string, string> = {
  scanner_pipeline: "/security/scanners",
  intent_verifier: "/security/intent-verifier",
  permission_system: "/security/permissions",
};

function getStatForLayer(layer: SecurityLayer): { label: string; value: string | number } | null {
  const stats = layer.stats ?? {};
  switch (layer.id) {
    case "scanner_pipeline":
      return {
        label: "Patterns",
        value: `${stats.patterns_enabled ?? 0}/${stats.patterns_total ?? 0}`,
      };
    case "intent_verifier":
      return {
        label: "Categories",
        value: String(stats.threat_categories ?? 0),
      };
    case "permission_system":
      return {
        label: "Permissions",
        value: String(stats.permissions_count ?? 0),
      };
    default:
      return null;
  }
}

interface SecurityArchitectureProps {
  layers: SecurityLayer[];
}

export function SecurityArchitecture({ layers }: SecurityArchitectureProps) {
  const router = useRouter();
  const sorted = [...layers].sort((a, b) => a.order - b.order);

  return (
    <Card
      title={
        <Space>
          <SafetyCertificateOutlined />
          <span>Security Architecture</span>
        </Space>
      }
      extra={
        <Text type="secondary" style={{ fontSize: 12 }}>
          Pipeline: Input &rarr; Output
        </Text>
      }
    >
      {/* Visual flow */}
      <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 16, flexWrap: "wrap" }}>
        {sorted.map((layer, i) => (
          <React.Fragment key={layer.id}>
            <div
              style={{
                padding: "6px 16px",
                borderRadius: 20,
                background: layer.enabled ? "var(--ant-color-primary-bg)" : "var(--ant-color-bg-layout)",
                border: `1px solid ${layer.enabled ? "var(--ant-color-primary-border)" : "var(--ant-color-border)"}`,
                cursor: layerRoutes[layer.id] ? "pointer" : "default",
                opacity: layer.enabled ? 1 : 0.5,
                transition: "all 0.2s",
              }}
              onClick={() => {
                const route = layerRoutes[layer.id];
                if (route) router.push(route);
              }}
            >
              <Space size={6}>
                {layerIcons[layer.id]}
                <Text
                  strong={layer.enabled}
                  type={layer.enabled ? undefined : "secondary"}
                  style={{ fontSize: 13, whiteSpace: "nowrap" }}
                >
                  {layer.name}
                </Text>
              </Space>
            </div>
            {i < sorted.length - 1 && (
              <ArrowRightOutlined style={{ color: "var(--ant-color-text-quaternary)", fontSize: 14 }} />
            )}
          </React.Fragment>
        ))}
      </div>

      <Divider style={{ margin: "12px 0" }} />

      {/* Detail cards */}
      <Row gutter={[12, 12]}>
        {sorted.map((layer) => {
          const stat = getStatForLayer(layer);
          return (
            <Col xs={24} sm={12} lg={6} key={layer.id}>
              <LayerCard
                layer={layer}
                icon={layerIcons[layer.id]}
                onClick={layerRoutes[layer.id] ? () => router.push(layerRoutes[layer.id]) : undefined}
                statLabel={stat?.label}
                statValue={stat?.value}
              />
            </Col>
          );
        })}
      </Row>
    </Card>
  );
}
