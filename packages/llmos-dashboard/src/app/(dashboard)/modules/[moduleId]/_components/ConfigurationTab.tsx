"use client";

import React, { useState, useMemo, useCallback } from "react";
import {
  Card,
  Form,
  Spin,
  Segmented,
  Collapse,
  Button,
  Input,
  Space,
  message,
  Typography,
} from "antd";
import { SaveOutlined, UndoOutlined, CodeOutlined } from "@ant-design/icons";
import type { UseModuleDetailReturn } from "@/hooks/useModuleDetail";
import type { JSONSchemaProperty } from "@/types/module";
import { EmptyState } from "@/components/common/EmptyState";
import { SchemaFormField } from "./SchemaFormField";

const { Text } = Typography;

type ViewMode = "Form View" | "Raw JSON";

interface ConfigurationTabProps {
  hook: UseModuleDetailReturn;
}

interface GroupedField {
  name: string;
  property: JSONSchemaProperty;
  order: number;
  required: boolean;
}

export function ConfigurationTab({ hook }: ConfigurationTabProps) {
  const [form] = Form.useForm();
  const [viewMode, setViewMode] = useState<ViewMode>("Form View");
  const [rawJson, setRawJson] = useState("");
  const [messageApi, contextHolder] = message.useMessage();

  const { data: configSchema, isLoading } = hook.configSchema;

  // Compute initial values from schema defaults
  const initialValues = useMemo(() => {
    if (!configSchema?.schema?.properties) return {};
    const values: Record<string, unknown> = {};
    for (const [key, prop] of Object.entries(configSchema.schema.properties)) {
      if (prop.default !== undefined) {
        values[key] = prop.default;
      }
    }
    return values;
  }, [configSchema]);

  // Group properties by x-ui-category, sorted by x-ui-order
  const groupedFields = useMemo(() => {
    if (!configSchema?.schema?.properties) return new Map<string, GroupedField[]>();

    const requiredSet = new Set(configSchema.schema.required ?? []);
    const groups = new Map<string, GroupedField[]>();

    for (const [name, property] of Object.entries(configSchema.schema.properties)) {
      const category = property["x-ui-category"] ?? "general";
      const order = property["x-ui-order"] ?? 0;
      const required = requiredSet.has(name);

      if (!groups.has(category)) {
        groups.set(category, []);
      }
      groups.get(category)!.push({ name, property, order, required });
    }

    // Sort fields within each group by order
    for (const fields of groups.values()) {
      fields.sort((a, b) => a.order - b.order);
    }

    return groups;
  }, [configSchema]);

  const handleSave = useCallback(async () => {
    try {
      const values = await form.validateFields();
      await hook.updateConfig.mutateAsync(values);
      messageApi.success("Configuration saved successfully.");
    } catch (err) {
      if (err && typeof err === "object" && "errorFields" in err) {
        messageApi.error("Please fix validation errors before saving.");
      } else {
        messageApi.error("Failed to save configuration.");
      }
    }
  }, [form, hook.updateConfig, messageApi]);

  const handleResetDefaults = useCallback(() => {
    form.setFieldsValue(initialValues);
    messageApi.info("Form reset to default values.");
  }, [form, initialValues, messageApi]);

  const handleApplyJson = useCallback(() => {
    try {
      const parsed = JSON.parse(rawJson);
      if (typeof parsed !== "object" || parsed === null || Array.isArray(parsed)) {
        messageApi.error("JSON must be a plain object.");
        return;
      }
      form.setFieldsValue(parsed);
      handleSave();
    } catch {
      messageApi.error("Invalid JSON. Please check the syntax.");
    }
  }, [rawJson, form, handleSave, messageApi]);

  const handleViewModeChange = useCallback(
    (value: ViewMode) => {
      if (value === "Raw JSON") {
        const currentValues = form.getFieldsValue();
        setRawJson(JSON.stringify(currentValues, null, 2));
      }
      setViewMode(value);
    },
    [form],
  );

  if (isLoading) {
    return (
      <div style={{ textAlign: "center", padding: 48 }}>
        <Spin size="large" />
      </div>
    );
  }

  if (!configSchema || configSchema.configurable === false || configSchema.schema === null) {
    return (
      <EmptyState description="This module does not expose configurable parameters." />
    );
  }

  const categoryNames = Array.from(groupedFields.keys());
  const hasMultipleCategories = categoryNames.length > 1;

  const renderFields = (fields: GroupedField[]) =>
    fields.map((field) => (
      <SchemaFormField
        key={field.name}
        name={field.name}
        property={field.property}
        required={field.required}
      />
    ));

  return (
    <>
      {contextHolder}
      <Card title="Module Configuration">
        <Space direction="vertical" size="middle" style={{ width: "100%" }}>
          <Segmented
            options={["Form View", "Raw JSON"] as ViewMode[]}
            value={viewMode}
            onChange={(val) => handleViewModeChange(val as ViewMode)}
          />

          {viewMode === "Form View" ? (
            <Form
              form={form}
              layout="vertical"
              initialValues={initialValues}
              style={{ maxWidth: 720 }}
            >
              {hasMultipleCategories ? (
                <Collapse
                  defaultActiveKey={categoryNames}
                  items={categoryNames.map((category) => ({
                    key: category,
                    label: (
                      <Text strong style={{ textTransform: "capitalize" }}>
                        {category.replace(/_/g, " ")}
                      </Text>
                    ),
                    children: renderFields(groupedFields.get(category) ?? []),
                  }))}
                />
              ) : (
                renderFields(groupedFields.get(categoryNames[0] ?? "general") ?? [])
              )}

              <div style={{ marginTop: 24 }}>
                <Space>
                  <Button
                    type="primary"
                    icon={<SaveOutlined />}
                    loading={hook.updateConfig.isPending}
                    onClick={handleSave}
                  >
                    Save Configuration
                  </Button>
                  <Button icon={<UndoOutlined />} onClick={handleResetDefaults}>
                    Reset to Defaults
                  </Button>
                </Space>
              </div>
            </Form>
          ) : (
            <div>
              <Input.TextArea
                value={rawJson}
                onChange={(e) => setRawJson(e.target.value)}
                rows={20}
                style={{ fontFamily: "monospace", fontSize: 13 }}
              />
              <div style={{ marginTop: 16 }}>
                <Button
                  type="primary"
                  icon={<CodeOutlined />}
                  loading={hook.updateConfig.isPending}
                  onClick={handleApplyJson}
                >
                  Apply JSON
                </Button>
              </div>
            </div>
          )}
        </Space>
      </Card>
    </>
  );
}
