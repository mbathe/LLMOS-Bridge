"use client";

import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { api } from "@/lib/api/client";
import type {
  ClusterResponse,
  ClusterHealthResponse,
  NodeResponse,
  NodeRegisterRequest,
} from "@/types/cluster";
import type { SystemConfig } from "@/types/events";

export interface RoutingConfig {
  strategy: string;
  node_fallback_enabled: boolean;
  max_node_retries: number;
  quarantine_threshold: number;
  quarantine_duration: number;
  module_affinity: Record<string, string>;
}

export function useCluster(refetchInterval = 10000) {
  const queryClient = useQueryClient();

  const clusterInfo = useQuery<ClusterResponse>({
    queryKey: ["cluster", "info"],
    queryFn: () => api.get<ClusterResponse>("/cluster"),
  });

  const routingConfig = useQuery<RoutingConfig | null>({
    queryKey: ["cluster", "routing"],
    queryFn: async () => {
      const config = await api.get<SystemConfig>("/admin/system/config");
      const routing = config?.routing as unknown as RoutingConfig | undefined;
      return routing ?? null;
    },
    retry: false,
  });

  const clusterHealth = useQuery<ClusterHealthResponse>({
    queryKey: ["cluster", "health"],
    queryFn: () => api.get<ClusterHealthResponse>("/cluster/health"),
    refetchInterval,
  });

  const nodes = useQuery<NodeResponse[]>({
    queryKey: ["cluster", "nodes"],
    queryFn: () => api.get<NodeResponse[]>("/nodes"),
    refetchInterval,
  });

  const invalidateCluster = () => {
    queryClient.invalidateQueries({ queryKey: ["cluster"] });
  };

  const registerNode = useMutation<NodeResponse, Error, NodeRegisterRequest>({
    mutationFn: (body) => api.post<NodeResponse>("/nodes", body),
    onSuccess: invalidateCluster,
  });

  const unregisterNode = useMutation<{ detail: string }, Error, string>({
    mutationFn: (nodeId) => api.delete<{ detail: string }>(`/nodes/${nodeId}`),
    onSuccess: invalidateCluster,
  });

  const triggerHeartbeat = useMutation<
    { node_id: string; health: Record<string, unknown> },
    Error,
    string
  >({
    mutationFn: (nodeId) =>
      api.post<{ node_id: string; health: Record<string, unknown> }>(
        `/nodes/${nodeId}/heartbeat`,
      ),
    onSuccess: invalidateCluster,
  });

  return {
    clusterInfo,
    clusterHealth,
    nodes,
    routingConfig,
    registerNode,
    unregisterNode,
    triggerHeartbeat,
  };
}

export function useNodeDetail(nodeId: string, refetchInterval = 5000) {
  return useQuery<NodeResponse>({
    queryKey: ["cluster", "node", nodeId],
    queryFn: () => api.get<NodeResponse>(`/nodes/${nodeId}`),
    refetchInterval,
    enabled: !!nodeId,
  });
}
