"""ブラウザ Cookie + 端末シリアル番号(DSN) による Kindle ダウンロード実装。

`/auth/register` の throttle を回避するため、既にブラウザで amazon.co.jp に
ログイン済みのセッション Cookie をそのまま流用する `kindle.py::Kindle` 経路を採用。
DRM 解除には DSN が必須だが、物理 Kindle 端末を一度でも購入していれば端末ページ
（コンテンツと端末の管理）から確認できる。

ダウンロードは AZW → DRM 解除 → EPUB 変換まで Kindle クラス内部で完結する。
"""

from __future__ import annotations

import logging
import re
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Optional

from kindle_download_helper.config import DEFAULT_SESSION_FILE
from kindle_download_helper.kindle import Kindle

from . import cookies as cookies_module
from . import settings
from .schemas import AuthStatus, BookItem

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class CookieDownloadResult:
    """ダウンロード 1 件分の結果。"""

    asin: str
    title: str
    ok: bool
    output_path: Optional[str] = None
    message: Optional[str] = None


class KindleCookieService:
    """`kindle.py::Kindle` のスレッドセーフなシングルトンラッパ。"""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._client: Optional[Kindle] = None
        self._device_sn: Optional[str] = None
        self._browser: Optional[str] = None
        self._books_cache: list[BookItem] = []

    # ---------- auth ----------
    def login(
        self,
        device_sn: str,
        browser: Literal["chrome", "safari", "firefox", "edge"] = "chrome",
    ) -> AuthStatus:
        """ブラウザから Cookie を抽出して Kindle クライアントを初期化する。"""
        device_sn = device_sn.strip()
        if not device_sn:
            raise ValueError("端末シリアル番号 (DSN) が未入力です。")

        # Cookie を抽出（browser-cookie3 が要求する権限が無い場合は例外）
        result = cookies_module.extract(browser)
        jar = cookies_module.last_cookie_jar()
        if not jar:
            raise RuntimeError(
                f"{browser} から amazon.co.jp の Cookie を取得できませんでした。"
            )
        if not result.has_session_id:
            raise RuntimeError(
                f"{browser} に amazon.co.jp のログイン済みセッションが見つかりません。"
                " ブラウザで一度ログインを完了させてから再試行してください。"
            )

        settings.ensure_dirs()
        out_dir = settings.OUTPUT_DIR / "DOWNLOADS"
        out_dedrm_dir = settings.OUTPUT_DIR / "DEDRMS"
        out_epub_dir = settings.OUTPUT_DIR
        out_dir.mkdir(parents=True, exist_ok=True)
        out_dedrm_dir.mkdir(parents=True, exist_ok=True)

        with self._lock:
            # 古いセッション pickle（前回失敗の状態）が残っていると Cookie が上書きされず
            # CSRF トークン取得も壊れるので、必ず削除してから作り直す。
            try:
                Path(DEFAULT_SESSION_FILE).unlink(missing_ok=True)
            except Exception:  # noqa: BLE001
                logger.exception("既存セッションファイル削除に失敗（続行）")

            client = Kindle(
                domain="jp",
                out_dir=str(out_dir),
                out_dedrm_dir=str(out_dedrm_dir),
                out_epub_dir=str(out_epub_dir),
                device_sn=device_sn,
            )
            # set_cookie は一般の CookieJar で update() が効かない場合があるので
            # 明示的に 1 件ずつセットし直す。
            client.session.cookies.clear()
            for cookie in jar:
                client.session.cookies.set_cookie(cookie)
            # DRM 解除を常に有効化（ユーザー要望に沿った方針）
            client.dedrm = True

            # kindle.py 標準の _get_csrf_token は `var csrfToken = "..."` という
            # 決め打ち正規表現で、Amazon.co.jp の現行ページ構造だとマッチしないことがある。
            # 複数パターンで再取得を試みる。
            try:
                csrf = _extract_csrf_token(client)
                client.csrf_token = csrf
            except _CsrfError as exc:
                raise RuntimeError(
                    "CSRF トークンの取得に失敗しました。"
                    f" 診断: status={exc.status}, html_length={exc.html_length},"
                    f" has_session_cookie={exc.has_session_cookie},"
                    f" body_head={exc.snippet!r}"
                ) from exc

            # CSRF トークン取得 + 端末一覧取得まで走らせて、DSN が登録済みかを検証
            try:
                devices = client.get_devices()
            except Exception as exc:  # noqa: BLE001
                raise RuntimeError(
                    "端末一覧の取得に失敗しました。Cookie の有効期限切れの可能性があります。"
                    " ブラウザで amazon.co.jp に再ログインしてから試してください。"
                    f" 詳細: {exc}"
                ) from exc

            matched = next(
                (d for d in devices if d.get("deviceSerialNumber") == device_sn),
                None,
            )
            if not matched:
                serials = [d.get("deviceSerialNumber", "??") for d in devices]
                raise RuntimeError(
                    f"指定された DSN ({device_sn}) がこのアカウントに登録されていません。"
                    f" 登録済み DSN: {serials}"
                )
            # find_device() が返す device を Kindle._device_serial_number に明示セットしておく
            # （DRM 解除時に self.device_serial_number を参照するため）
            client.device_serial_number = matched["deviceSerialNumber"]

            self._client = client
            self._device_sn = device_sn
            self._browser = browser
            logger.info("cookie login ok: device=%s", device_sn)
            return self.status()

    def logout(self) -> None:
        with self._lock:
            self._client = None
            self._device_sn = None
            self._browser = None
            self._books_cache = []

    def status(self) -> AuthStatus:
        if not self._client or not self._device_sn:
            return AuthStatus(authenticated=False)
        return AuthStatus(
            authenticated=True,
            mode="cookie",
            domain="co.jp",
            device_sn_tail=self._device_sn[-4:],
        )

    # ---------- books ----------
    def fetch_books(self, force: bool = False) -> list[BookItem]:
        if not force and self._books_cache:
            return list(self._books_cache)
        client = self._require_client()
        raw = client.get_all_books(filetype="EBOK")
        items = [_to_book_item(b) for b in raw]
        self._books_cache = items
        return list(items)

    # ---------- downloads ----------
    def download_one(self, asin: str) -> CookieDownloadResult:
        client = self._require_client()
        book = self._lookup_book(asin)
        if not book:
            return CookieDownloadResult(
                asin=asin,
                title=asin,
                ok=False,
                message="蔵書一覧に該当ASINがありません。再取得してください。",
            )
        try:
            device = client.find_device()
        except Exception as exc:  # noqa: BLE001
            return CookieDownloadResult(
                asin=asin,
                title=book.title,
                ok=False,
                message=f"端末検索に失敗: {exc}",
            )
        return _download_and_dedrm(client, device, book)

    def _lookup_book(self, asin: str) -> Optional[BookItem]:
        for b in self._books_cache:
            if b.asin == asin:
                return b
        return None

    def _require_client(self) -> Kindle:
        if not self._client:
            raise RuntimeError("未ログインです。先に Cookie+DSN でログインしてください。")
        return self._client

    # ---------- devices ----------
    def list_devices(self) -> list[dict[str, str]]:
        """現在の Kindle セッションで取得できる登録済み端末リスト。"""
        client = self._require_client()
        devices = client.get_devices()
        return [
            {
                "deviceSerialNumber": d.get("deviceSerialNumber", ""),
                "deviceType": d.get("deviceType", ""),
                "deviceName": d.get("deviceName", ""),
                "deviceAccountId": d.get("deviceAccountId", ""),
            }
            for d in devices
        ]


