"use client";

import { useQuery } from "@tanstack/react-query";
import { api } from "@/lib/api/client";
import type { HealthResponse } from "@/types/events";

export function useDaemonHealth(refetchInterval = 10000) {
  return useQuery<HealthResponse>({
    queryKey: ["health"],
    queryFn: () => api.get<HealthResponse>("/health"),
    refetchInterval,
    retry: 2,
  });
}
