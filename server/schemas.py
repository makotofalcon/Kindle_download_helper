"""API の入出力スキーマ定義（Pydantic v2）。"""

from __future__ import annotations

from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, Field

DownloadStatus = Literal["queued", "running", "success", "failed", "skipped"]


class LoginRequest(BaseModel):
    """Amazon アカウントへのログインリクエスト（email/password 経路）。"""

    email: str = Field(..., description="amazon.co.jp のメールアドレス")
    password: str = Field(..., description="amazon.co.jp のパスワード")
    otp_code: Optional[str] = Field(
        default=None,
        description="2段階認証が有効な場合の6桁コード。指定時はpasswordの末尾に連結される。",
    )


class CookieLoginRequest(BaseModel):
    """ブラウザCookie + 端末シリアルによるログインリクエスト（kindle.py 経路）。

    amazon.co.jp に既にログイン済みのブラウザから Cookie を抽出し、指定された DSN
    (端末シリアル番号) を使って蔵書取得・ダウンロード・DRM解除を行う。
    """

    browser: Literal["chrome", "safari", "firefox", "edge"] = Field(
        default="chrome", description="Cookie 抽出対象ブラウザ"
    )
    device_sn: str = Field(
        ..., description="Amazon に登録済みの Kindle 端末シリアル番号 (DSN)"
    )


class AuthStatus(BaseModel):
    """現在の認証状態。"""

    authenticated: bool
    mode: Optional[Literal["nokindle", "cookie"]] = None
    email_hash: Optional[str] = None
    domain: Optional[str] = None
    device_sn_tail: Optional[str] = Field(
        default=None, description="cookie モード時、DSN 末尾4文字だけ返す（確認用）"
    )


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
