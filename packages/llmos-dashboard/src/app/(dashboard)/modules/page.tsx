"use client";

import React from "react";
import { useQuery } from "@tanstack/react-query";
import {
  Card,
  Row,
  Col,
  Typography,
  Space,
  Button,
  Tag,
  Spin,
  Tooltip,
  Alert,
  Divider,
  Badge,
} from "antd";
import {
  AppstoreOutlined,
  ReloadOutlined,
  InfoCircleOutlined,
  WarningOutlined,
  CheckCircleOutlined,
  CloseCircleOutlined,
} from "@ant-design/icons";
import { useRouter } from "next/navigation";
import { api } from "@/lib/api/client";
import { useDaemonHealth } from "@/hooks/useDaemonHealth";
import type { ModuleInfo } from "@/types/module";

const { Title, Text, Paragraph } = Typography;

export default function ModulesPage() {
  const router = useRouter();

  // GET /modules returns an array directly
  const { data: modules, isLoading, refetch } = useQuery<ModuleInfo[]>({
    queryKey: ["modules"],
    queryFn: () => api.get<ModuleInfo[]>("/modules"),
  });

  // Also get health to show failed modules
  const { data: health } = useDaemonHealth(30000);
  const failedModules = Object.entries(health?.modules?.failed ?? {});

  if (isLoading) {
    return (
      <div style={{ textAlign: "center", padding: 80 }}>
        <Spin size="large" />
      </div>
    );
  }

  return (
    <div>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 20 }}>
        <div>
          <Title level={3} style={{ margin: 0 }}>
            <AppstoreOutlined style={{ marginRight: 8 }} />
            Modules
          </Title>
          <Text type="secondary">
            {modules?.length ?? 0} loaded
            {failedModules.length > 0 && ` / ${failedModules.length} failed`}
          </Text>
        </div>
        <Button icon={<ReloadOutlined />} onClick={() => refetch()}>
          Refresh
        </Button>
      </div>

      {/* Failed Modules */}
      {failedModules.length > 0 && (
        <Alert
          type="error"
          showIcon
          icon={<WarningOutlined />}
          message={`${failedModules.length} module(s) failed to load`}
          style={{ marginBottom: 16 }}
          description={
            <Space direction="vertical" size="small" style={{ marginTop: 8 }}>
              {failedModules.map(([modId, reason]) => (
                <div key={modId}>
                  <Tag color="error" style={{ fontWeight: 600 }}>{modId}</Tag>
                  <Text type="secondary" style={{ fontSize: 12 }}>{reason}</Text>
                </div>
              ))}
            </Space>
          }
        />
      )}

      {/* Available Modules */}
      <Row gutter={[16, 16]}>
        {(modules ?? []).map((mod) => (
          <Col key={mod.module_id} xs={24} sm={12} lg={8} xl={6}>
            <Card
              hoverable
              onClick={() => router.push(`/modules/${mod.module_id}`)}
              style={{ height: "100%" }}
              styles={{ body: { padding: 16 } }}
            >
              <Space direction="vertical" size="small" style={{ width: "100%" }}>
                <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
                  <Space>
                    <AppstoreOutlined style={{ fontSize: 18, color: "#1677ff" }} />
                    <Text strong style={{ fontSize: 15 }}>{mod.module_id}</Text>
                  </Space>
                  <Badge
                    status={mod.available ? "success" : "error"}
                    text={mod.available ? "Active" : "Failed"}
                  />
                </div>
                <Paragraph
                  ellipsis={{ rows: 2 }}
                  type="secondary"
                  style={{ margin: 0, fontSize: 12, minHeight: 36 }}
                >
                  {mod.description || "No description"}
                </Paragraph>
                <Space size={4}>
                  <Tag color="blue">{mod.action_count} actions</Tag>
                  <Tag>v{mod.version}</Tag>
                </Space>
              </Space>
            </Card>
          </Col>
        ))}
      </Row>
    </div>
  );
}
