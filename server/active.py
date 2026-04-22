"""アクティブな Kindle サービス（email/password 経路 or Cookie+DSN 経路）の切り替え。

Cookie+DSN 経路が優先されるのは、amazon.co.jp の `/auth/register` が
ThrottledRequest で弾かれる事例があり、そちらで回避する必要があるため。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Optional, Protocol

from .schemas import AuthStatus, BookItem


class _DownloadResult(Protocol):
    asin: str
    title: str
    ok: bool
    output_path: Optional[str]
    message: Optional[str]


class KindleLike(Protocol):
    """両サービスが満たすべき公開インターフェース。"""

    def status(self) -> AuthStatus: ...
    def fetch_books(self, force: bool = False) -> list[BookItem]: ...
    def download_one(self, asin: str) -> _DownloadResult: ...
    def logout(self) -> None: ...


@dataclass(frozen=True)
class ActiveMode:
    mode: Literal["nokindle", "cookie", "none"]


def get() -> KindleLike:
    """現在アクティブなサービスを返す。Cookie モードが優先。"""
    from . import kindle_cookie_service, kindle_service

    if kindle_cookie_service.service.status().authenticated:
        return kindle_cookie_service.service
    return kindle_service.service


def mode() -> ActiveMode:
    from . import kindle_cookie_service, kindle_service

    if kindle_cookie_service.service.status().authenticated:
        return ActiveMode(mode="cookie")
    if kindle_service.service.status().authenticated:
        return ActiveMode(mode="nokindle")
    return ActiveMode(mode="none")


def logout_all() -> None:
    """両モードを解除。"""
    from . import kindle_cookie_service, kindle_service

    kindle_cookie_service.service.logout()
    kindle_service.service.logout()
