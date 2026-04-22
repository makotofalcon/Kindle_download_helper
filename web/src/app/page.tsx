import { LoginPanel } from "@/components/LoginPanel";
import { CookieLoginPanel } from "@/components/CookieLoginPanel";
import { LoginTabs } from "@/components/LoginTabs";
import type { AuthStatus } from "@/lib/api";

async function fetchAuthStatus(): Promise<AuthStatus> {
  // サーバサイドからはローカル FastAPI に直アクセス
  const origin = process.env.KINDLE_WEB_API_ORIGIN ?? "http://127.0.0.1:8001";
  try {
    const res = await fetch(`${origin}/api/auth/status`, { cache: "no-store" });
    if (!res.ok) throw new Error(String(res.status));
    return (await res.json()) as AuthStatus;
  } catch {
    return {
      authenticated: false,
      mode: null,
      email_hash: null,
      domain: null,
      device_sn_tail: null,
    };
  }
}

export default async function Page() {
  const status = await fetchAuthStatus();
  if (status.authenticated) {
    // ログイン済みなら LoginPanel がログアウトボタン付きの success カードを描画
    return <LoginPanel initial={status} />;
  }
  return <LoginTabs initial={status} />;
}
