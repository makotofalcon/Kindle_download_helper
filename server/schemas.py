"""API の入出力スキーマ定義（Pydantic v2）。"""

from __future__ import annotations

from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, Field

DownloadStatus = Literal["queued", "running", "success", "failed", "skipped"]


class AuthStatus(BaseModel):
    """Cloud Reader の認証状態。"""

    authenticated: bool
    message: Optional[str] = None


class BookItem(BaseModel):
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
