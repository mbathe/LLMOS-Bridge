"use client";

import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { api } from "@/lib/api/client";
import type {
  InstalledModulesResponse,
  InstallResult,
  UpgradeResult,
  LifecycleResponse,
  UninstallResponse,
  RescanResponse,
  ScanReportResponse,
} from "@/types/module";

export function useHub() {
  const queryClient = useQueryClient();

  const invalidate = async () => {
    await queryClient.refetchQueries({ queryKey: ["hub-installed"] });
    queryClient.invalidateQueries({ queryKey: ["modules"] });
  };

  // ── Queries ──

  const installed = useQuery<InstalledModulesResponse>({
    queryKey: ["hub-installed"],
    queryFn: () =>
      api.get<InstalledModulesResponse>("/admin/modules/installed"),
    refetchInterval: 15000,
  });

  // ── Mutations ──

  const installFromPath = useMutation<InstallResult, Error, string>({
    mutationFn: (path: string) =>
      api.post<InstallResult>("/admin/modules/install", { path }),
    onSuccess: invalidate,
  });

  const uninstallModule = useMutation<UninstallResponse, Error, string>({
    mutationFn: (moduleId: string) =>
      api.delete<UninstallResponse>(`/admin/modules/${moduleId}/uninstall`),
    onSuccess: invalidate,
  });

  const upgradeModule = useMutation<
    UpgradeResult,
    Error,
    { moduleId: string; path: string }
  >({
    mutationFn: ({ moduleId, path }) =>
      api.post<UpgradeResult>(`/admin/modules/${moduleId}/upgrade`, { path }),
    onSuccess: invalidate,
  });

  const enableModule = useMutation<LifecycleResponse, Error, string>({
    mutationFn: (moduleId: string) =>
      api.post<LifecycleResponse>(`/admin/modules/${moduleId}/enable`),
    onSuccess: invalidate,
  });

  const disableModule = useMutation<LifecycleResponse, Error, string>({
    mutationFn: (moduleId: string) =>
      api.post<LifecycleResponse>(`/admin/modules/${moduleId}/disable`),
    onSuccess: invalidate,
  });

  const rescanModule = useMutation<RescanResponse, Error, string>({
    mutationFn: (moduleId: string) =>
      api.post<RescanResponse>(`/admin/modules/${moduleId}/rescan`),
    onSuccess: invalidate,
  });

  const setTrustTier = useMutation<
    { module_id: string; trust_tier: string; previous_tier: string },
    Error,
    { moduleId: string; trust_tier: string; reason?: string }
  >({
    mutationFn: ({ moduleId, ...body }) =>
      api.put(`/admin/modules/${moduleId}/trust`, body),
    onSuccess: invalidate,
  });

  const getScanReport = (moduleId: string) =>
    api.get<ScanReportResponse>(`/admin/modules/${moduleId}/scan-report`);

  return {
    installed,
    installFromPath,
    uninstallModule,
    upgradeModule,
    enableModule,
    disableModule,
    rescanModule,
    setTrustTier,
    getScanReport,
    invalidate,
  };
}
