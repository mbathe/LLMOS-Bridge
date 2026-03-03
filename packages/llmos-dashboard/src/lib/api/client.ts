import type { ErrorResponse } from "@/types/events";

const DAEMON_URL =
  process.env.NEXT_PUBLIC_DAEMON_URL ?? "http://localhost:40000";

export class ApiError extends Error {
  code: string;
  detail: string | null;
  requestId: string;

  constructor(resp: ErrorResponse, status: number) {
    super(resp.error);
    this.name = "ApiError";
    this.code = resp.code;
    this.detail = resp.detail;
    this.requestId = resp.request_id;
  }
}

function getToken(): string | null {
  if (typeof window === "undefined") return null;
  return sessionStorage.getItem("llmos_token");
}

function headers(): HeadersInit {
  const h: Record<string, string> = {
    "Content-Type": "application/json",
  };
  const token = getToken();
  if (token) h["X-LLMOS-Token"] = token;
  return h;
}

async function handleResponse<T>(res: Response): Promise<T> {
  if (!res.ok) {
    let body: ErrorResponse;
    try {
      body = await res.json();
    } catch {
      throw new ApiError(
        {
          error: res.statusText,
          code: `HTTP_${res.status}`,
          detail: null,
          request_id: "",
        },
        res.status,
      );
    }
    throw new ApiError(body, res.status);
  }
  if (res.status === 204) return undefined as T;
  return res.json();
}

export const api = {
  get<T>(path: string, params?: Record<string, string>): Promise<T> {
    const url = new URL(path, DAEMON_URL);
    if (params) {
      Object.entries(params).forEach(([k, v]) => {
        if (v !== undefined && v !== null && v !== "") {
          url.searchParams.set(k, v);
        }
      });
    }
    return fetch(url.toString(), { headers: headers() }).then(handleResponse<T>);
  },

  post<T>(path: string, body?: unknown): Promise<T> {
    return fetch(new URL(path, DAEMON_URL).toString(), {
      method: "POST",
      headers: headers(),
      body: body ? JSON.stringify(body) : undefined,
    }).then(handleResponse<T>);
  },

  put<T>(path: string, body?: unknown): Promise<T> {
    return fetch(new URL(path, DAEMON_URL).toString(), {
      method: "PUT",
      headers: headers(),
      body: body ? JSON.stringify(body) : undefined,
    }).then(handleResponse<T>);
  },

  delete<T>(path: string): Promise<T> {
    return fetch(new URL(path, DAEMON_URL).toString(), {
      method: "DELETE",
      headers: headers(),
    }).then(handleResponse<T>);
  },

  daemonUrl: DAEMON_URL,
};
