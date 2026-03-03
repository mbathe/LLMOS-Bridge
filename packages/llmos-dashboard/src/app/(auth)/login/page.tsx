"use client";

import React, { useState } from "react";
import { Card, Form, Input, Button, Alert, Typography, Space, Divider } from "antd";
import { ApiOutlined, KeyOutlined, LinkOutlined, CheckCircleOutlined, SafetyOutlined } from "@ant-design/icons";
import { useAuth } from "@/lib/auth/provider";

const { Title, Text, Paragraph } = Typography;

export default function LoginPage() {
  const { login } = useAuth();
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const onFinish = async (values: { daemonUrl: string; token: string }) => {
    setLoading(true);
    setError(null);

    try {
      const url = values.daemonUrl || "http://localhost:40000";
      const headers: Record<string, string> = {};
      if (values.token) headers["X-LLMOS-Token"] = values.token;

      const res = await fetch(`${url}/health`, { headers });
      if (!res.ok) {
        throw new Error(`HTTP ${res.status}: ${res.statusText}`);
      }

      login(values.token);
    } catch (err) {
      setError(
        err instanceof Error
          ? err.message
          : "Failed to connect to daemon",
      );
    } finally {
      setLoading(false);
    }
  };

  return (
    <div
      style={{
        minHeight: "100vh",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        background: "linear-gradient(135deg, #001529 0%, #002952 50%, #003a70 100%)",
        padding: 24,
      }}
    >
      {/* Background decorative elements */}
      <div
        style={{
          position: "fixed",
          top: 0,
          left: 0,
          right: 0,
          bottom: 0,
          background: "radial-gradient(circle at 20% 80%, rgba(22, 119, 255, 0.08) 0%, transparent 50%), radial-gradient(circle at 80% 20%, rgba(22, 119, 255, 0.06) 0%, transparent 50%)",
          pointerEvents: "none",
        }}
      />

      <div style={{ position: "relative", zIndex: 1, width: "100%", maxWidth: 460 }}>
        {/* Logo area */}
        <div style={{ textAlign: "center", marginBottom: 32 }}>
          <div
            style={{
              width: 64,
              height: 64,
              borderRadius: 16,
              background: "linear-gradient(135deg, #1677ff 0%, #4096ff 100%)",
              display: "inline-flex",
              alignItems: "center",
              justifyContent: "center",
              marginBottom: 16,
              boxShadow: "0 8px 24px rgba(22, 119, 255, 0.3)",
            }}
          >
            <ApiOutlined style={{ color: "#fff", fontSize: 32 }} />
          </div>
          <Title level={2} style={{ color: "rgba(255,255,255,0.95)", margin: 0, fontWeight: 700 }}>
            LLMOS Bridge
          </Title>
          <Text style={{ color: "rgba(255,255,255,0.55)", fontSize: 14 }}>
            AI-Powered OS Control Dashboard
          </Text>
        </div>

        <Card
          style={{
            borderRadius: 16,
            boxShadow: "0 12px 40px rgba(0, 0, 0, 0.25)",
            border: "1px solid rgba(255,255,255,0.08)",
          }}
          styles={{ body: { padding: "32px 28px" } }}
        >
          <Space direction="vertical" size="large" style={{ width: "100%" }}>
            {error && (
              <Alert
                type="error"
                message="Connection Failed"
                description={error}
                showIcon
                closable
                onClose={() => setError(null)}
              />
            )}

            <Form
              layout="vertical"
              onFinish={onFinish}
              initialValues={{
                daemonUrl:
                  process.env.NEXT_PUBLIC_DAEMON_URL ?? "http://localhost:40000",
                token: "",
              }}
              requiredMark={false}
            >
              <Form.Item
                label={<Text strong>Daemon URL</Text>}
                name="daemonUrl"
                extra={<Text type="secondary" style={{ fontSize: 12 }}>The address of your LLMOS Bridge daemon</Text>}
              >
                <Input
                  prefix={<LinkOutlined style={{ color: "#8c8c8c" }} />}
                  placeholder="http://localhost:40000"
                  size="large"
                  style={{ borderRadius: 8 }}
                />
              </Form.Item>

              <Form.Item
                label={<Text strong>API Token</Text>}
                name="token"
                extra={<Text type="secondary" style={{ fontSize: 12 }}>Leave empty if no token is configured</Text>}
              >
                <Input.Password
                  prefix={<KeyOutlined style={{ color: "#8c8c8c" }} />}
                  placeholder="Optional security token"
                  size="large"
                  style={{ borderRadius: 8 }}
                />
              </Form.Item>

              <Form.Item style={{ marginBottom: 0, marginTop: 8 }}>
                <Button
                  type="primary"
                  htmlType="submit"
                  loading={loading}
                  block
                  size="large"
                  style={{ height: 44, borderRadius: 8, fontWeight: 600, fontSize: 15 }}
                >
                  {loading ? "Connecting..." : "Connect to Daemon"}
                </Button>
              </Form.Item>
            </Form>

            <Divider style={{ margin: "4px 0" }} />

            {/* Features preview */}
            <Space direction="vertical" size={8} style={{ width: "100%" }}>
              {[
                { icon: <CheckCircleOutlined style={{ color: "#52c41a" }} />, text: "Real-time module monitoring" },
                { icon: <SafetyOutlined style={{ color: "#1677ff" }} />, text: "Security scanner pipeline" },
                { icon: <ApiOutlined style={{ color: "#722ed1" }} />, text: "IML plan execution & approval" },
              ].map((item, i) => (
                <Space key={i} size="small">
                  {item.icon}
                  <Text type="secondary" style={{ fontSize: 13 }}>{item.text}</Text>
                </Space>
              ))}
            </Space>
          </Space>
        </Card>

        <div style={{ textAlign: "center", marginTop: 24 }}>
          <Text style={{ color: "rgba(255,255,255,0.35)", fontSize: 12 }}>
            LLMOS Bridge Dashboard v0.1.0 — Protocol 2.0
          </Text>
        </div>
      </div>
    </div>
  );
}
