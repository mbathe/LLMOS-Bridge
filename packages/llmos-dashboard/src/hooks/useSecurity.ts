"use client";

import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { api } from "@/lib/api/client";
import type {
  SecurityStatus,
  SecurityLayersResponse,
  IntentVerifierStatus,
  VerificationResult,
  PatternsResponse,
  PermissionsResponse,
  PermissionScope,
} from "@/types/security";
import type { ScannersResponse } from "@/types/events";

const keys = {
  layers: ["security", "layers"],
  status: ["security", "status"],
  permissions: ["security", "permissions"],
  scanners: ["security", "scanners"],
  patterns: ["security", "patterns"],
  intentStatus: ["security", "intent-status"],
};

export function useSecurity() {
  const queryClient = useQueryClient();

  // ── Queries ──

  const layers = useQuery<SecurityLayersResponse>({
    queryKey: keys.layers,
    queryFn: () => api.get<SecurityLayersResponse>("/admin/security/layers"),
    retry: false,
    refetchInterval: 15000,
  });

  // status & permissions require the SecurityModule (enable_decorators=true).
  // Only fetch when layers confirms decorators are on to avoid 503 spam.
  const decoratorsOn = layers.data?.decorators_enabled === true;

  const status = useQuery<SecurityStatus>({
    queryKey: keys.status,
    queryFn: () => api.get<SecurityStatus>("/admin/security/status"),
    retry: false,
    refetchInterval: 15000,
    enabled: decoratorsOn,
  });

  const permissions = useQuery<PermissionsResponse>({
    queryKey: keys.permissions,
    queryFn: () => api.get<PermissionsResponse>("/admin/security/permissions"),
    retry: false,
    enabled: decoratorsOn,
  });

  const scanners = useQuery<ScannersResponse>({
    queryKey: keys.scanners,
    queryFn: () => api.get<ScannersResponse>("/security/scanners"),
    retry: false,
    refetchInterval: 30000,
  });

  const patterns = useQuery<PatternsResponse>({
    queryKey: keys.patterns,
    queryFn: () => api.get<PatternsResponse>("/admin/security/scanners/patterns"),
    retry: false,
  });

  const intentStatus = useQuery<IntentVerifierStatus>({
    queryKey: keys.intentStatus,
    queryFn: () =>
      api.get<IntentVerifierStatus>("/admin/security/intent-verifier/status"),
    retry: false,
    refetchInterval: 30000,
  });

  // ── Mutations ──

  const grantPermission = useMutation({
    mutationFn: (params: {
      permission: string;
      module_id: string;
      scope: PermissionScope;
      reason?: string;
    }) => api.post("/admin/security/permissions/grant", params),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: keys.permissions });
      queryClient.invalidateQueries({ queryKey: keys.status });
      queryClient.invalidateQueries({ queryKey: keys.layers });
    },
  });

  const revokePermission = useMutation({
    mutationFn: (params: { permission: string; module_id: string }) =>
      api.post("/admin/security/permissions/revoke", params),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: keys.permissions });
      queryClient.invalidateQueries({ queryKey: keys.status });
      queryClient.invalidateQueries({ queryKey: keys.layers });
    },
  });

  const enableScanner = useMutation({
    mutationFn: (scannerId: string) =>
      api.post(`/security/scanners/${scannerId}/enable`),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: keys.scanners });
      queryClient.invalidateQueries({ queryKey: keys.layers });
    },
  });

  const disableScanner = useMutation({
    mutationFn: (scannerId: string) =>
      api.post(`/security/scanners/${scannerId}/disable`),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: keys.scanners });
      queryClient.invalidateQueries({ queryKey: keys.layers });
    },
  });

  const enablePattern = useMutation({
    mutationFn: (patternId: string) =>
      api.post(`/admin/security/scanners/patterns/${patternId}/enable`),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: keys.patterns });
      queryClient.invalidateQueries({ queryKey: keys.scanners });
    },
  });

  const disablePattern = useMutation({
    mutationFn: (patternId: string) =>
      api.post(`/admin/security/scanners/patterns/${patternId}/disable`),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: keys.patterns });
      queryClient.invalidateQueries({ queryKey: keys.scanners });
    },
  });

  const addPattern = useMutation({
    mutationFn: (params: {
      id: string;
      category: string;
      pattern: string;
      severity: number;
      description: string;
    }) => api.post("/admin/security/scanners/patterns", params),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: keys.patterns });
      queryClient.invalidateQueries({ queryKey: keys.scanners });
    },
  });

  const dryScan = useMutation({
    mutationFn: (input: Record<string, unknown>) =>
      api.post("/security/scanners/scan", input),
  });

  const testVerification = useMutation({
    mutationFn: (text: string) =>
      api.post<VerificationResult>("/admin/security/intent-verifier/test", {
        text,
      }),
  });

  const clearCache = useMutation({
    mutationFn: () =>
      api.post("/admin/security/intent-verifier/cache/clear"),
    onSuccess: () =>
      queryClient.invalidateQueries({ queryKey: keys.intentStatus }),
  });

  return {
    // queries
    layers,
    status,
    permissions,
    scanners,
    patterns,
    intentStatus,
    // mutations
    grantPermission,
    revokePermission,
    enableScanner,
    disableScanner,
    enablePattern,
    disablePattern,
    addPattern,
    dryScan,
    testVerification,
    clearCache,
  };
}
