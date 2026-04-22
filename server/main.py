"""FastAPI エントリポイント（Cloud Reader + PDF キャプチャ専用）。

ルート:
    GET  /api/health
    GET  /api/auth/status
    POST /api/auth/logout                -- Playwright プロファイルは保持、プロセスだけ落とす
    POST /api/capture/ensure-login       -- Cloud Reader にログイン済みか確認
    GET  /api/books?refresh=1            -- Cloud Reader の library ページから抽出
    POST /api/downloads                  -- { "asins": [...] } で PDF キャプチャ開始
    GET  /api/downloads/stream           -- SSE 進捗
    GET  /api/output/reveal              -- Finder で出力ディレクトリを開く
"""

from __future__ import annotations

import json
import logging
import subprocess
from datetime import datetime, timezone

from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse, StreamingResponse

from . import settings
from .capture_service import service as capture_service
from .downloader import manager, sse_stream
from .schemas import AuthStatus, BookItem, BookList, DownloadRequest

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger(__name__)

app = FastAPI(title="Kindle Cloud Reader Capture", version="0.2.0")


@app.on_event("startup")
async def _startup() -> None:
    settings.ensure_dirs()


@app.on_event("shutdown")
async def _shutdown() -> None:
    try:
        await capture_service.shutdown()
    except Exception:
        logger.exception("capture_service シャットダウンでエラー")


@app.get("/api/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


# ---------- auth ----------
@app.get("/api/auth/status", response_model=AuthStatus)
async def auth_status() -> AuthStatus:
    """Cloud Reader のログイン状態（プロファイルに保存済みの Cookie で判定）。"""
    try:
        state = await capture_service.ensure_login()
        return AuthStatus(
            authenticated=bool(state.get("authenticated")),
            message=str(state.get("message") or ""),
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception("auth_status error")
        return AuthStatus(authenticated=False, message=f"状態取得エラー: {exc}")


@app.post("/api/auth/logout")
async def auth_logout() -> dict[str, bool]:
    """Playwright プロセスを停止。プロファイル自体は保持する。"""
    await capture_service.shutdown()
    return {"ok": True}


@app.post("/api/capture/ensure-login", response_model=AuthStatus)
async def capture_ensure_login() -> AuthStatus:
    """Cloud Reader 用 Chromium を立ち上げ、ログイン済みか確認。
    未ログインならブラウザで手動ログインを促す。
    """
    try:
        state = await capture_service.ensure_login()
        return AuthStatus(
            authenticated=bool(state.get("authenticated")),
            message=str(state.get("message") or ""),
        )
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=str(exc))


# ---------- books ----------
@app.get("/api/books", response_model=BookList)
async def get_books(refresh: bool = False) -> BookList:
    """Cloud Reader の library ページを Playwright でスクレイプ。"""
    # キャッシュ
    if not refresh and settings.BOOKS_CACHE_FILE.exists():
        try:
            cached = json.loads(settings.BOOKS_CACHE_FILE.read_text(encoding="utf-8"))
            items = [BookItem(**b) for b in cached.get("books", [])]
            fetched = datetime.fromisoformat(cached["fetched_at"])
            return BookList(books=items, fetched_at=fetched)
        except Exception:
            logger.exception("蔵書キャッシュ読み込み失敗、再取得します")

    try:
        books = await capture_service.fetch_library()
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=str(exc))

    now = datetime.now(timezone.utc)
    settings.BOOKS_CACHE_FILE.write_text(
        json.dumps(
            {
                "books": [b.model_dump() for b in books],
                "fetched_at": now.isoformat(),
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return BookList(books=books, fetched_at=now)


# ---------- downloads (= PDF capture) ----------
@app.post("/api/downloads")
async def start_download(req: DownloadRequest) -> JSONResponse:
    """選択 ASIN を PDF キャプチャキューに投入。"""
    # タイトルを引く（出力ファイル名に使う）
    items: list[tuple[str, str]] = []
    by_asin: dict[str, str] = {}
    if settings.BOOKS_CACHE_FILE.exists():
        try:
            cached = json.loads(settings.BOOKS_CACHE_FILE.read_text(encoding="utf-8"))
            for b in cached.get("books", []):
                if b.get("asin"):
                    by_asin[b["asin"]] = b.get("title") or b["asin"]
        except Exception:
            logger.exception("蔵書キャッシュ読み込みでエラー（タイトル解決できません）")

    for a in req.asins:
        items.append((a, by_asin.get(a, a)))

    try:
        job = await manager.start(items)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=409, detail=str(exc))
    return JSONResponse({"ok": True, "total": len(job.items)})


@app.get("/api/downloads/stream")
async def stream_downloads() -> StreamingResponse:
    job = manager.current()
    if not job:
        raise HTTPException(status_code=404, detail="進行中のジョブがありません")
    return StreamingResponse(
        sse_stream(job),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


# ---------- output ----------
@app.get("/api/output/reveal")
async def reveal_output() -> dict[str, str]:
    """出力ディレクトリを Finder で開く（Mac 専用）。"""
    settings.ensure_dirs()
    subprocess.Popen(["open", str(settings.OUTPUT_DIR)])
    return {"path": str(settings.OUTPUT_DIR)}


def run() -> None:
    """`python -m server.main` 相当のエントリ。"""
    import uvicorn

    uvicorn.run(
        "server.main:app",
        host=settings.API_HOST,
        port=settings.API_PORT,
        reload=False,
    )


if __name__ == "__main__":
    run()
