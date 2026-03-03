"use client";

import React from "react";
import { usePathname, useRouter } from "next/navigation";
import { Layout, Menu, Typography, Space } from "antd";
import {
  DashboardOutlined,
  FileTextOutlined,
  AppstoreOutlined,
  ShopOutlined,
  ThunderboltOutlined,
  VideoCameraOutlined,
  SafetyOutlined,
  SettingOutlined,
  MonitorOutlined,
  KeyOutlined,
  ScanOutlined,
  AuditOutlined,
  ApiOutlined,
  ClusterOutlined,
  TeamOutlined,
  HistoryOutlined,
} from "@ant-design/icons";
import { useUIStore } from "@/stores/ui";
import type { MenuProps } from "antd";

const { Sider } = Layout;
const { Text } = Typography;

type MenuItem = Required<MenuProps>["items"][number];

const menuItems: MenuItem[] = [
  {
    key: "/overview",
    icon: <DashboardOutlined />,
    label: "Overview",
  },
  { type: "divider" },
  {
    key: "/plans",
    icon: <FileTextOutlined />,
    label: "Plans",
  },
  {
    key: "/modules",
    icon: <AppstoreOutlined />,
    label: "Modules",
  },
  {
    key: "/hub",
    icon: <ShopOutlined />,
    label: "Module Hub",
  },
  {
    key: "/applications",
    icon: <TeamOutlined />,
    label: "Applications",
  },
  { type: "divider" },
  {
    key: "/triggers",
    icon: <ThunderboltOutlined />,
    label: "Triggers",
  },
  {
    key: "/recordings",
    icon: <VideoCameraOutlined />,
    label: "Recordings",
  },
  {
    key: "/cluster",
    icon: <ClusterOutlined />,
    label: "Cluster",
  },
  {
    key: "/events",
    icon: <HistoryOutlined />,
    label: "Events",
  },
  { type: "divider" },
  {
    key: "security-group",
    icon: <SafetyOutlined />,
    label: "Security",
    children: [
      { key: "/security", icon: <SafetyOutlined />, label: "Overview" },
      { key: "/security/permissions", icon: <KeyOutlined />, label: "Permissions" },
      { key: "/security/scanners", icon: <ScanOutlined />, label: "Scanners" },
      { key: "/security/audit", icon: <AuditOutlined />, label: "Audit Log" },
    ],
  },
  {
    key: "system-group",
    icon: <SettingOutlined />,
    label: "System",
    children: [
      { key: "/system", icon: <SettingOutlined />, label: "Status" },
      { key: "/system/config", icon: <SettingOutlined />, label: "Configuration" },
      { key: "/monitoring", icon: <MonitorOutlined />, label: "Monitoring" },
    ],
  },
];

export function AppSidebar() {
  const pathname = usePathname();
  const router = useRouter();
  const collapsed = useUIStore((s) => s.sidebarCollapsed);
  const setSidebarCollapsed = useUIStore((s) => s.setSidebarCollapsed);

  const selectedKeys = [pathname];
  const openKeys = menuItems
    .filter(
      (item) =>
        item &&
        "children" in item &&
        item.children?.some((child) => child && "key" in child && pathname.startsWith(child.key as string)),
    )
    .map((item) => item!.key as string);

  return (
    <Sider
      collapsible
      collapsed={collapsed}
      onCollapse={setSidebarCollapsed}
      style={{
        overflow: "auto",
        height: "100vh",
        position: "fixed",
        left: 0,
        top: 0,
        bottom: 0,
        zIndex: 100,
      }}
      width={230}
    >
      {/* Logo / Branding */}
      <div
        style={{
          padding: collapsed ? "20px 8px" : "20px 20px",
          borderBottom: "1px solid rgba(255,255,255,0.08)",
          marginBottom: 8,
          display: "flex",
          alignItems: "center",
          justifyContent: collapsed ? "center" : "flex-start",
          gap: 10,
          transition: "all 0.2s",
        }}
      >
        <div
          style={{
            width: 36,
            height: 36,
            borderRadius: 8,
            background: "linear-gradient(135deg, #1677ff 0%, #4096ff 100%)",
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            flexShrink: 0,
          }}
        >
          <ApiOutlined style={{ color: "#fff", fontSize: 18 }} />
        </div>
        {!collapsed && (
          <div style={{ overflow: "hidden" }}>
            <Text
              strong
              style={{
                color: "rgba(255,255,255,0.95)",
                fontSize: 15,
                display: "block",
                lineHeight: 1.2,
                whiteSpace: "nowrap",
              }}
            >
              LLMOS Bridge
            </Text>
            <Text
              style={{
                color: "rgba(255,255,255,0.45)",
                fontSize: 11,
                display: "block",
                whiteSpace: "nowrap",
              }}
            >
              Dashboard
            </Text>
          </div>
        )}
      </div>

      <Menu
        theme="dark"
        mode="inline"
        selectedKeys={selectedKeys}
        defaultOpenKeys={openKeys}
        items={menuItems}
        onClick={({ key }) => {
          if (!key.endsWith("-group")) {
            router.push(key);
          }
        }}
        style={{ borderRight: 0 }}
      />
    </Sider>
  );
}
