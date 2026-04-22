import { LoginPanel } from "@/components/LoginPanel";
import { BrowserCookiePanel } from "@/components/BrowserCookiePanel";
import type { AuthStatus } from "@/lib/api";

async function fetchAuthStatus(): Promise<AuthStatus> {
  // サーバサイドからはローカル FastAPI に直アクセス
  const origin = process.env.KINDLE_WEB_API_ORIGIN ?? "http://127.0.0.1:8001";
  try {
    const res = await fetch(`${origin}/api/auth/status`, { cache: "no-store" });
    if (!res.ok) throw new Error(String(res.status));
    return (await res.json()) as AuthStatus;
  } catch {
    return { authenticated: false, email_hash: null, domain: null };
  }
}

export default async function Page() {
  const status = await fetchAuthStatus();
  return (
    <>
      <LoginPanel initial={status} />
      <BrowserCookiePanel />
    </>
  );
}
