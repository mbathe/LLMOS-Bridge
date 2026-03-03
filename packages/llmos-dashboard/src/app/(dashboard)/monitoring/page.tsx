"use client";

import React, { useState, useMemo } from "react";
import {
  Card,
  Typography,
  Space,
  Tag,
  Button,
  Select,
  Row,
  Col,
  List,
  Switch,
  Tooltip,
  Badge,
} from "antd";
import {
  ThunderboltOutlined,
  ClearOutlined,
  WifiOutlined,
  DisconnectOutlined,
  MonitorOutlined,
  FilterOutlined,
  ClockCircleOutlined,
} from "@ant-design/icons";
import { useWSEventStore } from "@/stores/ws-events";
import { PageHeader } from "@/components/common/PageHeader";
import { StatCard } from "@/components/common/StatCard";
import { EmptyState } from "@/components/common/EmptyState";
import { JsonViewer } from "@/components/common/JsonViewer";
import type { WSMessage } from "@/types/events";

const { Text } = Typography;

const eventTypeOptions: { label: string; value: string }[] = [
  { label: "All Events", value: "" },
  { label: "Plan Events", value: "plan_" },
  { label: "Action Events", value: "action_" },
  { label: "Module Events", value: "module_" },
  { label: "Security Events", value: "security_" },
  { label: "Approval Events", value: "approval_" },
  { label: "Perception Events", value: "perception_" },
  { label: "Trigger Events", value: "trigger_" },
  { label: "Recording Events", value: "recording_" },
];

const eventTypeColorMap: Record<string, string> = {
  plan_: "blue",
  action_: "cyan",
  module_: "purple",
  security_: "red",
  approval_: "volcano",
  perception_: "geekblue",
  trigger_: "orange",
  recording_: "gold",
};

function getEventColor(type: string): string {
  for (const [prefix, color] of Object.entries(eventTypeColorMap)) {
    if (type.startsWith(prefix)) return color;
  }
  return "default";
}

