#!/usr/bin/env bash
# Kindle Download Helper - Web (Mac 専用) の開発起動スクリプト。
#   - FastAPI (uvicorn) を 127.0.0.1:8001 で起動
#   - Next.js (dev) を 127.0.0.1:3000 で起動
#   - ブラウザを自動オープン
# Ctrl+C で両プロセスを停止。

set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$PROJECT_ROOT"

# 仮想環境がなければ案内して終了
if [ ! -x "$PROJECT_ROOT/.venv/bin/python" ]; then
  echo "[!] Python 仮想環境が未作成です。以下を実行してください:"
  echo "    /opt/homebrew/bin/python3.12 -m venv .venv"
  echo "    ./.venv/bin/pip install -r server/requirements.txt"
  exit 1
fi

# Node modules 未インストールなら自動で入れる
if [ ! -d "$PROJECT_ROOT/web/node_modules" ]; then
  echo "[i] web/node_modules が無いため npm install を実行します..."
  (cd "$PROJECT_ROOT/web" && npm install)
fi

# FastAPI をバックグラウンド起動
echo "[i] FastAPI を起動中 (http://127.0.0.1:8001)"
"$PROJECT_ROOT/.venv/bin/python" -m server.main &
API_PID=$!

cleanup() {
  echo "\n[i] 終了します..."
  kill "$API_PID" 2>/dev/null || true
  kill "$WEB_PID" 2>/dev/null || true
  wait 2>/dev/null || true
}
trap cleanup INT TERM EXIT

# FastAPI ヘルスチェック
for _ in $(seq 1 20); do
  if curl -sf http://127.0.0.1:8001/api/health >/dev/null 2>&1; then
    break
  fi
  sleep 0.3
done

# Next.js dev を起動
echo "[i] Next.js dev を起動中 (http://127.0.0.1:3000)"
(cd "$PROJECT_ROOT/web" && npm run dev) &
WEB_PID=$!

# ブラウザ自動オープン
(
  sleep 2
  if curl -sf http://127.0.0.1:3000 >/dev/null 2>&1; then
    open http://127.0.0.1:3000
  else
    for _ in $(seq 1 20); do
      sleep 0.5
      curl -sf http://127.0.0.1:3000 >/dev/null 2>&1 && { open http://127.0.0.1:3000; break; } || true
    done
  fi
) &

wait "$API_PID" "$WEB_PID"