def _to_book_item(raw: dict) -> BookItem:
    """`Kindle.get_all_books` の生データから UI 用 BookItem に変換。"""
    asin = str(raw.get("asin") or raw.get("ASIN") or "")
    title = str(raw.get("title") or "(無題)")
    authors_raw = raw.get("authors")
    if isinstance(authors_raw, list):
        authors = ", ".join(str(a) for a in authors_raw)
    else:
        authors = str(authors_raw or "")
    acquired = str(raw.get("acquiredDate") or raw.get("acquiredTime") or raw.get("purchaseDate") or "")
    return BookItem(
        asin=asin,
        title=title,
        authors=authors,
        acquired_at=acquired,
        content_type=str(raw.get("contentType") or "EBOK"),
    )


def _to_raw_book(item: BookItem) -> dict:
    """Kindle.download_one_book は生 dict を期待するので逆変換。"""
    return {
        "asin": item.asin,
        "title": item.title,
        "authors": item.authors,
    }


def _guess_output_path(title: str) -> Optional[Path]:
    """EPUB 変換後のファイル名を推測。"""
    for ext in (".epub", ".azw", ".azw3", ".mobi"):
        candidate = settings.OUTPUT_DIR / f"{title}{ext}"
        if candidate.exists():
            return candidate
    return settings.OUTPUT_DIR


