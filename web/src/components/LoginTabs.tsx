"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { LoginPanel } from "@/components/LoginPanel";
import { CookieLoginPanel } from "@/components/CookieLoginPanel";
import { BrowserCookiePanel } from "@/components/BrowserCookiePanel";
import type { AuthStatus } from "@/lib/api";

type Tab = "cookie" | "email";

type Props = { initial: AuthStatus };

export function LoginTabs({ initial }: Props) {
  const router = useRouter();
  const [tab, setTab] = useState<Tab>("cookie");

  const onCookieSuccess = (_next: AuthStatus) => {
    // Server Component を再取得して、ログイン済みカードに切り替わるようにする
    router.refresh();
  };

  return (
    <div className="stack">
      <div className="card" style={{ paddingBottom: 8 }}>
        <div className="row" style={{ gap: 6, flexWrap: "wrap" }}>
          <button
            type="button"
            className={tab === "cookie" ? "primary" : "ghost"}
            onClick={() => setTab("cookie")}
          >
            Cookie + 端末シリアル（推奨）
          </button>
          <button
            type="button"
            className={tab === "email" ? "primary" : "ghost"}
            onClick={() => setTab("email")}
          >
            Email / Password
          </button>
        </div>
        <div className="muted" style={{ fontSize: 12, marginTop: 8 }}>
          amazon.co.jp の認証 API は OTP/Throttle で失敗しやすいので、ブラウザに残っている
          ログイン済み Cookie を流用する経路を推奨します。端末シリアル (DSN) は
          <span className="inline-code">amazon.co.jp/hz/mycd</span> の「端末」から確認できます。
        </div>
      </div>

      {tab === "cookie" ? (
        <CookieLoginPanel onSuccess={onCookieSuccess} />
      ) : (
        <LoginPanel initial={initial} />
      )}

      <BrowserCookiePanel />
    </div>
  );
}
