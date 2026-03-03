"use client";

import React, { useState, useCallback } from "react";
import { Modal, List, Typography, Space, Button, Breadcrumb, Tag, Spin, Alert } from "antd";
import {
  FolderOutlined,
  FolderOpenOutlined,
  HomeFilled,
  ArrowUpOutlined,
  CheckCircleFilled,
} from "@ant-design/icons";
import { useQuery } from "@tanstack/react-query";
import { api } from "@/lib/api/client";

const { Text } = Typography;

interface BrowseEntry {
  name: string;
  path: string;
  is_module: boolean;
}

interface BrowseResponse {
  current: string;
  parent: string | null;
  is_module: boolean;
  entries: BrowseEntry[];
}

interface FolderPickerProps {
  open: boolean;
  onCancel: () => void;
  onSelect: (path: string) => void;
  initialPath?: string;
}

export function FolderPicker({ open, onCancel, onSelect, initialPath = "~" }: FolderPickerProps) {
  const [currentPath, setCurrentPath] = useState(initialPath);

  const { data, isLoading, error } = useQuery<BrowseResponse>({
    queryKey: ["browse", currentPath],
    queryFn: () => api.get<BrowseResponse>("/admin/modules/browse", { path: currentPath }),
    enabled: open,
  });

  const navigateTo = useCallback((path: string) => {
    setCurrentPath(path);
  }, []);

  const breadcrumbParts = data?.current.split("/").filter(Boolean) ?? [];

  return (
    <Modal
      title={
        <Space>
          <FolderOpenOutlined />
          <span>Select Module Directory</span>
        </Space>
      }
      open={open}
      onCancel={onCancel}
      width={680}
      footer={[
        <Button key="cancel" onClick={onCancel}>
          Cancel
        </Button>,
        <Button
          key="select"
          type="primary"
          disabled={!data?.is_module}
          onClick={() => data && onSelect(data.current)}
        >
          {data?.is_module ? "Select this module" : "No llmos-module.toml here"}
        </Button>,
      ]}
    >
      {/* Current path info */}
      {data?.is_module && (
        <Alert
          type="success"
          showIcon
          icon={<CheckCircleFilled />}
          message="Valid module directory detected"
          description={`llmos-module.toml found in ${data.current}`}
          style={{ marginBottom: 12 }}
        />
      )}

      {/* Breadcrumb navigation */}
      <div style={{ marginBottom: 12, display: "flex", alignItems: "center", gap: 8 }}>
        <Button
          size="small"
          icon={<HomeFilled />}
          onClick={() => navigateTo("~")}
        />
        {data?.parent && (
          <Button
            size="small"
            icon={<ArrowUpOutlined />}
            onClick={() => navigateTo(data.parent!)}
          >
            Up
          </Button>
        )}
        <Breadcrumb
          style={{ flex: 1 }}
          items={[
            { title: "/" },
            ...breadcrumbParts.map((part, i) => ({
              title: (
                <a onClick={() => navigateTo("/" + breadcrumbParts.slice(0, i + 1).join("/"))}>
                  {part}
                </a>
              ),
            })),
          ]}
        />
      </div>

      {/* Directory listing */}
      {isLoading ? (
        <div style={{ textAlign: "center", padding: 40 }}>
          <Spin />
        </div>
      ) : error ? (
        <Alert
          type="error"
          message="Cannot browse this directory"
          description={error instanceof Error ? error.message : "Permission denied"}
        />
      ) : (
        <List
          size="small"
          bordered
          style={{ maxHeight: 400, overflowY: "auto" }}
          dataSource={data?.entries ?? []}
          locale={{ emptyText: "Empty directory" }}
          renderItem={(entry) => (
            <List.Item
              style={{ cursor: "pointer", padding: "8px 16px" }}
              onClick={() => navigateTo(entry.path)}
            >
              <Space style={{ width: "100%" }}>
                <FolderOutlined
                  style={{ color: entry.is_module ? "#52c41a" : "#faad14", fontSize: 16 }}
                />
                <Text strong={entry.is_module}>{entry.name}</Text>
                {entry.is_module && (
                  <Tag color="success" style={{ marginLeft: "auto" }}>
                    module
                  </Tag>
                )}
              </Space>
            </List.Item>
          )}
        />
      )}

      <Text
        type="secondary"
        style={{ display: "block", marginTop: 8, fontSize: 12 }}
      >
        Current: {data?.current ?? currentPath}
      </Text>
    </Modal>
  );
}
