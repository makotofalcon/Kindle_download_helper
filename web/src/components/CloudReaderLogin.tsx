"use client";

import { useState, useTransition } from "react";
import { ApiError, api } from "@/lib/api";

export function CloudReaderLogin() {
  const [state, setState] = useState<{
    authenticated: boolean;
    message: string;
  } | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [pending, startTransition] = useTransition();

  const check = () => {
    setError(null);
    startTransition(async () => {
      try {
        const r = await api.captureEnsureLogin();
        setState(r);
      } catch (err) {
        if (err instanceof ApiError) {
          setError(err.message);
        } else {
          setError(err instanceof Error ? err.message : String(err));
        }
      }
    });
  };

  return (
    <div className="card stack">
      <div style={{ fontWeight: 600 }}>
        Kindle Cloud Reader ログイン確認
      </div>
      <div className="muted" style={{ fontSize: 12 }}>
        Playwright が管理する専用 Chromium を起動して
        <span className="inline-code">read.amazon.co.jp/kindle-library</span>
        を開きます。未ログインならそのウィンドウで Amazon
        ログイン（OTP含む）を完了させ、もう一度ここで「ログイン確認」を押してください。
        セッションは
        <span className="inline-code">~/.kindle-web/chrome-profile/</span>
        に保持されます。
      </div>
      <div className="row">
        <button className="primary" onClick={check} disabled={pending}>
          {pending ? "確認中..." : "ログイン確認 / ブラウザを開く"}
        </button>
        {state && (
          <span className={state.authenticated ? "success" : "muted"}>
            {state.authenticated ? "✓ " : "… "}
            {state.message}
          </span>
        )}
      </div>
      {error && <div className="error">{error}</div>}
    </div>
  );
}
