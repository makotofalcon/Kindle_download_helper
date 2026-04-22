/**
 * FastAPI バックエンドを叩く共通クライアント。
 * `next.config.ts` の rewrites によって `/api/*` がバックエンドへフォワードされる前提。
 */

export type AuthStatus = {
  authenticated: boolean;
  email_hash: string | null;
  domain: string | null;
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

async function jsonFetch<T>(path: string, init: RequestInit = {}): Promise<T> {
  const res = await fetch(path, {
    cache: "no-store",
    ...init,
  });
  if (!res.ok) {
    const msg = await res.text().catch(() => res.statusText);
    throw new Error(msg || `${res.status}`);
  }
  return (await res.json()) as T;
}

export const api = {
  authStatus: () => jsonFetch<AuthStatus>("/api/auth/status"),
  login: (email: string, password: string) =>
    jsonFetch<AuthStatus>("/api/auth/login", {
      method: "POST",
      headers: jsonHeaders,
      body: JSON.stringify({ email, password }),
    }),
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
};
