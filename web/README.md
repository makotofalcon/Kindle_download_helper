# Kindle Download Helper — Web UI

ブラウザ上で amazon.co.jp の Kindle 蔵書を選択し、DRM 解除済み EPUB として
`/Users/makotofalcon/kindle/` 配下にダウンロードできる Mac 専用のローカル Web アプリ。

- フロントエンド: Next.js 16 (App Router / Server Components / Turbopack) + React 19
- バックエンド: FastAPI (`../server/main.py`)
- バックエンドが既存 Python パッケージ `kindle_download_helper.no_kindle.NoKindle`
  を呼び出すため、物理 Kindle 端末の Device Serial Number は不要。

## 起動方法（ルートディレクトリから）

```bash
./dev.sh
```

- FastAPI → `http://127.0.0.1:8001`
- Next.js dev → `http://127.0.0.1:3000` （自動でブラウザを開く）

## ページ構成

- `/` ログイン / ブラウザ Cookie 取得
- `/library` 蔵書一覧（検索・全選択・まとめてダウンロード）
- `/downloads` SSE による進捗表示、完了後に Finder で出力先を開ける

## 開発メモ

- `src/app/*` は原則 Server Component。インタラクションが必要なものだけ
  `src/components/*` に `"use client"` として切り出している。
- `/api/*` は `next.config.ts` の `rewrites` で FastAPI にフォワード。
- 型: `src/lib/api.ts` がフロント側の型定義とクライアント。
- ダウンロードは Amazon のリスク制御を避けるため 1 ジョブずつ直列実行。
