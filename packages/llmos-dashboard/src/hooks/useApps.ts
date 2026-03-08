"use client";

import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { api } from "@/lib/api/client";
import type {
  AppRecord,
  RegisterAppRequest,
  PrepareAppResponse,
  RunAppRequest,
  RunAppResponse,
  ValidateAppResponse,
  UpdateStatusRequest,
  YamlParsedConfig,
} from "@/types/appLanguage";

export function useApps() {
  const queryClient = useQueryClient();

  const invalidate = () => {
    queryClient.invalidateQueries({ queryKey: ["llmos-apps"] });
    queryClient.invalidateQueries({ queryKey: ["applications"] });
  };

  // ── Queries ──

  const apps = useQuery<AppRecord[]>({
    queryKey: ["llmos-apps"],
    queryFn: () => api.get<AppRecord[]>("/apps"),
    refetchInterval: 10000,
  });

  // ── Mutations ──

  const registerApp = useMutation<AppRecord, Error, RegisterAppRequest>({
    mutationFn: (body) => api.post<AppRecord>("/apps/register", body),
    onSuccess: invalidate,
  });

  const deleteApp = useMutation<void, Error, string>({
    mutationFn: (appId) => api.delete<void>(`/apps/${appId}`),
    onSuccess: invalidate,
  });

  const runApp = useMutation<
    RunAppResponse,
    Error,
    { appId: string; body: RunAppRequest }
  >({
    mutationFn: ({ appId, body }) =>
      api.post<RunAppResponse>(`/apps/${appId}/run`, body),
    onSuccess: invalidate,
  });

  const prepareApp = useMutation<PrepareAppResponse, Error, string>({
    mutationFn: (appId) =>
      api.post<PrepareAppResponse>(`/apps/${appId}/prepare`),
    onSuccess: invalidate,
  });

  const validateApp = useMutation<ValidateAppResponse, Error, string>({
    mutationFn: (appId) =>
      api.post<ValidateAppResponse>(`/apps/${appId}/validate`),
  });

  const updateStatus = useMutation<
    AppRecord,
    Error,
    { appId: string; status: string }
  >({
    mutationFn: ({ appId, status }) =>
      api.put<AppRecord>(`/apps/${appId}/status`, { status }),
    onSuccess: invalidate,
  });

  return {
    apps,
    registerApp,
    deleteApp,
    prepareApp,
    runApp,
    validateApp,
    updateStatus,
    invalidate,
  };
}

export function useAppDetail(appId: string) {
  return useQuery<AppRecord>({
    queryKey: ["llmos-apps", appId],
    queryFn: () => api.get<AppRecord>(`/apps/${appId}`),
    enabled: !!appId,
  });
}

/**
 * Fetch the structured/parsed YAML configuration for a YAML app,
 * including sync status between YAML capabilities and the Identity.
 */
export function useYamlParsed(appId: string | undefined) {
  const queryClient = useQueryClient();

  const parsed = useQuery<YamlParsedConfig>({
    queryKey: ["llmos-apps", appId, "parsed"],
    queryFn: () => api.get<YamlParsedConfig>(`/apps/${appId}/parsed`),
    enabled: !!appId,
    refetchInterval: 15000,
    retry: false,
  });

  const syncFromYaml = useMutation<
    { synced: boolean; yaml_modules: string[]; yaml_allowed_actions: Record<string, string[]> },
    Error,
    string
  >({
    mutationFn: (id) => api.post(`/apps/${id}/sync-from-yaml`, {}),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["llmos-apps", appId, "parsed"] });
      queryClient.invalidateQueries({ queryKey: ["applications"] });
    },
  });

  const syncToYaml = useMutation<
    { synced: boolean; allowed_modules: string[]; allowed_actions: Record<string, string[]> },
    Error,
    string
  >({
    mutationFn: (id) => api.post(`/apps/${id}/sync-to-yaml`, {}),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["llmos-apps", appId, "parsed"] });
    },
  });

  return { parsed, syncFromYaml, syncToYaml };
}

/**
 * Fetch the linked Application identity for a YAML app.
 * When a YAML app is registered, an Application identity with the same ID
 * is auto-created. This hook fetches that identity to show security settings.
 */
export function useLinkedApplication(appId: string) {
  return useQuery({
    queryKey: ["llmos-apps", appId, "linked-identity"],
    queryFn: async () => {
      try {
        // The Application identity shares the same ID as the YAML app
        return await api.get(`/applications/${appId}`);
      } catch {
        return null; // Identity system may be disabled
      }
    },
    enabled: !!appId,
    retry: false,
  });
}
