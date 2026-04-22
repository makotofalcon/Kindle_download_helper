"use client";

import { useState, useTransition } from "react";
import { useRouter } from "next/navigation";
import {
  ApiError,
  api,
  type AuthStatus,
  type BrowserName,
  type KindleDevice,
} from "@/lib/api";

const BROWSERS: { id: BrowserName; label: string; note: string }[] = [
  { id: "chrome", label: "Chrome", note: "初回は Keychain 許可が必要" },
  { id: "safari", label: "Safari", note: "フルディスクアクセス必須" },
  { id: "firefox", label: "Firefox", note: "権限不要" },
  { id: "edge", label: "Edge", note: "Keychain 許可が必要" },
];

type Props = {
  onSuccess: (next: AuthStatus) => void;
};

export function CookieLoginPanel({ onSuccess }: Props) {
  const router = useRouter();
  const [browser, setBrowser] = useState<BrowserName>("chrome");
  const [deviceSn, setDeviceSn] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [pending, startTransition] = useTransition();
  const [devices, setDevices] = useState<KindleDevice[] | null>(null);
  const [loadingDevices, startLoadingDevices] = useTransition();

  const fetchDevices = () => {
    setError(null);
    startLoadingDevices(async () => {
      try {
        const r = await api.listDevices();
        setDevices(r.devices);
      } catch (err) {
        const message =
          err instanceof Error ? err.message : String(err);
        setError(
          `端末一覧の取得に失敗: ${message}。仮ログイン（任意のDSNでログインボタン）後に再度お試しください。`,
        );
      }
    });
  };

  // 物理 Kindle は DSN が B0 で始まる or 16文字英数。アプリは32文字hex。
  const isPhysical = (sn: string) =>
    /^[A-Z0-9]{16}$/.test(sn) && !/^[a-f0-9]{16}$/.test(sn);

  const onSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);
    const normalized = deviceSn.trim().toUpperCase();
    if (!normalized) {
      setError("端末シリアル番号 (DSN) を入力してください。");
      return;
    }
    startTransition(async () => {
      try {
        const next = await api.cookieLogin(browser, normalized);
        onSuccess(next);
        router.refresh();
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
    <form className="card stack" onSubmit={onSubmit}>
      <div className="stack">
        <div style={{ fontWeight: 600 }}>
          ブラウザ Cookie + 端末シリアル番号でログイン（推奨）
        </div>
        <div className="muted" style={{ fontSize: 12 }}>
          amazon.co.jp に既にログイン済みのブラウザを選び、
          「コンテンツと端末の管理 → 端末」から取れる DSN を入力してください。
          Amazon の認証 API を叩かないのでスロットルや OTP の影響を受けません。
        </div>
      </div>

      <label className="stack">
        <div className="muted">対象ブラウザ</div>
        <div className="row">
          {BROWSERS.map((b) => (
            <label key={b.id} className="row" style={{ gap: 6 }} title={b.note}>
              <input
                type="radio"
                name="browser"
                value={b.id}
                checked={browser === b.id}
                onChange={() => setBrowser(b.id)}
              />
              <span>{b.label}</span>
            </label>
          ))}
        </div>
      </label>

      <label>
        <div className="muted">
          端末シリアル番号 (DSN) <span style={{ fontSize: 11 }}>16文字・B0 で始まる</span>
        </div>
        <input
          type="text"
          value={deviceSn}
          onChange={(e) =>
            setDeviceSn(e.target.value.replace(/\s+/g, "").toUpperCase().slice(0, 16))
          }
          placeholder="例: B00AXXXXXXXXXXXX"
          spellCheck={false}
          autoCapitalize="characters"
          maxLength={16}
        />
      </label>

      <div className="stack">
        <button
          type="button"
          onClick={fetchDevices}
          disabled={loadingDevices}
          className="ghost"
        >
          {loadingDevices
            ? "取得中..."
            : "登録済み端末を一覧表示（要：任意DSNでの仮ログイン）"}
        </button>
        {devices && (
          <div className="stack" style={{ gap: 6 }}>
            <div className="muted" style={{ fontSize: 12 }}>
              物理 Kindle（USB 転送可）を選ぶと成功しやすいです。アプリ系は多くの場合
              GENERIC_ERROR で失敗します。
            </div>
            {devices.map((d) => (
              <button
                key={d.deviceSerialNumber}
                type="button"
                onClick={() => setDeviceSn(d.deviceSerialNumber)}
                className={
                  deviceSn === d.deviceSerialNumber ? "primary" : "ghost"
                }
                style={{
                  justifyContent: "flex-start",
                  textAlign: "left",
                  fontFamily: "ui-monospace, monospace",
                  fontSize: 12,
                }}
                title={d.deviceType}
              >
                {isPhysical(d.deviceSerialNumber) ? "📘" : "📱"}{" "}
                {d.deviceSerialNumber}{" "}
                <span style={{ opacity: 0.6 }}>
                  ({d.deviceType})
                </span>
              </button>
            ))}
          </div>
        )}
      </div>

      <div className="row" style={{ justifyContent: "space-between" }}>
        <div className="muted" style={{ fontSize: 12 }}>
          DSN はサーバのメモリにのみ保持されます（ディスク保存なし）。
        </div>
        <button className="primary" type="submit" disabled={pending}>
          {pending ? "認証中..." : "ブラウザ Cookie でログイン"}
        </button>
      </div>

      {error && <div className="error">{error}</div>}
    </form>
  );
}
