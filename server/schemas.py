"""API の入出力スキーマ定義（Pydantic v2）。"""

from __future__ import annotations

from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, Field

DownloadStatus = Literal["queued", "running", "success", "failed", "skipped"]


class LoginRequest(BaseModel):
    """Amazon アカウントへのログインリクエスト。"""

    email: str = Field(..., description="amazon.co.jp のメールアドレス")
    password: str = Field(..., description="amazon.co.jp のパスワード")


class AuthStatus(BaseModel):
    """現在の認証状態。"""

    authenticated: bool
    email_hash: Optional[str] = None
    domain: Optional[str] = None


class BookItem(BaseModel):
    """蔵書の 1 件分。"""

    asin: str
    title: str
    authors: str = ""
    acquired_at: Optional[str] = None
    content_type: Optional[str] = None


class BookList(BaseModel):
    books: list[BookItem]
    fetched_at: datetime


class DownloadRequest(BaseModel):
    asins: list[str] = Field(..., min_length=1)


class DownloadProgress(BaseModel):
    asin: str
    title: str
    status: DownloadStatus
    message: Optional[str] = None
    output_path: Optional[str] = None


class BrowserCookieResult(BaseModel):
    """ブラウザ Cookie 抽出結果のサマリ。"""

    browser: Literal["chrome", "safari", "firefox", "edge"]
    domain: str
    count: int
    has_session_id: bool
    # Cookie 値自体は返さない（機微情報なので）。サーバ側で保持するのみ。
