"use client";

import { useState, useTransition } from "react";
import { useRouter } from "next/navigation";
import { api, type AuthStatus } from "@/lib/api";

type Props = { initial: AuthStatus };

export function LoginPanel({ initial }: Props) {
  const router = useRouter();
  const [status, setStatus] = useState<AuthStatus>(initial);
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [pending, startTransition] = useTransition();

  const onLogin = (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);
    startTransition(async () => {
      try {
        const next = await api.login(email, password);
        setStatus(next);
        setPassword("");
        router.refresh();
      } catch (err) {
        const message = err instanceof Error ? err.message : String(err);
        setError(message);
      }
    });
  };

  const onLogout = () => {
    startTransition(async () => {
      await api.logout();
      setStatus({ authenticated: false, email_hash: null, domain: null });
      router.refresh();
    });
  };

  if (status.authenticated) {
    return (
      <div className="card">
        <div className="row" style={{ justifyContent: "space-between" }}>
          <div className="stack">
            <div className="success">
              ログイン済み（amazon.{status.domain}・id:{status.email_hash}）
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

      <div className="warning">
        2段階認証(OTP)が有効なアカウントは、この経路では通過できません。
        その場合は Amazon 側で一時的に無効化するか、端末認証済みの状態を別途ご用意ください。
      </div>
    </form>
  );
}
