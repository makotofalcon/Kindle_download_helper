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

from . import active, cookies, settings
from .amazon_login import LoginError
from .capture_service import service as capture_service
from .downloader import manager, sse_stream
from .kindle_cookie_service import service as cookie_service
from .kindle_service import service
from .schemas import (
    AuthStatus,
    BookList,
    BrowserCookieResult,
    CookieLoginRequest,
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
    """アクティブな経路（Cookie優先）の認証状態を返す。"""
    return active.get().status()


@app.post("/api/auth/login")
async def auth_login(req: LoginRequest) -> JSONResponse:
    """ログイン。失敗時は Amazon の生レスポンスも同梱して返す（診断用）。"""
    try:
        status = service.login(req.email, req.password, req.otp_code)
        return JSONResponse(status.model_dump())
    except LoginError as exc:
        logger.warning("login failed: %s / raw=%s", exc, exc.raw_response)
        return JSONResponse(
            status_code=401,
            content={
                "detail": str(exc),
                "amazon_response": exc.raw_response,
            },
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception("unexpected login error")
        raise HTTPException(status_code=500, detail=str(exc))


@app.post("/api/auth/logout")
async def auth_logout() -> dict[str, bool]:
    """両経路まとめてログアウト。"""
    active.logout_all()
    return {"ok": True}


@app.post("/api/auth/restore", response_model=AuthStatus)
async def auth_restore() -> AuthStatus:
    """email/password 経路のリフレッシュ復元のみ対応。"""
    return service.restore_session()


@app.post("/api/auth/cookie-login", response_model=AuthStatus)
async def cookie_login(req: CookieLoginRequest) -> AuthStatus:
    """ブラウザ Cookie + 端末シリアル番号 (DSN) によるログイン。"""
    try:
        return cookie_service.login(device_sn=req.device_sn, browser=req.browser)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=str(exc))


@app.get("/api/kindle/devices")
async def list_kindle_devices() -> dict[str, list[dict[str, str]]]:
    """Cookie 経路でログイン済みのアカウントに登録されている端末一覧。"""
    try:
        return {"devices": cookie_service.list_devices()}
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=str(exc))


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
        items = active.get().fetch_books(force=refresh)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=str(exc))
    from datetime import datetime, timezone

    return BookList(books=items, fetched_at=datetime.now(timezone.utc))


# ---------- capture (Kindle Cloud Reader → PDF) ----------
@app.post("/api/capture/ensure-login")
async def capture_ensure_login() -> dict[str, bool | str]:
    """Cloud Reader を開いてログイン済みかを確認。未ログインならブラウザで手動ログイン待ち。"""
    try:
        return await capture_service.ensure_login()
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=str(exc))


@app.post("/api/downloads")
async def start_download(req: DownloadRequest) -> JSONResponse:
    """選択 ASIN をキャプチャキューに投入。

    req.asins は単なる ASIN 文字列リスト。タイトルは蔵書キャッシュから引く。
    """
    # タイトルを引く（出力ファイル名に使う）
    items: list[tuple[str, str]] = []
    try:
        current_books = active.get().fetch_books(force=False)
    except Exception:  # noqa: BLE001
        current_books = []
    by_asin = {b.asin: b.title for b in current_books}
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