# --- ダウンロード本体（自前） ----------------------------------------------


def _download_and_dedrm(
    client: Kindle, device: dict, book: BookItem
) -> CookieDownloadResult:
    """AZW ダウンロード → DRM 解除 → EPUB 変換 までまとめて実行。

    kindle.py::download_one_book は `cde-ta-g7g.amazon.com` に対して直接 GET するが、
    Cookie ベース認証では "Invalid access token" で 403 になる。正しい経路は
    `amazon.co.jp/hz/mycd/ajax` に POST して署名付き URL を取得してから GET する。
    """
    import json
    import os
    import re
    import shutil
    import urllib.parse

    from moki import extract as mobi_extract  # type: ignore[import-not-found]
    from kindle_download_helper.dedrm import MobiBook, get_pid_list

    asin = book.asin
    title = book.title

    # 1) mycd/ajax で署名付きダウンロード URL を貰う
    logger.info("fetching signed URL: asin=%s dsn=%s", asin, device.get("deviceSerialNumber"))
    signed_url = _fetch_signed_download_url(client, device, asin)
    if isinstance(signed_url, CookieDownloadResult):
        logger.warning("signed URL fetch failed: %s", signed_url.message)
        return signed_url  # エラー結果
    logger.info("signed URL acquired (len=%d)", len(signed_url))
    url = signed_url

    # 2) 署名付き URL から本体を取得
    try:
        r = client.session.get(url, verify=False, stream=True, timeout=120)
    except Exception as exc:  # noqa: BLE001
        return CookieDownloadResult(
            asin=asin, title=title, ok=False, message=f"接続エラー: {exc}"
        )

    if r.status_code != 200:
        body = r.text[:500] if r.text else ""
        return CookieDownloadResult(
            asin=asin,
            title=title,
            ok=False,
            message=f"CDN ダウンロード {r.status_code}: {body!r} | URL={url[:200]}",
        )

    # ファイル名決定
    disp = r.headers.get("Content-Disposition", "")
    match = re.findall(r"filename\*=UTF-8''(.+)", disp)
    if match:
        _, extname = os.path.splitext(urllib.parse.unquote(match[0]))
    else:
        extname = ".azw"
    sanitized_title = re.sub(r'[\\/:*?"<>|]', "_", title)
    cut = client.cut_length
    if len(sanitized_title) + len(extname) > cut:
        sanitized_title = sanitized_title[: cut - len(extname) - 5] + sanitized_title[-5:]

    name = sanitized_title + extname
    out_raw = Path(client.out_dir) / name
    out_dedrm = Path(client.out_dedrm_dir) / name
    out_epub = Path(client.out_epub_dir) / (sanitized_title + ".epub")
    out_raw.parent.mkdir(parents=True, exist_ok=True)
    out_dedrm.parent.mkdir(parents=True, exist_ok=True)
    out_epub.parent.mkdir(parents=True, exist_ok=True)

    with open(out_raw, "wb") as f:
        for chunk in r.iter_content(chunk_size=8192):
            if chunk:
                f.write(chunk)

    # DRM 解除
    try:
        mb = MobiBook(str(out_raw))
        md1, md2 = mb.get_pid_meta_info()
        totalpids = list(set(get_pid_list(md1, md2, [device["deviceSerialNumber"]], [])))
        mb.make_drm_file(totalpids, str(out_dedrm))
        # EPUB 変換
        epub_dir, epub_file = mobi_extract(str(out_dedrm))
        shutil.copy2(epub_file, out_epub)
        shutil.rmtree(epub_dir, ignore_errors=True)
        return CookieDownloadResult(
            asin=asin, title=title, ok=True, output_path=str(out_epub)
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception("dedrm failed for %s", title)
        # DRM 解除に失敗しても AZW は残しておく
        return CookieDownloadResult(
            asin=asin,
            title=title,
            ok=False,
            output_path=str(out_raw),
            message=f"AZW 保存成功 / DRM 解除失敗: {exc}",
        )


# --- 署名付きダウンロード URL 取得 ---------------------------------------


def _fetch_signed_download_url(
    client: Kindle, device: dict, asin: str
) -> str | CookieDownloadResult:
    """mycd/ajax を叩いて「Download & transfer via USB」の署名付き URL を得る。

    Amazon の Web UI がまさに同じ API を叩いている。複数のパラメータ形式が
    あるので順番に試す。成功時は URL 文字列、失敗時は CookieDownloadResult を返す。
    """
    import json

    payloads: list[dict] = [
        {
            "param": {
                "DownloadViaUSB": {
                    "contentName": asin,
                    "deviceSerialNumber": device["deviceSerialNumber"],
                    "isAsin": "true",
                }
            }
        },
        {
            "param": {
                "DownloadViaUSB": {
                    "contentName": asin,
                    "deviceSerialNumber": device["deviceSerialNumber"],
                }
            }
        },
    ]
    last_body = ""
    last_status = 0
    for payload in payloads:
        logger.info("POST mycd/ajax payload=%s", json.dumps(payload))
        try:
            r = client.session.post(
                client.urls["payload"],
                data={
                    "data": json.dumps(payload),
                    "csrfToken": client.csrf_token,
                },
                timeout=30,
            )
        except Exception as exc:  # noqa: BLE001
            logger.exception("mycd/ajax request failed")
            return CookieDownloadResult(
                asin=asin,
                title=asin,
                ok=False,
                message=f"mycd/ajax 接続エラー: {exc}",
            )
        last_body = r.text[:500]
        last_status = r.status_code
        logger.info("mycd/ajax status=%d body_head=%s", r.status_code, last_body[:200])
        if r.status_code != 200:
            continue
        try:
            data = r.json()
        except Exception:  # noqa: BLE001
            continue
        # 成功レスポンスのキーは "DownloadViaUSB"
        inner = data.get("DownloadViaUSB") if isinstance(data, dict) else None
        if not isinstance(inner, dict):
            continue
        if inner.get("success") is False:
            # 失敗メッセージを保存して次のペイロードを試す
            last_body = json.dumps(inner)[:500]
            continue
        url = inner.get("URL") or inner.get("url")
        if url:
            return str(url)
        last_body = json.dumps(inner)[:500]

    return CookieDownloadResult(
        asin=asin,
        title=asin,
        ok=False,
        message=(
            "mycd/ajax から署名付き URL が取れませんでした。"
            " 端末が本当に登録済みか、USB 転送が可能な端末タイプか確認してください。"
            f" 最終 status={last_status} 応答: {last_body!r}"
        ),
    )


# --- CSRF 抽出 -----------------------------------------------------------

_CSRF_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r'var\s+csrfToken\s*=\s*"([^"]+)"'),
    re.compile(r'"csrfToken"\s*:\s*"([^"]+)"'),
    re.compile(r"csrfToken\s*:\s*'([^']+)'"),
    re.compile(r'csrf[_-]?token["\'\s:=]+([A-Za-z0-9+/=_-]{20,})'),
    re.compile(r'anti[_-]?csrftoken[-_]?a2z["\'\s:=]+([A-Za-z0-9+/=_-]{20,})'),
)


