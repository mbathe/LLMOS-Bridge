export interface ApplicationResponse {
  app_id: string;
  name: string;
  description: string;
  created_at: number;
  updated_at: number;
  enabled: boolean;
  max_concurrent_plans: number;
  max_actions_per_plan: number;
  allowed_modules: string[];
  allowed_actions: Record<string, string[]>;
  tags: Record<string, string>;
  agent_count: number;
  session_count: number;
}

export interface AgentResponse {
  agent_id: string;
  name: string;
  app_id: string;
  role: string;
  created_at: number;
  enabled: boolean;
}

export interface ApiKeyResponse {
  key_id: string;
  prefix: string;
  api_key: string | null;
  created_at: number;
  expires_at: number | null;
}

export interface SessionResponse {
  session_id: string;
  app_id: string;
  agent_id: string | null;
  created_at: number;
  last_active: number;
  expires_at: number | null;
  idle_timeout_seconds: number | null;
  allowed_modules: string[];
  permission_grants: string[];
  permission_denials: string[];
  expired: boolean;
}

export interface CreateSessionRequest {
  agent_id?: string | null;
  expires_in_seconds?: number | null;
  idle_timeout_seconds?: number | null;
  allowed_modules?: string[];
  permission_grants?: string[];
  permission_denials?: string[];
}

export interface CreateApplicationRequest {
  name: string;
  description?: string;
  max_concurrent_plans?: number;
  max_actions_per_plan?: number;
  allowed_modules?: string[];
  allowed_actions?: Record<string, string[]>;
  tags?: Record<string, string>;
}

export interface UpdateApplicationRequest {
  name?: string;
  description?: string;
  enabled?: boolean;
  max_concurrent_plans?: number;
  max_actions_per_plan?: number;
  allowed_modules?: string[];
  allowed_actions?: Record<string, string[]>;
  tags?: Record<string, string>;
}

export interface CreateAgentRequest {
  name: string;
  role?: string;
}
