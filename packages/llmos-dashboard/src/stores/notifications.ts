import { create } from "zustand";
import type { ApprovalRequest } from "@/types/plan";

interface Notification {
  id: string;
  type: "info" | "success" | "warning" | "error";
  title: string;
  message: string;
  timestamp: number;
}

interface NotificationState {
  notifications: Notification[];
  pendingApprovals: ApprovalRequest[];
  addNotification: (n: Omit<Notification, "id" | "timestamp">) => void;
  removeNotification: (id: string) => void;
  clearAll: () => void;
  setPendingApprovals: (approvals: ApprovalRequest[]) => void;
  addPendingApproval: (approval: ApprovalRequest) => void;
  removePendingApproval: (planId: string, actionId: string) => void;
}

let notifId = 0;

export const useNotificationStore = create<NotificationState>((set) => ({
  notifications: [],
  pendingApprovals: [],
  addNotification: (n) =>
    set((s) => ({
      notifications: [
        ...s.notifications,
        { ...n, id: String(++notifId), timestamp: Date.now() },
      ].slice(-50),
    })),
  removeNotification: (id) =>
    set((s) => ({
      notifications: s.notifications.filter((n) => n.id !== id),
    })),
  clearAll: () => set({ notifications: [] }),
  setPendingApprovals: (approvals) => set({ pendingApprovals: approvals }),
  addPendingApproval: (approval) =>
    set((s) => ({
      pendingApprovals: [...s.pendingApprovals, approval],
    })),
  removePendingApproval: (planId, actionId) =>
    set((s) => ({
      pendingApprovals: s.pendingApprovals.filter(
        (a) => !(a.plan_id === planId && a.action_id === actionId),
      ),
    })),
}));
