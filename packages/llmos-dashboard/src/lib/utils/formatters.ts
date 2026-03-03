import dayjs from "dayjs";
import relativeTime from "dayjs/plugin/relativeTime";
import duration from "dayjs/plugin/duration";

dayjs.extend(relativeTime);
dayjs.extend(duration);

export function formatUptime(seconds: number): string {
  const d = dayjs.duration(seconds, "seconds");
  const days = Math.floor(d.asDays());
  const hours = d.hours();
  const mins = d.minutes();
  if (days > 0) return `${days}d ${hours}h ${mins}m`;
  if (hours > 0) return `${hours}h ${mins}m`;
  return `${mins}m ${d.seconds()}s`;
}

export function timeAgo(dateStr: string | number): string {
  if (typeof dateStr === "number") {
    return dayjs.unix(dateStr).fromNow();
  }
  return dayjs(dateStr).fromNow();
}

export function formatDate(dateStr: string | number): string {
  if (typeof dateStr === "number") {
    return dayjs.unix(dateStr).format("YYYY-MM-DD HH:mm:ss");
  }
  return dayjs(dateStr).format("YYYY-MM-DD HH:mm:ss");
}

export function formatTimestamp(ts: number): string {
  return dayjs.unix(ts).format("YYYY-MM-DD HH:mm:ss");
}

export function formatBytes(bytes: number): string {
  if (bytes === 0) return "0 B";
  const k = 1024;
  const sizes = ["B", "KB", "MB", "GB", "TB"];
  const i = Math.floor(Math.log(bytes) / Math.log(k));
  return `${parseFloat((bytes / Math.pow(k, i)).toFixed(1))} ${sizes[i]}`;
}

export function truncateId(id: string, length = 8): string {
  if (id.length <= length) return id;
  return `${id.slice(0, length)}...`;
}
