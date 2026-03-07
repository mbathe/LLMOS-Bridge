"use client";

import React, { useEffect, useState } from "react";
import {
  Modal,
  Descriptions,
  Tag,
  Space,
  Typography,
  List,
  Spin,
  Alert,
  Rate,
  Divider,
} from "antd";
import {
  SafetyCertificateOutlined,
  DownloadOutlined,
  WarningOutlined,
} from "@ant-design/icons";
import type {
  HubSearchResult,
  RatingsResponse,
  HubSecurityInfo,
} from "@/types/module";
import { RatingDisplay } from "./RatingDisplay";

const { Text } = Typography;

interface ModuleDetailModalProps {
  open: boolean;
  module: HubSearchResult | null;
  onClose: () => void;
  onInstall?: (moduleId: string) => void;
  getRatings: (moduleId: string) => Promise<RatingsResponse>;
  getSecurity: (moduleId: string) => Promise<HubSecurityInfo>;
}

export function ModuleDetailModal({
  open,
  module,
  onClose,
  onInstall,
  getRatings,
  getSecurity,
}: ModuleDetailModalProps) {
  const [ratings, setRatings] = useState<RatingsResponse | null>(null);
  const [security, setSecurity] = useState<HubSecurityInfo | null>(null);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    if (!open || !module) {
      setRatings(null);
      setSecurity(null);
      return;
    }
    setLoading(true);
    Promise.allSettled([
      getRatings(module.module_id),
      getSecurity(module.module_id),
    ]).then(([ratingsResult, securityResult]) => {
      if (ratingsResult.status === "fulfilled") setRatings(ratingsResult.value);
      if (securityResult.status === "fulfilled") setSecurity(securityResult.value);
      setLoading(false);
    });
  }, [open, module?.module_id]);

  if (!module) return null;

  const verdictColor =
    security?.scan_verdict === "allow"
      ? "success"
      : security?.scan_verdict === "warn"
        ? "warning"
        : security?.scan_verdict === "reject"
          ? "error"
          : "default";

  return (
    <Modal
      title={
        <Space>
          <Text strong style={{ fontSize: 16 }}>{module.module_id}</Text>
          <Tag color="geekblue">v{module.version}</Tag>
          {module.deprecated && <Tag color="error" icon={<WarningOutlined />}>Deprecated</Tag>}
        </Space>
      }
      open={open}
      onCancel={onClose}
      footer={null}
      width={640}
    >
      {loading ? (
        <div style={{ textAlign: "center", padding: 40 }}><Spin /></div>
      ) : (
        <Space direction="vertical" size="middle" style={{ width: "100%" }}>
          {module.deprecated && module.deprecated_message && (
            <Alert
              type="warning"
              showIcon
              message="This module is deprecated"
              description={
                <Space direction="vertical" size={2}>
                  <Text>{module.deprecated_message}</Text>
                  {module.replacement_module_id && (
                    <Text>
                      Replacement: <Text strong>{module.replacement_module_id}</Text>
                    </Text>
                  )}
                </Space>
              }
            />
          )}

          <Descriptions column={2} size="small" bordered>
            <Descriptions.Item label="Author">{module.author || "\u2014"}</Descriptions.Item>
            <Descriptions.Item label="License">{module.license || "\u2014"}</Descriptions.Item>
            <Descriptions.Item label="Downloads">
              <DownloadOutlined style={{ marginRight: 4 }} />
              {module.downloads.toLocaleString()}
            </Descriptions.Item>
            <Descriptions.Item label="Category">{module.category || "\u2014"}</Descriptions.Item>
            <Descriptions.Item label="Rating" span={2}>
              <RatingDisplay
                average={module.average_rating ?? 0}
                count={module.rating_count ?? 0}
              />
            </Descriptions.Item>
          </Descriptions>

          {module.description && (
            <Text type="secondary">{module.description}</Text>
          )}

          {module.tags && module.tags.length > 0 && (
            <Space wrap>
              {module.tags.map((tag) => (
                <Tag key={tag} color="purple">{tag}</Tag>
              ))}
            </Space>
          )}

          {/* Security */}
          {security && (
            <>
              <Divider orientation="left" orientationMargin={0}>
                <SafetyCertificateOutlined /> Security Scan
              </Divider>
              <Space>
                <Text>Score: <Text strong>{security.scan_score}/100</Text></Text>
                <Tag color={verdictColor}>{security.scan_verdict}</Tag>
                <Text type="secondary">{security.findings.length} finding(s)</Text>
              </Space>
              {security.findings.length > 0 && (
                <List
                  size="small"
                  dataSource={security.findings.slice(0, 5)}
                  renderItem={(f) => (
                    <List.Item>
                      <Space>
                        <Tag color={f.severity >= 8 ? "error" : f.severity >= 5 ? "warning" : "default"}>
                          {f.severity.toFixed(1)}
                        </Tag>
                        <Text style={{ fontSize: 13 }}>
                          {f.description} ({f.file_path}:{f.line_number})
                        </Text>
                      </Space>
                    </List.Item>
                  )}
                />
              )}
            </>
          )}

          {/* Ratings */}
          {ratings && ratings.ratings.length > 0 && (
            <>
              <Divider orientation="left" orientationMargin={0}>Ratings</Divider>
              <List
                size="small"
                dataSource={ratings.ratings.slice(0, 5)}
                renderItem={(r) => (
                  <List.Item>
                    <Space direction="vertical" size={0}>
                      <Space size={4}>
                        <Rate disabled value={r.stars} style={{ fontSize: 12 }} />
                        <Text type="secondary" style={{ fontSize: 12 }}>
                          by {r.publisher_id}
                        </Text>
                      </Space>
                      {r.comment && <Text style={{ fontSize: 13 }}>{r.comment}</Text>}
                    </Space>
                  </List.Item>
                )}
              />
            </>
          )}
        </Space>
      )}
    </Modal>
  );
}
