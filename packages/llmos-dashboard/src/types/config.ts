export interface TriggerInfo {
  trigger_id: string;
  name: string;
  description: string;
  trigger_type: string;
  state: string;
  enabled: boolean;
  fire_count: number;
  fail_count: number;
  throttle_count: number;
  created_at: string;
  tags: string[];
}

export interface RecordingInfo {
  recording_id: string;
  title: string;
  description: string;
  status: "active" | "stopped" | "replaying";
  created_at: string;
  plan_count: number;
}
