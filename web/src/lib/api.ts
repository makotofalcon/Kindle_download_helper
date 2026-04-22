/**
 * FastAPI バックエンドを叩く共通クライアント。
 * `next.config.ts` の rewrites によって `/api/*` がバックエンドへフォワードされる前提。
 */

export type AuthStatus = {
  authenticated: boolean;
  mode: "nokindle" | "cookie" | null;
  email_hash: string | null;
  domain: string | null;
  device_sn_tail: string | null;
};

export type BookItem = {
  asin: string;
  title: string;
  authors: string;
  acquired_at: string | null;
  content_type: string | null;
};

export type BookList = {
  books: BookItem[];
  fetched_at: string;
};

export type BrowserName = "chrome" | "safari" | "firefox" | "edge";

export type KindleDevice = {
  deviceSerialNumber: string;
  deviceType: string;
  deviceName: string;
  deviceAccountId: string;
};

export type BrowserCookieResult = {
  browser: BrowserName;
  domain: string;
  count: number;
  has_session_id: boolean;
};

export type DownloadStatus =
  | "queued"
  | "running"
  | "success"
  | "failed"
  | "skipped";

export type DownloadProgress = {
  asin: string;
  title: string;
  status: DownloadStatus;
  message: string | null;
  output_path: string | null;
};

const jsonHeaders = { "Content-Type": "application/json" };

export class ApiError extends Error {
  status: number;
  body: unknown;
  constructor(status: number, message: string, body: unknown) {
    super(message);
    this.status = status;
    this.body = body;
  }
}

async function jsonFetch<T>(path: string, init: RequestInit = {}): Promise<T> {
  const res = await fetch(path, {
    cache: "no-store",
    ...init,
  });
  if (!res.ok) {
    // FastAPI の detail + amazon_response を取り出せるようにする
    let body: unknown = null;
    let message = res.statusText;
    try {
      body = await res.clone().json();
      if (body && typeof body === "object" && "detail" in body) {
        message = String((body as { detail: unknown }).detail);
      }
    } catch {
      message = await res.text().catch(() => res.statusText);
    }
    throw new ApiError(res.status, message || `${res.status}`, body);
  }
  return (await res.json()) as T;
}

export const api = {
  authStatus: () => jsonFetch<AuthStatus>("/api/auth/status"),
  login: (email: string, password: string, otp_code?: string) =>
    jsonFetch<AuthStatus>("/api/auth/login", {
      method: "POST",
      headers: jsonHeaders,
      body: JSON.stringify({
        email,
        password,
        otp_code: otp_code?.trim() || null,
      }),
    }),
  cookieLogin: (browser: BrowserName, device_sn: string) =>
    jsonFetch<AuthStatus>("/api/auth/cookie-login", {
      method: "POST",
      headers: jsonHeaders,
      body: JSON.stringify({ browser, device_sn }),
    }),
  listDevices: () =>
    jsonFetch<{ devices: KindleDevice[] }>("/api/kindle/devices"),
  logout: () =>
    jsonFetch<{ ok: boolean }>("/api/auth/logout", { method: "POST" }),
  restore: () =>
    jsonFetch<AuthStatus>("/api/auth/restore", { method: "POST" }),
  extractCookies: (browser: BrowserName) =>
    jsonFetch<BrowserCookieResult>(`/api/cookies/${browser}`, {
      method: "POST",
    }),
  books: (refresh = false) =>
    jsonFetch<BookList>(`/api/books${refresh ? "?refresh=true" : ""}`),
  startDownload: (asins: string[]) =>
    jsonFetch<{ ok: boolean; total: number }>("/api/downloads", {
      method: "POST",
      headers: jsonHeaders,
      body: JSON.stringify({ asins }),
    }),
  revealOutput: () =>
    jsonFetch<{ path: string }>("/api/output/reveal"),
  captureEnsureLogin: () =>
    jsonFetch<{ authenticated: boolean; message: string }>(
      "/api/capture/ensure-login",
      { method: "POST" },
    ),
};
