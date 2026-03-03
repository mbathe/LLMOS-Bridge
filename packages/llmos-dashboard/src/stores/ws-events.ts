import { create } from "zustand";
import type { WSMessage } from "@/types/events";

const MAX_EVENTS = 500;

interface WSEventState {
  events: WSMessage[];
  connected: boolean;
  pushEvent: (event: WSMessage) => void;
  setConnected: (connected: boolean) => void;
  clearEvents: () => void;
}

export const useWSEventStore = create<WSEventState>((set) => ({
  events: [],
  connected: false,
  pushEvent: (event) =>
    set((s) => ({
      events: [...s.events, event].slice(-MAX_EVENTS),
    })),
  setConnected: (connected) => set({ connected }),
  clearEvents: () => set({ events: [] }),
}));
