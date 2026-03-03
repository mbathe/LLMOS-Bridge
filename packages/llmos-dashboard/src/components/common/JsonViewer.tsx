"use client";

import { Typography } from "antd";

interface JsonViewerProps {
  data: unknown;
  maxHeight?: number;
}

export function JsonViewer({ data, maxHeight = 400 }: JsonViewerProps) {
  const formatted = JSON.stringify(data, null, 2);

  return (
    <pre
      style={{
        maxHeight,
        overflow: "auto",
        padding: 12,
        borderRadius: 6,
        fontSize: 12,
        lineHeight: 1.6,
        background: "var(--ant-color-bg-container)",
        border: "1px solid var(--ant-color-border)",
      }}
    >
      <Typography.Text code style={{ whiteSpace: "pre-wrap" }}>
        {formatted}
      </Typography.Text>
    </pre>
  );
}
