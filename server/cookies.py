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

# amazon.co.jp だけでなく、ダウンロード CDN (cde-ta-g7g.amazon.com) で使う amazon.com
# のクッキーもまとめて取り込むために複数ドメインを対象にする。
_DOMAINS: tuple[str, ...] = ("amazon.co.jp", "amazon.com")
_PRIMARY_DOMAIN = "amazon.co.jp"
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
    """指定ブラウザから amazon.co.jp と amazon.com の Cookie を抽出してマージ。"""
    extractor = _extractor_for(browser)

    merged_jar: CookieJar | None = None
    total_count = 0
    has_session = False
    errors: list[str] = []

    for domain in _DOMAINS:
        try:
            jar = extractor(domain_name=domain)
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{domain}: {exc}")
            continue

        for cookie in jar:
            total_count += 1
            if cookie.name in {"session-id", "ubid-acbjp", "at-acbjp", "x-acbjp", "ubid-main", "at-main"}:
                has_session = True
            if merged_jar is None:
                from http.cookiejar import CookieJar as _CJ

                merged_jar = _CJ()
            merged_jar.set_cookie(cookie)

    if merged_jar is None:
        raise RuntimeError(
            f"{browser} から Cookie を取得できませんでした: {errors}。"
            " Safari はフルディスクアクセス権限、Chrome/Edge は Keychain 許可が必要です。"
        )

    with _lock:
        global _last_jar
        _last_jar = merged_jar

    return BrowserCookieResult(
        browser=browser,
        domain=_PRIMARY_DOMAIN,
        count=total_count,
        has_session_id=has_session,
    )


def last_cookie_jar() -> CookieJar | None:
    """直近に抽出した CookieJar を返す（認証補助としての参照用）。"""
    with _lock:
        return _last_jar
