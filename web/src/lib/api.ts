/**
 * FastAPI バックエンドを叩く共通クライアント。
 * `next.config.ts` の rewrites によって `/api/*` がバックエンドへフォワードされる前提。
 */

export interface AuthStatus {
  authenticated: boolean;
  message: string | null;
}

export interface BookItem {
  asin: string;
  title: string;
  authors: string;
  acquired_at: string | null;
  content_type: string | null;
}

export interface BookList {
  books: BookItem[];
  fetched_at: string;
}

export type DownloadStatus =
  | "queued"
  | "running"
  | "success"
  | "failed"
  | "skipped";

export interface DownloadProgress {
  asin: string;
  title: string;
  status: DownloadStatus;
  message: string | null;
  output_path: string | null;
}

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
  authStatus: (): Promise<AuthStatus> => jsonFetch<AuthStatus>("/api/auth/status"),
  logout: (): Promise<{ ok: boolean }> =>
    jsonFetch<{ ok: boolean }>("/api/auth/logout", { method: "POST" }),
  captureEnsureLogin: (): Promise<AuthStatus> =>
    jsonFetch<AuthStatus>("/api/capture/ensure-login", { method: "POST" }),
  books: (refresh = false): Promise<BookList> =>
    jsonFetch<BookList>(`/api/books${refresh ? "?refresh=true" : ""}`),
  startDownload: (
    asins: string[],
  ): Promise<{ ok: boolean; total: number }> =>
    jsonFetch<{ ok: boolean; total: number }>("/api/downloads", {
      method: "POST",
      headers: jsonHeaders,
      body: JSON.stringify({ asins }),
    }),
  revealOutput: (): Promise<{ path: string }> =>
    jsonFetch<{ path: string }>("/api/output/reveal"),
};
