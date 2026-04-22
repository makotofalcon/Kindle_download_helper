"use client";

import { useMemo, useState, useTransition } from "react";
import { useRouter } from "next/navigation";
import { api, type BookItem } from "@/lib/api";

type Props = { books: BookItem[] };

export function LibraryTable({ books }: Props) {
  const router = useRouter();
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [query, setQuery] = useState("");
  const [pending, startTransition] = useTransition();
  const [refreshing, startRefresh] = useTransition();
  const [error, setError] = useState<string | null>(null);

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return books;
    return books.filter(
      (b) =>
        b.title.toLowerCase().includes(q) ||
        b.authors.toLowerCase().includes(q) ||
        b.asin.toLowerCase().includes(q),
    );
  }, [books, query]);

  const toggle = (asin: string) => {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(asin)) {
        next.delete(asin);
      } else {
        next.add(asin);
      }
      return next;
    });
  };

  const toggleAll = () => {
    if (selected.size === filtered.length && filtered.length > 0) {
      setSelected(new Set());
    } else {
      setSelected(new Set(filtered.map((b) => b.asin)));
    }
  };

  const startDownload = () => {
    setError(null);
    if (selected.size === 0) return;
    const asins = Array.from(selected);
    startTransition(async () => {
      try {
        await api.startDownload(asins);
        router.push("/downloads");
      } catch (err) {
        const message = err instanceof Error ? err.message : String(err);
        setError(message);
      }
    });
  };

  const refresh = () => {
    setError(null);
    startRefresh(async () => {
      try {
        await fetch("/api/books?refresh=true", { cache: "no-store" });
        router.refresh();
      } catch (err) {
        const message = err instanceof Error ? err.message : String(err);
        setError(message);
      }
    });
  };

  const allSelectedOnPage =
    filtered.length > 0 && selected.size === filtered.length;

  return (
    <div className="stack">
      <div className="row" style={{ justifyContent: "space-between" }}>
        <div className="row">
          <input
            type="text"
            placeholder="タイトル / 著者 / ASIN で検索"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            style={{ width: 320 }}
          />
          <button onClick={refresh} disabled={refreshing} className="ghost">
            {refreshing ? "再取得中..." : "蔵書を再取得"}
          </button>
        </div>
        <div className="row">
          <span className="muted">選択 {selected.size} 件</span>
          <button
            className="primary"
            disabled={pending || selected.size === 0}
            onClick={startDownload}
          >
            {pending ? "開始中..." : "選択したものをダウンロード"}
          </button>
        </div>
      </div>

      {error && <div className="error">{error}</div>}

      <div className="table-wrap">
        <table className="table">
          <thead>
            <tr>
              <th style={{ width: 36 }}>
                <input
                  type="checkbox"
                  checked={allSelectedOnPage}
                  onChange={toggleAll}
                  aria-label="全選択"
                />
              </th>
              <th>タイトル</th>
              <th style={{ width: 180 }}>著者</th>
              <th style={{ width: 120 }}>ASIN</th>
              <th style={{ width: 120 }}>取得日</th>
            </tr>
          </thead>
          <tbody>
            {filtered.length === 0 && (
              <tr>
                <td colSpan={5} className="muted" style={{ padding: 24 }}>
                  該当する蔵書がありません。
                </td>
              </tr>
            )}
            {filtered.map((b) => (
              <tr key={b.asin}>
                <td>
                  <input
                    type="checkbox"
                    checked={selected.has(b.asin)}
                    onChange={() => toggle(b.asin)}
                    aria-label={`${b.title} を選択`}
                  />
                </td>
                <td>{b.title}</td>
                <td className="muted">{b.authors}</td>
                <td className="inline-code">{b.asin}</td>
                <td className="muted">{b.acquired_at ?? ""}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
