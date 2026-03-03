"use client";

import React from "react";
import { ConfigProvider } from "antd";
import { useUIStore } from "@/stores/ui";
import { lightTheme, darkTheme } from "./tokens";

export function ThemeProvider({ children }: { children: React.ReactNode }) {
  const themeMode = useUIStore((s) => s.theme);

  return (
    <ConfigProvider theme={themeMode === "dark" ? darkTheme : lightTheme}>
      {children}
    </ConfigProvider>
  );
}
