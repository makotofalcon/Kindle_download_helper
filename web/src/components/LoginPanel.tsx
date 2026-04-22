"use client";

import { useState, useTransition } from "react";
import { useRouter } from "next/navigation";
import { ApiError, api, type AuthStatus } from "@/lib/api";

type Props = { initial: AuthStatus };

export function LoginPanel({ initial }: Props) {
  const router = useRouter();
  const [status, setStatus] = useState<AuthStatus>(initial);
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [otpCode, setOtpCode] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [rawAmazon, setRawAmazon] = useState<unknown>(null);
  const [pending, startTransition] = useTransition();

  const onLogin = (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);
    setRawAmazon(null);
    startTransition(async () => {
      try {
        const next = await api.login(email, password, otpCode);
        setStatus(next);
        setPassword("");
        setOtpCode("");
        router.refresh();
      } catch (err) {
        if (err instanceof ApiError) {
          setError(err.message);
          const body = err.body as { amazon_response?: unknown } | null;
          setRawAmazon(body?.amazon_response ?? null);
        } else {
          setError(err instanceof Error ? err.message : String(err));
        }
      }
    });
  };

  const onLogout = () => {
    startTransition(async () => {
      await api.logout();
      setStatus({
        authenticated: false,
        mode: null,
        email_hash: null,
        domain: null,
        device_sn_tail: null,
      });
      router.refresh();
    });
  };

  if (status.authenticated) {
    const modeLabel =
      status.mode === "cookie"
        ? `Cookie+DSN (…${status.device_sn_tail ?? "????"})`
        : `email/password (id:${status.email_hash ?? "-"})`;
    return (
      <div className="card">
        <div className="row" style={{ justifyContent: "space-between" }}>
          <div className="stack">
            <div className="success">
              ログイン済み（amazon.{status.domain ?? "co.jp"} / {modeLabel}）
            </div>
            <div className="muted">
              蔵書タブで本を選んでダウンロードできます。
            </div>
          </div>
          <button onClick={onLogout} disabled={pending} className="ghost">
            ログアウト
          </button>
        </div>
      </div>
    );
  }

  return (
    <form className="card stack" onSubmit={onLogin}>
      <div className="stack">
        <label>
          <div className="muted">メールアドレス（amazon.co.jp）</div>
          <input
            type="email"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            autoComplete="username"
            required
          />
        </label>
        <label>
          <div className="muted">パスワード</div>
          <input
            type="password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            autoComplete="current-password"
            required
          />
        </label>
        <label>
          <div className="muted">
            2段階認証コード <span style={{ fontSize: 11 }}>(OTPが有効な場合のみ・6桁)</span>
          </div>
          <input
            type="text"
            value={otpCode}
            onChange={(e) => setOtpCode(e.target.value.replace(/[^0-9]/g, "").slice(0, 6))}
            inputMode="numeric"
            autoComplete="one-time-code"
            placeholder="例: 123456"
            maxLength={6}
          />
        </label>
      </div>

      <div className="row" style={{ justifyContent: "space-between" }}>
        <div className="muted" style={{ fontSize: 12 }}>
          パスワードはローカルのFastAPIに送信されるのみで、保存されません。
          セッション（リフレッシュトークン）のみ <span className="inline-code">~/.kindle_download_helper</span> に保存されます。
        </div>
        <button className="primary" type="submit" disabled={pending}>
          {pending ? "認証中..." : "ログイン"}
        </button>
      </div>

      {error && <div className="error">{error}</div>}
      {rawAmazon !== null && (
        <details>
          <summary className="muted" style={{ cursor: "pointer" }}>
            Amazon の生レスポンスを表示（診断用）
          </summary>
          <pre
            style={{
              marginTop: 8,
              padding: 10,
              background: "var(--surface-2)",
              borderRadius: 6,
              maxHeight: 300,
              overflow: "auto",
              fontSize: 11,
            }}
          >
            {JSON.stringify(rawAmazon, null, 2)}
          </pre>
        </details>
      )}

      <div className="warning">
        2段階認証(OTP)が有効なアカウントは、認証アプリ/SMSの6桁コードを上のOTP欄に入れてください。
        サーバ側で <span className="inline-code">password + otp_code</span> を連結して Amazon の register API に送ります。
      </div>
    </form>
  );
}