export default function MonitoringPage() {
  const events = useWSEventStore((s) => s.events);
  const connected = useWSEventStore((s) => s.connected);
  const clearEvents = useWSEventStore((s) => s.clearEvents);
  const [typeFilter, setTypeFilter] = useState<string>("");
  const [showPayload, setShowPayload] = useState(false);

  const filteredEvents = useMemo(() => {
    const filtered = typeFilter
      ? events.filter((e) => e.type.startsWith(typeFilter))
      : events;
    return [...filtered].reverse();
  }, [events, typeFilter]);

  const eventTypeCounts = useMemo(() => {
    const counts: Record<string, number> = {};
    for (const event of events) {
      counts[event.type] = (counts[event.type] ?? 0) + 1;
    }
    return counts;
  }, [events]);

  const uniqueTypes = Object.keys(eventTypeCounts).sort();

  return (
    <Space direction="vertical" size="large" style={{ width: "100%" }}>
      <PageHeader
        icon={<MonitorOutlined />}
        title="Event Monitor"
        subtitle="Real-time WebSocket event stream and analytics"
        tags={
          connected ? (
            <Tag color="success" icon={<WifiOutlined />}>Connected</Tag>
          ) : (
            <Tag color="error" icon={<DisconnectOutlined />}>Disconnected</Tag>
          )
        }
        extra={
          <>
            <Space size="small">
              <Text type="secondary" style={{ fontSize: 12 }}>
                Payloads
              </Text>
              <Switch
                size="small"
                checked={showPayload}
                onChange={setShowPayload}
              />
            </Space>
            <Select
              value={typeFilter}
              onChange={setTypeFilter}
              options={eventTypeOptions}
              style={{ width: 180 }}
              placeholder="Filter by type"
              suffixIcon={<FilterOutlined />}
            />
            <Button
              icon={<ClearOutlined />}
              onClick={clearEvents}
            >
              Clear
            </Button>
          </>
        }
      />

      {/* Stats */}
      <Row gutter={[16, 16]}>
        <Col xs={24} sm={12} lg={6}>
          <StatCard
            title="Total Events"
            value={events.length}
            prefix={<ThunderboltOutlined />}
            color="#1677ff"
            footer={
              <Text type="secondary" style={{ fontSize: 12 }}>
                Since session start
              </Text>
            }
          />
        </Col>
        <Col xs={24} sm={12} lg={6}>
          <StatCard
            title="Event Types"
            value={uniqueTypes.length}
            prefix={<MonitorOutlined />}
            color="#722ed1"
            footer={
              <Text type="secondary" style={{ fontSize: 12 }}>
                Unique event categories
              </Text>
            }
          />
        </Col>
        <Col xs={24} sm={12} lg={6}>
          <StatCard
            title="Connection Status"
            value={connected ? "Live" : "Offline"}
            prefix={
              connected ? (
                <WifiOutlined style={{ color: "#52c41a" }} />
              ) : (
                <DisconnectOutlined style={{ color: "#ff4d4f" }} />
              )
            }
            color={connected ? "#52c41a" : "#ff4d4f"}
            valueStyle={{ color: connected ? "#52c41a" : "#ff4d4f" }}
            footer={
              <Text type="secondary" style={{ fontSize: 12 }}>
                WebSocket connection
              </Text>
            }
          />
        </Col>
        <Col xs={24} sm={12} lg={6}>
          <StatCard
            title="Filtered Events"
            value={filteredEvents.length}
            suffix={`/ ${events.length}`}
            color="#fa8c16"
            footer={
              <Text type="secondary" style={{ fontSize: 12 }}>
                {typeFilter ? `Filtered by: ${typeFilter}*` : "No filter active"}
              </Text>
            }
          />
        </Col>
      </Row>

      {/* Event Type Summary */}
      {uniqueTypes.length > 0 && (
        <Card
          title={
            <Space>
              <ThunderboltOutlined />
              <span>Event Type Counts</span>
              <Tag color="blue">{uniqueTypes.length} types</Tag>
            </Space>
          }
          size="small"
        >
          <Space wrap size={[8, 8]}>
            {uniqueTypes.map((type) => (
              <Tooltip key={type} title={`Click to filter by ${type} events`}>
                <Tag
                  color={getEventColor(type)}
                  style={{
                    cursor: "pointer",
                    padding: "4px 10px",
                    fontSize: 13,
                    borderRadius: 6,
                  }}
                  onClick={() => {
                    const prefix = eventTypeOptions.find((o) =>
                      type.startsWith(o.value) && o.value !== "",
                    );
                    setTypeFilter(prefix?.value ?? "");
                  }}
                >
                  {type}
                  <Badge
                    count={eventTypeCounts[type]}
                    style={{
                      marginLeft: 6,
                      fontSize: 11,
                      boxShadow: "none",
                    }}
                    size="small"
                  />
                </Tag>
              </Tooltip>
            ))}
          </Space>
        </Card>
      )}

      {/* Event Stream */}
      <Card
        title={
          <Space>
            <MonitorOutlined />
            <span>Event Stream</span>
            <Tag color="default">{filteredEvents.length} events</Tag>
          </Space>
        }
        extra={
          <Space>
            <Text type="secondary" style={{ fontSize: 12 }}>
              <ClockCircleOutlined style={{ marginRight: 4 }} />
              Most recent first
            </Text>
            {connected && (
              <Badge status="processing" text={<Text type="secondary" style={{ fontSize: 12 }}>Live</Text>} />
            )}
          </Space>
        }
      >
        {filteredEvents.length === 0 ? (
          <EmptyState
            description={
              events.length === 0
                ? "No events yet. Connect WebSocket to see live events."
                : "No events match the current filter."
            }
          />
        ) : (
          <List
            size="small"
            dataSource={filteredEvents.slice(0, 100)}
            renderItem={(event: WSMessage) => (
              <List.Item
                style={{
                  padding: "8px 0",
                  borderBottom: "1px solid var(--ant-color-border)",
                }}
              >
                <div style={{ width: "100%" }}>
                  <Space
                    style={{
                      display: "flex",
                      justifyContent: "space-between",
                      width: "100%",
                    }}
                  >
                    <Space>
                      <Tag
                        color={getEventColor(event.type)}
                        style={{ minWidth: 120, textAlign: "center" }}
                      >
                        {event.type}
                      </Tag>
                      <Tooltip title={new Date(event.timestamp).toLocaleString()}>
                        <Text type="secondary" style={{ fontSize: 12 }}>
                          <ClockCircleOutlined style={{ marginRight: 4 }} />
                          {new Date(event.timestamp).toLocaleTimeString()}
                        </Text>
                      </Tooltip>
                    </Space>
                    {!showPayload && (
                      <Tooltip title="Toggle payload view for full details">
                        <Text
                          ellipsis
                          type="secondary"
                          style={{
                            maxWidth: 400,
                            fontSize: 12,
                            fontFamily: "monospace",
                          }}
                        >
                          {JSON.stringify(event.payload).slice(0, 100)}
                        </Text>
                      </Tooltip>
                    )}
                  </Space>
                  {showPayload && (
                    <div style={{ marginTop: 8 }}>
                      <JsonViewer data={event.payload} maxHeight={200} />
                    </div>
                  )}
                </div>
              </List.Item>
            )}
          />
        )}
      </Card>
    </Space>
  );
}
