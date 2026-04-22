"""ローカルブラウザから amazon.co.jp の Cookie を抽出するユーティリティ。

`browser-cookie3` を用いて Chrome / Safari / Firefox / Edge のクッキーストアから
amazon.co.jp のセッションクッキーを読み出す。読み出した Cookie 自体は UI に返さず、
サーバ側のメモリに保持し、「何件取れたか」「session-id が含まれていたか」のみ返す。

注意:
    - Chrome/Edge: macOS Keychain への初回アクセス許可が必要。
    - Safari: フルディスクアクセス権限（システム設定 -> プライバシーとセキュリティ）が必要。
    - Firefox: 権限不要。
"""

from __future__ import annotations

import logging
import threading
from http.cookiejar import CookieJar
from typing import Callable, Literal

import browser_cookie3

from .schemas import BrowserCookieResult

logger = logging.getLogger(__name__)

BrowserName = Literal["chrome", "safari", "firefox", "edge"]

_DOMAIN = "amazon.co.jp"
_lock = threading.Lock()
_last_jar: CookieJar | None = None


def _extractor_for(browser: BrowserName) -> Callable[..., CookieJar]:
    mapping: dict[str, Callable[..., CookieJar]] = {
        "chrome": browser_cookie3.chrome,
        "safari": browser_cookie3.safari,
        "firefox": browser_cookie3.firefox,
        "edge": browser_cookie3.edge,
    }
    if browser not in mapping:
        raise ValueError(f"unsupported browser: {browser}")
    return mapping[browser]


def extract(browser: BrowserName) -> BrowserCookieResult:
    """指定ブラウザから amazon.co.jp の Cookie を抽出。"""
    extractor = _extractor_for(browser)
    try:
        jar = extractor(domain_name=_DOMAIN)
    except Exception as exc:  # noqa: BLE001
        logger.exception("Cookie 抽出に失敗: %s", browser)
        raise RuntimeError(
            f"{browser} から Cookie を取得できませんでした: {exc}。"
            " Safari はフルディスクアクセス権限、Chrome/Edge は Keychain 許可が必要です。"
        ) from exc

    count = 0
    has_session = False
    for cookie in jar:
        count += 1
        if cookie.name in {"session-id", "ubid-acbjp", "at-acbjp"}:
            has_session = True

    with _lock:
        global _last_jar
        _last_jar = jar

    return BrowserCookieResult(
        browser=browser,
        domain=_DOMAIN,
        count=count,
        has_session_id=has_session,
    )


def last_cookie_jar() -> CookieJar | None:
    """直近に抽出した CookieJar を返す（認証補助としての参照用）。"""
    with _lock:
        return _last_jar
