"""FastAPI エントリポイント。

ルート:
    GET  /api/health
    GET  /api/auth/status
    POST /api/auth/login
    POST /api/auth/logout
    POST /api/auth/restore
    POST /api/cookies/{browser}            -- Chrome/Safari/Firefox/Edge から抽出
    GET  /api/books?refresh=1
    POST /api/downloads                    -- { "asins": [...] } でキュー開始
    GET  /api/downloads/stream             -- SSE
    GET  /api/output/reveal                -- Finder で出力ディレクトリを開く
"""

from __future__ import annotations

import logging
import subprocess
from typing import Literal

from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse, StreamingResponse

from . import cookies, settings
from .downloader import manager, sse_stream
from .kindle_service import service
from .schemas import (
    AuthStatus,
    BookList,
    BrowserCookieResult,
    DownloadRequest,
    LoginRequest,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger(__name__)

app = FastAPI(title="Kindle Download Helper Web", version="0.1.0")


@app.on_event("startup")
async def _startup() -> None:
    settings.ensure_dirs()
    # 保存済みセッションがあれば自動復元を試みる（失敗しても問題なし）
    try:
        service.restore_session()
    except Exception:
        logger.exception("初期セッション復元でエラー")


@app.get("/api/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


# ---------- auth ----------
@app.get("/api/auth/status", response_model=AuthStatus)
async def auth_status() -> AuthStatus:
    return service.status()


@app.post("/api/auth/login", response_model=AuthStatus)
async def auth_login(req: LoginRequest) -> AuthStatus:
    try:
        return service.login(req.email, req.password)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=401, detail=str(exc))


@app.post("/api/auth/logout")
async def auth_logout() -> dict[str, bool]:
    service.logout()
    return {"ok": True}


@app.post("/api/auth/restore", response_model=AuthStatus)
async def auth_restore() -> AuthStatus:
    return service.restore_session()


# ---------- cookies ----------
BrowserName = Literal["chrome", "safari", "firefox", "edge"]


@app.post("/api/cookies/{browser}", response_model=BrowserCookieResult)
async def extract_cookies(browser: BrowserName) -> BrowserCookieResult:
    try:
        return cookies.extract(browser)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=str(exc))


# ---------- books ----------
@app.get("/api/books", response_model=BookList)
async def get_books(refresh: bool = False) -> BookList:
    try:
        items = service.fetch_books(force=refresh)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=str(exc))
    from datetime import datetime, timezone

    return BookList(books=items, fetched_at=datetime.now(timezone.utc))


# ---------- downloads ----------
@app.post("/api/downloads")
async def start_download(req: DownloadRequest) -> JSONResponse:
    try:
        job = await manager.start(req.asins)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=409, detail=str(exc))
    return JSONResponse({"ok": True, "total": len(job.asins)})


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
    """`python -m server` 相当のエントリ。"""
    import uvicorn

    uvicorn.run(
        "server.main:app",
        host=settings.API_HOST,
        port=settings.API_PORT,
        reload=False,
    )


if __name__ == "__main__":
    run()
