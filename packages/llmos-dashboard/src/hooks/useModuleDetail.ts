"use client";

import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { api } from "@/lib/api/client";
import type {
  ModuleDetailInfo,
  ModuleManifest,
  ModuleHealth,
  ModuleMetrics,
  ModuleStateSnapshot,
  ModuleDescription,
  ModuleDocs,
  ModuleConfigSchema,
  LifecycleResponse,
  ActionToggleResponse,
  VerifyResponse,
  UninstallResponse,
} from "@/types/module";

export function useModuleDetail(moduleId: string) {
  const queryClient = useQueryClient();
  const prefix = `/admin/modules/${moduleId}`;

  const keys = {
    info: ["module", moduleId, "info"],
    manifest: ["module", moduleId, "manifest"],
    health: ["module", moduleId, "health"],
    metrics: ["module", moduleId, "metrics"],
    state: ["module", moduleId, "state"],
    describe: ["module", moduleId, "describe"],
    docs: ["module", moduleId, "docs"],
    configSchema: ["module", moduleId, "configSchema"],
  };

  // ── Queries ──

  const info = useQuery<ModuleDetailInfo>({
    queryKey: keys.info,
    queryFn: () =>
      api.get<ModuleDetailInfo>(prefix, {
        include_health: "true",
        include_metrics: "true",
      }),
    refetchInterval: 10000,
  });

  const manifest = useQuery<ModuleManifest>({
    queryKey: keys.manifest,
    queryFn: () => api.get<ModuleManifest>(`${prefix}/manifest`),
  });

  const health = useQuery<ModuleHealth>({
    queryKey: keys.health,
    queryFn: () => api.get<ModuleHealth>(`${prefix}/health`),
    refetchInterval: 5000,
  });

  const metrics = useQuery<ModuleMetrics>({
    queryKey: keys.metrics,
    queryFn: () => api.get<ModuleMetrics>(`${prefix}/metrics`),
    refetchInterval: 5000,
  });

  const stateSnapshot = useQuery<ModuleStateSnapshot>({
    queryKey: keys.state,
    queryFn: () => api.get<ModuleStateSnapshot>(`${prefix}/state`),
    refetchInterval: 10000,
  });

  const describe = useQuery<ModuleDescription>({
    queryKey: keys.describe,
    queryFn: () => api.get<ModuleDescription>(`${prefix}/describe`),
  });

  const docs = useQuery<ModuleDocs>({
    queryKey: keys.docs,
    queryFn: () => api.get<ModuleDocs>(`${prefix}/docs`),
  });

  const configSchema = useQuery<ModuleConfigSchema>({
    queryKey: keys.configSchema,
    queryFn: () => api.get<ModuleConfigSchema>(`${prefix}/config/schema`),
  });

  // ── Mutations ──

  const invalidateAll = () => {
    Object.values(keys).forEach((k) =>
      queryClient.invalidateQueries({ queryKey: k }),
    );
  };

  const enableModule = useMutation<LifecycleResponse>({
    mutationFn: () => api.post<LifecycleResponse>(`${prefix}/enable`),
    onSuccess: invalidateAll,
  });

  const disableModule = useMutation<LifecycleResponse>({
    mutationFn: () => api.post<LifecycleResponse>(`${prefix}/disable`),
    onSuccess: invalidateAll,
  });

  const pauseModule = useMutation<LifecycleResponse>({
    mutationFn: () => api.post<LifecycleResponse>(`${prefix}/pause`),
    onSuccess: invalidateAll,
  });

  const resumeModule = useMutation<LifecycleResponse>({
    mutationFn: () => api.post<LifecycleResponse>(`${prefix}/resume`),
    onSuccess: invalidateAll,
  });

  const restartModule = useMutation<LifecycleResponse>({
    mutationFn: () => api.post<LifecycleResponse>(`${prefix}/restart`),
    onSuccess: invalidateAll,
  });

  const updateConfig = useMutation({
    mutationFn: (config: Record<string, unknown>) =>
      api.put(`${prefix}/config`, { config }),
    onSuccess: invalidateAll,
  });

  const enableAction = useMutation<ActionToggleResponse, Error, string>({
    mutationFn: (actionName: string) =>
      api.post<ActionToggleResponse>(`${prefix}/actions/${actionName}/enable`),
    onSuccess: invalidateAll,
  });

  const disableAction = useMutation<
    ActionToggleResponse,
    Error,
    { action: string; reason?: string }
  >({
    mutationFn: ({ action, reason }) =>
      api.post<ActionToggleResponse>(`${prefix}/actions/${action}/disable`, {
        reason: reason ?? "",
      }),
    onSuccess: invalidateAll,
  });

  const verifyModule = useMutation<VerifyResponse>({
    mutationFn: () =>
      api.get<VerifyResponse>(`/admin/hub/modules/${moduleId}/verify`),
  });

  const uninstallModule = useMutation<UninstallResponse>({
    mutationFn: () =>
      api.delete<UninstallResponse>(`/admin/modules/${moduleId}/uninstall`),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["hub-installed"] });
      queryClient.invalidateQueries({ queryKey: ["modules"] });
      invalidateAll();
    },
  });

  return {
    info,
    manifest,
    health,
    metrics,
    stateSnapshot,
    describe,
    docs,
    configSchema,
    enableModule,
    disableModule,
    pauseModule,
    resumeModule,
    restartModule,
    updateConfig,
    enableAction,
    disableAction,
    verifyModule,
    uninstallModule,
    invalidateAll,
  };
}

export type UseModuleDetailReturn = ReturnType<typeof useModuleDetail>;
