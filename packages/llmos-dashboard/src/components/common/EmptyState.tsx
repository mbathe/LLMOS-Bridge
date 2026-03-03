"use client";

import { Empty, Button } from "antd";

interface EmptyStateProps {
  description?: string;
  actionLabel?: string;
  onAction?: () => void;
}

export function EmptyState({
  description = "No data",
  actionLabel,
  onAction,
}: EmptyStateProps) {
  return (
    <Empty description={description}>
      {actionLabel && onAction && (
        <Button type="primary" onClick={onAction}>
          {actionLabel}
        </Button>
      )}
    </Empty>
  );
}
