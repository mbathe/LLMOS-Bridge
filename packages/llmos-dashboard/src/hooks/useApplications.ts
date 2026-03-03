"use client";

import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { api } from "@/lib/api/client";
import type {
  ApplicationResponse,
  AgentResponse,
  ApiKeyResponse,
  SessionResponse,
  CreateApplicationRequest,
  UpdateApplicationRequest,
  CreateAgentRequest,
} from "@/types/application";

export function useApplications(refetchInterval = 10000) {
  const queryClient = useQueryClient();

  const invalidateApps = () => {
    queryClient.invalidateQueries({ queryKey: ["applications"] });
  };

  const applications = useQuery<ApplicationResponse[]>({
    queryKey: ["applications"],
    queryFn: () => api.get<ApplicationResponse[]>("/applications", { include_disabled: "true" }),
    refetchInterval,
    retry: false,
  });

  const createApp = useMutation<ApplicationResponse, Error, CreateApplicationRequest>({
    mutationFn: (body) => api.post<ApplicationResponse>("/applications", body),
    onSuccess: invalidateApps,
  });

  const updateApp = useMutation<
    ApplicationResponse,
    Error,
    { appId: string; body: UpdateApplicationRequest }
  >({
    mutationFn: ({ appId, body }) =>
      api.put<ApplicationResponse>(`/applications/${appId}`, body),
    onSuccess: invalidateApps,
  });

  const deleteApp = useMutation<{ detail: string }, Error, { appId: string; hard?: boolean }>({
    mutationFn: ({ appId, hard }) =>
      api.delete<{ detail: string }>(
        `/applications/${appId}${hard ? "?hard=true" : ""}`,
      ),
    onSuccess: invalidateApps,
  });

  return { applications, createApp, updateApp, deleteApp };
}

export function useApplicationDetail(appId: string, refetchInterval = 5000) {
  const queryClient = useQueryClient();

  const invalidateApp = () => {
    queryClient.invalidateQueries({ queryKey: ["applications", appId] });
    queryClient.invalidateQueries({ queryKey: ["applications"] });
  };

  const app = useQuery<ApplicationResponse>({
    queryKey: ["applications", appId],
    queryFn: () => api.get<ApplicationResponse>(`/applications/${appId}`),
    refetchInterval,
    enabled: !!appId,
  });

  const agents = useQuery<AgentResponse[]>({
    queryKey: ["applications", appId, "agents"],
    queryFn: () => api.get<AgentResponse[]>(`/applications/${appId}/agents`),
    refetchInterval,
    enabled: !!appId,
  });

  const sessions = useQuery<SessionResponse[]>({
    queryKey: ["applications", appId, "sessions"],
    queryFn: () => api.get<SessionResponse[]>(`/applications/${appId}/sessions`),
    refetchInterval,
    enabled: !!appId,
  });

  const updateApp = useMutation<ApplicationResponse, Error, UpdateApplicationRequest>({
    mutationFn: (body) => api.put<ApplicationResponse>(`/applications/${appId}`, body),
    onSuccess: invalidateApp,
  });

  const createAgent = useMutation<AgentResponse, Error, CreateAgentRequest>({
    mutationFn: (body) =>
      api.post<AgentResponse>(`/applications/${appId}/agents`, body),
    onSuccess: invalidateApp,
  });

  const deleteAgent = useMutation<{ detail: string }, Error, string>({
    mutationFn: (agentId) =>
      api.delete<{ detail: string }>(`/applications/${appId}/agents/${agentId}`),
    onSuccess: invalidateApp,
  });

  const generateKey = useMutation<ApiKeyResponse, Error, string>({
    mutationFn: (agentId) =>
      api.post<ApiKeyResponse>(`/applications/${appId}/agents/${agentId}/keys`),
  });

  const revokeKey = useMutation<
    { detail: string },
    Error,
    { agentId: string; keyId: string }
  >({
    mutationFn: ({ agentId, keyId }) =>
      api.delete<{ detail: string }>(
        `/applications/${appId}/agents/${agentId}/keys/${keyId}`,
      ),
    onSuccess: invalidateApp,
  });

  return { app, agents, sessions, updateApp, createAgent, deleteAgent, generateKey, revokeKey };
}
