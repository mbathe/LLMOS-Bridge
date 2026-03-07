"use client";

import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { api } from "@/lib/api/client";
import type {
  CategoriesResponse,
  PublisherProfile,
  RatingsResponse,
  HubSecurityInfo,
} from "@/types/module";

export function useHubEcosystem() {
  const queryClient = useQueryClient();

  // ── Categories ──

  const categories = useQuery<CategoriesResponse>({
    queryKey: ["hub-categories"],
    queryFn: () => api.get<CategoriesResponse>("/admin/hub/categories"),
    retry: false,
    staleTime: 60_000,
  });

  // ── On-demand fetchers ──

  const getPublisher = (publisherId: string) =>
    api.get<PublisherProfile>(`/admin/hub/publishers/${publisherId}`);

  const getRatings = (moduleId: string) =>
    api.get<RatingsResponse>(`/admin/hub/modules/${moduleId}/ratings`);

  const getSecurity = (moduleId: string) =>
    api.get<HubSecurityInfo>(`/admin/hub/modules/${moduleId}/security`);

  // ── Rate mutation ──

  const rateModule = useMutation<
    { success: boolean },
    Error,
    { moduleId: string; stars: number; comment?: string }
  >({
    mutationFn: ({ moduleId, stars, comment }) =>
      api.post(`/admin/hub/modules/${moduleId}/rate`, { stars, comment }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["hub-search"] });
    },
  });

  return {
    categories,
    getPublisher,
    getRatings,
    getSecurity,
    rateModule,
  };
}
