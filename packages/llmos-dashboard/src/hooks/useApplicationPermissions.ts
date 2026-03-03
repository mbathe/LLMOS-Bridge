"use client";

import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { api } from "@/lib/api/client";
import type { PermissionGrant, PermissionScope } from "@/types/security";

export interface AppPermissionsResponse {
  grants: PermissionGrant[];
  count: number;
}

export function useApplicationPermissions(appId: string) {
  const queryClient = useQueryClient();

  const permissionsKey = ["applications", appId, "permissions"];

  const permissions = useQuery<AppPermissionsResponse>({
    queryKey: permissionsKey,
    queryFn: () => api.get<AppPermissionsResponse>(`/applications/${appId}/permissions`),
    enabled: !!appId,
    retry: false,
  });

  const grantPermission = useMutation({
    mutationFn: (params: {
      permission: string;
      module_id: string;
      scope: PermissionScope;
      reason?: string;
    }) =>
      api.post(`/applications/${appId}/permissions/grant`, {
        ...params,
        reason: params.reason ?? "",
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: permissionsKey });
    },
  });

  const revokePermission = useMutation({
    mutationFn: (params: { permission: string; module_id: string }) =>
      api.post(`/applications/${appId}/permissions/revoke`, params),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: permissionsKey });
    },
  });

  return { permissions, grantPermission, revokePermission };
}