@dataclass(frozen=True)
class _CsrfError(RuntimeError):
    status: int
    html_length: int
    has_session_cookie: bool
    snippet: str

    def __str__(self) -> str:  # noqa: D401
        return (
            f"csrf not found (status={self.status}, len={self.html_length},"
            f" session={self.has_session_cookie})"
        )


def _extract_csrf_token(client: Kindle) -> str:
    """Amazon.co.jp の mycd ページから CSRF トークンを抽出。複数 URL/パターンを試す。"""
    urls = [
        client.urls["bookall"],
        "https://www.amazon.co.jp/hz/mycd/digital-console/contentlist/booksAll/dateDsc",
        "https://www.amazon.co.jp/hz/mycd",
    ]

    last_status = 0
    last_len = 0
    last_snippet = ""
    session_cookie_present = any(c.name == "session-id" for c in client.session.cookies)

    for url in urls:
        try:
            r = client.session.get(url, timeout=20, allow_redirects=True)
        except Exception:  # noqa: BLE001
            continue
        last_status = r.status_code
        last_len = len(r.text)
        last_snippet = r.text[:300]
        if r.status_code != 200:
            continue
        for pat in _CSRF_PATTERNS:
            m = pat.search(r.text)
            if m:
                logger.info("csrf matched via %s (url=%s)", pat.pattern[:40], url)
                return m.group(1)

    raise _CsrfError(
        status=last_status,
        html_length=last_len,
        has_session_cookie=session_cookie_present,
        snippet=last_snippet,
    )


service = KindleCookieService()
