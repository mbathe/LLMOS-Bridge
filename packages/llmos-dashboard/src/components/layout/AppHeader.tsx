"use client";

import React from "react";
import { Layout, Space, Button, Badge, Tooltip, Select, Tag, Typography, Divider } from "antd";
import {
  BulbOutlined,
  BulbFilled,
  LogoutOutlined,
  BellOutlined,
  WifiOutlined,
  DisconnectOutlined,
  GlobalOutlined,
  UserOutlined,
} from "@ant-design/icons";
import { useAuth } from "@/lib/auth/provider";
import { useUIStore } from "@/stores/ui";
import { useWSEventStore } from "@/stores/ws-events";
import { useNotificationStore } from "@/stores/notifications";

const { Header } = Layout;
const { Text } = Typography;

export function AppHeader() {
  const { logout } = useAuth();
  const themeMode = useUIStore((s) => s.theme);
  const toggleTheme = useUIStore((s) => s.toggleTheme);
  const locale = useUIStore((s) => s.locale);
  const setLocale = useUIStore((s) => s.setLocale);
  const connected = useWSEventStore((s) => s.connected);
  const eventCount = useWSEventStore((s) => s.events.length);
  const approvalCount = useNotificationStore((s) => s.pendingApprovals.length);

  return (
    <Header
      style={{
        padding: "0 24px",
        display: "flex",
        alignItems: "center",
        justifyContent: "space-between",
        gap: 12,
        background: "var(--ant-color-bg-container)",
        borderBottom: "1px solid var(--ant-color-border)",
        height: 56,
        lineHeight: "56px",
      }}
    >
      {/* Left side - Connection status */}
      <Space size="middle">
        <Tooltip title={connected ? `WebSocket connected — ${eventCount} events received` : "WebSocket disconnected"}>
          <Tag
            icon={connected ? <WifiOutlined /> : <DisconnectOutlined />}
            color={connected ? "success" : "error"}
            style={{ margin: 0, borderRadius: 6, padding: "2px 10px" }}
          >
            {connected ? "Live" : "Offline"}
          </Tag>
        </Tooltip>
      </Space>

      {/* Right side - Controls */}
      <Space size={4} split={<Divider type="vertical" style={{ margin: "0 4px" }} />}>
        <Badge count={approvalCount} size="small" offset={[-4, 4]}>
          <Button type="text" icon={<BellOutlined style={{ fontSize: 16 }} />} size="small" />
        </Badge>

        <Select
          value={locale}
          onChange={(v) => setLocale(v as "en" | "fr")}
          size="small"
          style={{ width: 72 }}
          suffixIcon={<GlobalOutlined />}
          variant="borderless"
          options={[
            { label: "EN", value: "en" },
            { label: "FR", value: "fr" },
          ]}
        />

        <Tooltip title={themeMode === "light" ? "Dark Mode" : "Light Mode"}>
          <Button
            type="text"
            size="small"
            icon={themeMode === "light" ? <BulbOutlined style={{ fontSize: 16 }} /> : <BulbFilled style={{ fontSize: 16 }} />}
            onClick={toggleTheme}
          />
        </Tooltip>

        <Tooltip title="Logout">
          <Button type="text" size="small" icon={<LogoutOutlined style={{ fontSize: 16 }} />} onClick={logout} danger />
        </Tooltip>
      </Space>
    </Header>
  );
}
