"use client";

import React, { useEffect, useState, useCallback } from "react";
import {
  Modal,
  Select,
  Button,
  Space,
  Divider,
  Typography,
  Card,
  Tag,
  Spin,
  message,
} from "antd";
import { LockOutlined, AppstoreOutlined, DeleteOutlined } from "@ant-design/icons";
import { api } from "@/lib/api/client";
import type { ApplicationResponse, UpdateApplicationRequest } from "@/types/application";

const { Text } = Typography;

interface ModuleInfo {
  module_id: string;
  actions: Array<{ name: string }>;
}

interface EditPermissionsModalProps {
  app: ApplicationResponse;
  open: boolean;
  onClose: () => void;
  onSave: (updates: UpdateApplicationRequest) => Promise<void>;
  saving: boolean;
}

export function EditPermissionsModal({
  app,
  open,
  onClose,
  onSave,
  saving,
}: EditPermissionsModalProps) {
  const [allModules, setAllModules] = useState<string[]>([]);
  const [moduleActions, setModuleActions] = useState<Record<string, string[]>>({});
  const [loadingModules, setLoadingModules] = useState(false);

  // allowed_modules: which modules this app can use (empty = all)
  const [selectedModules, setSelectedModules] = useState<string[]>([]);
  // allowed_actions: per-module action restrictions
  const [actionRules, setActionRules] = useState<Record<string, string[]>>({});

  // Fetch the module list when the modal opens
  useEffect(() => {
    if (!open) return;
    setLoadingModules(true);
    api
      .get<ModuleInfo[]>("/modules")
      .then((mods) => setAllModules(mods.map((m) => m.module_id)))
      .catch(() => message.error("Failed to load module list"))
      .finally(() => setLoadingModules(false));
  }, [open]);

  // Seed form state from the current app settings
  useEffect(() => {
    if (!open) return;
    const mods = app.allowed_modules ?? [];
    setSelectedModules(mods);
    setActionRules(app.allowed_actions ?? {});
    // Pre-load actions for all modules that already have rules
    const toLoad = [
      ...mods,
      ...Object.keys(app.allowed_actions ?? {}),
    ];
    toLoad.forEach(loadModuleActions);
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, app]);

  const loadModuleActions = useCallback(async (moduleId: string) => {
    if (!moduleId) return;
    setModuleActions((prev) => {
      if (prev[moduleId] !== undefined) return prev; // already loaded
      return prev; // will load below
    });
    // Only fetch once
    setModuleActions((prev) => {
      if (prev[moduleId] !== undefined) return prev;
      // Mark as loading with a sentinel
      return { ...prev, [moduleId]: null as unknown as string[] };
    });
    try {
      const mod = await api.get<ModuleInfo>(`/modules/${moduleId}`);
      setModuleActions((prev) => ({
        ...prev,
        [moduleId]: mod.actions?.map((a) => a.name) ?? [],
      }));
    } catch {
      setModuleActions((prev) => ({ ...prev, [moduleId]: [] }));
    }
  }, []);

  // When user selects a module → auto-create an action rule card + load actions
  const handleModulesChange = (values: string[]) => {
    setSelectedModules(values);
    // Load actions for newly added modules
    for (const m of values) {
      loadModuleActions(m);
      // Add an empty rule if not already present
      setActionRules((prev) => (m in prev ? prev : { ...prev, [m]: [] }));
    }
    // Remove action rules for deselected modules (if no specific actions were set)
    setActionRules((prev) => {
      const next = { ...prev };
      for (const m of Object.keys(next)) {
        if (!values.includes(m) && (next[m] ?? []).length === 0) {
          delete next[m];
        }
      }
      return next;
    });
  };

  // Extra modules with action rules that aren't in selectedModules
  const extraRuleModules = Object.keys(actionRules).filter(
    (m) => !selectedModules.includes(m),
  );

  // All modules with action rule cards = selected + extra
  const ruleModules = [...selectedModules, ...extraRuleModules];

  // Modules available for adding an extra rule
  const addableModules = allModules.filter((m) => !ruleModules.includes(m));

  const handleAddExtraRule = (moduleId: string | null) => {
    if (!moduleId) return;
    loadModuleActions(moduleId);
    setActionRules((prev) => ({ ...prev, [moduleId]: [] }));
  };

  const handleRemoveRule = (moduleId: string) => {
    setActionRules((prev) => {
      const next = { ...prev };
      delete next[moduleId];
      return next;
    });
  };

  const handleSave = async () => {
    // Only send action rules that have at least one action selected
    const allowedActions: Record<string, string[]> = {};
    for (const [mod, actions] of Object.entries(actionRules)) {
      if ((actions ?? []).length > 0) {
        allowedActions[mod] = actions;
      }
    }
    await onSave({ allowed_modules: selectedModules, allowed_actions: allowedActions });
  };

  return (
    <Modal
      title={
        <Space>
          <LockOutlined />
          <span>Edit Permissions — {app.name}</span>
        </Space>
      }
      open={open}
      onCancel={onClose}
      width={640}
      footer={
        <Space>
          <Button onClick={onClose}>Cancel</Button>
          <Button type="primary" loading={saving} onClick={handleSave}>
            Save Permissions
          </Button>
        </Space>
      }
    >
      <Spin spinning={loadingModules}>
        <Space direction="vertical" size="large" style={{ width: "100%" }}>

          {/* ── Allowed Modules ── */}
          <div>
            <Space style={{ marginBottom: 6 }}>
              <AppstoreOutlined />
              <Text strong>Allowed Modules</Text>
            </Space>
            <Text type="secondary" style={{ display: "block", fontSize: 12, marginBottom: 8 }}>
              Select which modules this application can use. Leave empty to allow all modules.
              Selecting a module automatically shows its action settings below.
            </Text>
            <Select
              mode="multiple"
              style={{ width: "100%" }}
              placeholder="All modules allowed (no restriction)"
              value={selectedModules}
              onChange={handleModulesChange}
              options={allModules.map((id) => ({ label: id, value: id }))}
              allowClear
              showSearch
              filterOption={(input, opt) =>
                (opt?.label ?? "").toLowerCase().includes(input.toLowerCase())
              }
            />
          </div>

          <Divider style={{ margin: "4px 0" }} />

          {/* ── Allowed Actions per Module ── */}
          <div>
            <Space style={{ marginBottom: 6 }}>
              <LockOutlined />
              <Text strong>Allowed Actions per Module</Text>
            </Space>
            <Text type="secondary" style={{ display: "block", fontSize: 12, marginBottom: 12 }}>
              For each module, choose which actions are allowed.{" "}
              <strong>Leave the list empty to allow all actions</strong> of that module.
            </Text>

            <Space direction="vertical" size={8} style={{ width: "100%" }}>
              {ruleModules.map((modId) => {
                const availableActions = moduleActions[modId];
                const loadingActions = availableActions === null;
                const selected = actionRules[modId] ?? [];
                const isFromModuleFilter = selectedModules.includes(modId);

                return (
                  <Card
                    key={modId}
                    size="small"
                    style={{ background: "var(--ant-color-bg-layout)" }}
                    title={
                      <Space size={4}>
                        <Tag
                          color={isFromModuleFilter ? "blue" : "default"}
                          style={{ borderRadius: 4 }}
                        >
                          {modId}
                        </Tag>
                        {!isFromModuleFilter && (
                          <Tag style={{ borderRadius: 4, fontSize: 11 }}>extra rule</Tag>
                        )}
                      </Space>
                    }
                    extra={
                      !isFromModuleFilter && (
                        <Button
                          type="text"
                          size="small"
                          danger
                          icon={<DeleteOutlined />}
                          onClick={() => handleRemoveRule(modId)}
                        />
                      )
                    }
                  >
                    <Select
                      mode="multiple"
                      style={{ width: "100%" }}
                      placeholder="All actions allowed (leave empty to allow all)"
                      value={selected}
                      onChange={(val) =>
                        setActionRules((prev) => ({ ...prev, [modId]: val }))
                      }
                      onFocus={() => loadModuleActions(modId)}
                      options={(availableActions ?? []).map((a) => ({ label: a, value: a }))}
                      loading={loadingActions}
                      allowClear
                      showSearch
                    />
                    {selected.length === 0 && (
                      <Text type="secondary" style={{ fontSize: 11, marginTop: 4, display: "block" }}>
                        All actions of <strong>{modId}</strong> are allowed
                      </Text>
                    )}
                  </Card>
                );
              })}
            </Space>

            {/* Add an extra action rule for a module not in selectedModules */}
            {addableModules.length > 0 && (
              <Select
                style={{ width: "100%", marginTop: 8 }}
                placeholder="+ Restrict actions for another module..."
                value={null}
                onSelect={(val: string | null) => handleAddExtraRule(val)}
                options={addableModules.map((m) => ({ label: m, value: m }))}
                showSearch
                filterOption={(input, opt) =>
                  (opt?.label ?? "").toLowerCase().includes(input.toLowerCase())
                }
              />
            )}

            {ruleModules.length === 0 && (
              <Text type="secondary" style={{ fontSize: 12 }}>
                All modules and actions are allowed. Select modules above to configure restrictions.
              </Text>
            )}
          </div>
        </Space>
      </Spin>
    </Modal>
  );
}
