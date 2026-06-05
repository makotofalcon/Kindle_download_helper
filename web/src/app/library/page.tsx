import { LibraryTable } from "@/components/LibraryTable";
import type { AuthStatus, BookList } from "@/lib/api";

const ORIGIN = process.env.KINDLE_WEB_API_ORIGIN ?? "http://127.0.0.1:8001";

async function fetchAuth(): Promise<AuthStatus> {
  try {
    const res = await fetch(`${ORIGIN}/api/auth/status`, { cache: "no-store" });
    if (!res.ok) throw new Error(String(res.status));
    return (await res.json()) as AuthStatus;
  } catch {
    return { authenticated: false, message: null };
  }
}

async function fetchBooks(): Promise<{
  data: BookList | null;
  error: string | null;
}> {
  try {
    const res = await fetch(`${ORIGIN}/api/books`, { cache: "no-store" });
    if (!res.ok) {
      const msg = await res.text().catch(() => "");
      return { data: null, error: msg || `HTTP ${res.status}` };
    }
    return { data: (await res.json()) as BookList, error: null };
  } catch (err) {
    return {
      data: null,
      error: err instanceof Error ? err.message : String(err),
    };
  }
}

export const dynamic = "force-dynamic";

export default async function LibraryPage() {
  const auth = await fetchAuth();
  if (!auth.authenticated) {
    return (
      <div className="card stack">
        <div className="error">Cloud Reader にログインしていません。</div>
        <div className="muted">
          トップページの「ログイン確認 / ブラウザを開く」から amazon.co.jp に
          ログインしてください。
        </div>
      </div>
    );
  }

  const { data, error } = await fetchBooks();
  if (error || !data) {
    return (
      <div className="card stack">
        <div className="error">蔵書取得に失敗しました。</div>
        <pre className="muted" style={{ whiteSpace: "pre-wrap" }}>
          {error}
        </pre>
      </div>
    );
  }

  return (
    <div className="card">
      <LibraryTable books={data.books} />
    </div>
  );
}
