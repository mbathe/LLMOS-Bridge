"use client";

import React from "react";
import { Rate, Space, Typography } from "antd";

const { Text } = Typography;

interface RatingDisplayProps {
  average: number;
  count: number;
  size?: "small" | "default";
}

export function RatingDisplay({ average, count, size = "small" }: RatingDisplayProps) {
  if (count === 0) {
    return <Text type="secondary" style={{ fontSize: size === "small" ? 12 : 14 }}>No ratings</Text>;
  }
  return (
    <Space size={4}>
      <Rate
        disabled
        allowHalf
        value={Math.round(average * 2) / 2}
        style={{ fontSize: size === "small" ? 12 : 16 }}
      />
      <Text type="secondary" style={{ fontSize: size === "small" ? 12 : 14 }}>
        {average.toFixed(1)} ({count})
      </Text>
    </Space>
  );
}
