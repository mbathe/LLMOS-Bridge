"use client";

import React from "react";
import { Layout } from "antd";
import { AppSidebar } from "@/components/layout/AppSidebar";
import { AppHeader } from "@/components/layout/AppHeader";
import { BreadcrumbNav } from "@/components/layout/BreadcrumbNav";
import { AuthGuard } from "@/lib/auth/guard";
import { useWebSocket } from "@/hooks/useWebSocket";
import { useUIStore } from "@/stores/ui";

const { Content } = Layout;

export default function DashboardLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  useWebSocket();
  const collapsed = useUIStore((s) => s.sidebarCollapsed);

  return (
    <AuthGuard>
      <Layout style={{ minHeight: "100vh" }}>
        <AppSidebar />
        <Layout style={{ marginLeft: collapsed ? 80 : 200, transition: "margin-left 0.2s" }}>
          <AppHeader />
          <Content style={{ padding: 24, minHeight: 360 }}>
            <BreadcrumbNav />
            {children}
          </Content>
        </Layout>
      </Layout>
    </AuthGuard>
  );
}
