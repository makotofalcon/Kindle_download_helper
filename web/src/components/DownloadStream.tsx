"use client";

import { useEffect, useRef, useState } from "react";
import { api, type DownloadProgress } from "@/lib/api";

export function DownloadStream() {
  const [events, setEvents] = useState<DownloadProgress[]>([]);
  const [done, setDone] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [revealPath, setRevealPath] = useState<string | null>(null);
  const sourceRef = useRef<EventSource | null>(null);

  useEffect(() => {
    const src = new EventSource("/api/downloads/stream");
    sourceRef.current = src;

    src.onmessage = (ev) => {
      try {
        const data = JSON.parse(ev.data) as DownloadProgress;
        setEvents((prev) => [...prev, data]);
      } catch (err) {
        const msg = err instanceof Error ? err.message : String(err);
        setError(`ペイロード解析失敗: ${msg}`);
      }
    };

    src.addEventListener("done", () => {
      setDone(true);
      src.close();
    });

    src.onerror = () => {
      setError(
        "SSE 接続に失敗しました。進行中のダウンロードジョブが存在しない可能性があります。",
      );
      src.close();
    };

    return () => {
      src.close();
    };
  }, []);

  const reveal = async () => {
    try {
      const r = await api.revealOutput();
      setRevealPath(r.path);
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      setError(msg);
    }
  };

  const byAsin = new Map<string, DownloadProgress>();
  for (const e of events) byAsin.set(e.asin, e);
  const rows = Array.from(byAsin.values());
  const summary = {
    total: rows.length,
    success: rows.filter((r) => r.status === "success").length,
    failed: rows.filter((r) => r.status === "failed").length,
    running: rows.filter((r) => r.status === "running").length,
  };

  return (
    <div className="stack">
      <div className="row" style={{ justifyContent: "space-between" }}>
        <div className="muted">
          進行 {summary.running} / 成功 {summary.success} / 失敗 {summary.failed}{" "}
          / 合計 {summary.total}
        </div>
        <div className="row">
          <button onClick={reveal} className="ghost">
            出力先を Finder で開く
          </button>
        </div>
      </div>

      {revealPath && (
        <div className="success" style={{ fontSize: 13 }}>
          {revealPath} を Finder で開きました。
        </div>
      )}

      {error && <div className="error">{error}</div>}

      {rows.length === 0 && !done && (
        <div className="muted">進行中のジョブの最初のイベントを待機中...</div>
      )}

      <div>
        {rows.map((r) => (
          <div key={r.asin} className="progress-row">
            <span className={`badge ${r.status}`}>{r.status}</span>
            <div>
              <div>{r.title}</div>
              {r.message && (
                <div className="error" style={{ fontSize: 12 }}>
                  {r.message}
                </div>
              )}
              {r.output_path && (
                <div className="muted" style={{ fontSize: 12 }}>
                  {r.output_path}
                </div>
              )}
            </div>
            <span className="inline-code">{r.asin}</span>
          </div>
        ))}
      </div>

      {done && <div className="success">すべてのジョブが完了しました。</div>}
    </div>
  );
}
