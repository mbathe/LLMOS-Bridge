const TOKEN_KEY = "llmos_token";
const DAEMON_URL_KEY = "llmos_daemon_url";

export function setToken(token: string): void {
  sessionStorage.setItem(TOKEN_KEY, token);
}

export function getToken(): string | null {
  if (typeof window === "undefined") return null;
  return sessionStorage.getItem(TOKEN_KEY);
}

export function clearToken(): void {
  sessionStorage.removeItem(TOKEN_KEY);
}

export function setDaemonUrl(url: string): void {
  localStorage.setItem(DAEMON_URL_KEY, url);
}

export function getDaemonUrl(): string {
  if (typeof window === "undefined") return "http://localhost:40000";
  return (
    localStorage.getItem(DAEMON_URL_KEY) ??
    process.env.NEXT_PUBLIC_DAEMON_URL ??
    "http://localhost:40000"
  );
}

export function isAuthenticated(): boolean {
  return getToken() !== null;
}
