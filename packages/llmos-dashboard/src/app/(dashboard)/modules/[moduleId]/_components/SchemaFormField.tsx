"use client";

import React from "react";
import { Form, Input, InputNumber, Switch, Select, Slider, Tooltip, Tag, Typography } from "antd";
import { WarningOutlined } from "@ant-design/icons";
import type { JSONSchemaProperty } from "@/types/module";

const { Text } = Typography;

interface SchemaFormFieldProps {
  name: string;
  property: JSONSchemaProperty;
  required?: boolean;
}

export function SchemaFormField({ name, property, required }: SchemaFormFieldProps) {
  const label = property["x-ui-label"] || name.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
  const isSecret = property["x-ui-secret"] ?? false;
  const restartRequired = property["x-ui-restart-required"] ?? false;

  const rules = required
    ? [{ required: true, message: `${label} is required` }]
    : [];

  const widget = getWidget(property, isSecret);

  return (
    <Form.Item
      name={name}
      label={
        <span>
          {label}
          {restartRequired && (
            <Tooltip title="Changing this requires a module restart">
              <Tag color="warning" style={{ marginLeft: 6, fontSize: 10 }}>
                <WarningOutlined /> restart
              </Tag>
            </Tooltip>
          )}
        </span>
      }
      tooltip={property.description}
      rules={rules}
      valuePropName={property.type === "boolean" ? "checked" : "value"}
      extra={
        property.description ? (
          <Text type="secondary" style={{ fontSize: 12 }}>
            {property.description}
          </Text>
        ) : undefined
      }
    >
      {widget}
    </Form.Item>
  );
}

function getWidget(property: JSONSchemaProperty, isSecret: boolean): React.ReactElement {
  // 1. Explicit widget hint
  const hint = property["x-ui-widget"];
  if (hint) {
    switch (hint) {
      case "textarea":
        return <Input.TextArea rows={4} style={{ fontFamily: "monospace" }} />;
      case "slider":
        return <Slider min={property.minimum ?? 0} max={property.maximum ?? 100} />;
      case "password":
        return <Input.Password placeholder={`Enter ${property.title ?? "value"}`} />;
      case "select":
        return (
          <Select
            options={(property.enum ?? []).map((v) => ({
              label: String(v),
              value: v as string,
            }))}
          />
        );
    }
  }

  // 2. Secret
  if (isSecret) {
    return <Input.Password placeholder="Enter secret value" />;
  }

  // 3. Enum → Select
  if (property.enum && property.enum.length > 0) {
    return (
      <Select
        options={property.enum.map((v) => ({
          label: String(v),
          value: v as string,
        }))}
        placeholder="Select..."
      />
    );
  }

  // 4. Type-based
  switch (property.type) {
    case "boolean":
      return <Switch />;
    case "integer":
      return (
        <InputNumber
          precision={0}
          min={property.minimum}
          max={property.maximum}
          style={{ width: "100%" }}
        />
      );
    case "number":
      return (
        <InputNumber
          min={property.minimum}
          max={property.maximum}
          style={{ width: "100%" }}
        />
      );
    case "array":
      return <Select mode="tags" placeholder="Add items..." />;
    case "object":
      return <Input.TextArea rows={4} placeholder="{}" style={{ fontFamily: "monospace" }} />;
    case "string":
    default:
      return <Input placeholder={`Enter ${property.title ?? "value"}`} />;
  }
}
