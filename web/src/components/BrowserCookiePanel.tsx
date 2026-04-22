"use client";

import { useState, useTransition } from "react";
import { api, type BrowserCookieResult, type BrowserName } from "@/lib/api";

const browsers: { id: BrowserName; label: string; note: string }[] = [
  { id: "chrome", label: "Chrome", note: "初回は Keychain 許可が必要" },
  { id: "safari", label: "Safari", note: "フルディスクアクセス必須" },
  { id: "firefox", label: "Firefox", note: "権限不要" },
  { id: "edge", label: "Edge", note: "Chrome と同じく Keychain 許可" },
];

export function BrowserCookiePanel() {
  const [result, setResult] = useState<BrowserCookieResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [pending, startTransition] = useTransition();

  const run = (browser: BrowserName) => {
    setError(null);
    startTransition(async () => {
      try {
        const r = await api.extractCookies(browser);
        setResult(r);
      } catch (err) {
        const message = err instanceof Error ? err.message : String(err);
        setError(message);
      }
    });
  };

  return (
    <div className="card stack">
      <div className="stack">
        <div style={{ fontWeight: 600 }}>
          ブラウザから amazon.co.jp の Cookie を取得
        </div>
        <div className="muted" style={{ fontSize: 12 }}>
          ログイン済みブラウザのセッション Cookie を読み取り、サーバのメモリに保持します。
          Cookie 値自体は UI には表示されません。
          ログインを補助する参考用途で、必須ではありません。
        </div>
      </div>

      <div className="row">
        {browsers.map((b) => (
          <button
            key={b.id}
            onClick={() => run(b.id)}
            disabled={pending}
            title={b.note}
          >
            {b.label}
          </button>
        ))}
      </div>

      {result && (
        <div className="muted" style={{ fontSize: 13 }}>
          <span className="inline-code">{result.browser}</span> から{" "}
          <strong>{result.count}</strong> 件取得。
          {result.has_session_id ? (
            <span className="success"> セッション Cookie 検出</span>
          ) : (
            <span className="error">
              {" "}
              セッション Cookie が見つかりませんでした（未ログインの可能性）
            </span>
          )}
        </div>
      )}

      {error && <div className="error">{error}</div>}
    </div>
  );
}
