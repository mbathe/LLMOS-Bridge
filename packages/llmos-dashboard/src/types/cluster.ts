export interface NodeResponse {
  node_id: string;
  url: string | null;
  location: string;
  available: boolean;
  last_heartbeat: number | null;
  modules: string[];
  is_local: boolean;
  latency_ms: number | null;
  active_actions: number;
  quarantined: boolean;
}

export interface ClusterResponse {
  cluster_id: string;
  cluster_name: string;
  node_id: string;
  mode: string;
  app_count: number;
  identity_enabled: boolean;
}

export interface ClusterHealthResponse {
  total_nodes: number;
  available_nodes: number;
  unavailable_nodes: number;
  nodes: NodeResponse[];
}

export interface NodeRegisterRequest {
  node_id: string;
  url: string;
  api_token?: string;
  location?: string;
}
