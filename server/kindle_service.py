"""既存の `kindle_download_helper.no_kindle.NoKindle` を FastAPI から使うためのラッパ。

NoKindle は `__init__` 内で Amazon にログインしてトークンを取得し、
`~/.kindle_download_helper/.tokens*.json` に保存する。以降は同じメールアドレスでの
インスタンス生成時にリフレッシュ経由で再ログインされる。

本モジュールはグローバルに 1 つだけインスタンスを保持し、スレッドセーフに切り替える。
"""

from __future__ import annotations

import hashlib
import json
import logging
import shutil
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from kindle_download_helper import amazon_api
from kindle_download_helper.no_kindle import NoKindle

from . import settings
from .amazon_login import LoginError, login as amazon_register_login
from .schemas import AuthStatus, BookItem

logger = logging.getLogger(__name__)


@dataclass
class DownloadResult:
    """ダウンロード 1 件分の結果。"""

    asin: str
    title: str
    ok: bool
    output_path: Optional[str] = None
    message: Optional[str] = None


class KindleService:
    """NoKindle のシングルトンラッパ。"""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._client: Optional[NoKindle] = None
        self._email: Optional[str] = None
        settings.ensure_dirs()

    # --------- 認証 ---------
    def login(self, email: str, password: str, otp_code: Optional[str] = None) -> AuthStatus:
        """自前実装の /auth/register 経路でログイン。

        失敗時は Amazon の生レスポンスを `LoginError.raw_response` に含めて投げる。
        呼び出し元（FastAPI ハンドラ）はこれを UI に返すことで、OTP 以外にどんな
        challenge が要求されているか・エラーコードは何かを切り分けられる。
        """
        with self._lock:
            settings.ensure_dirs()
            # 先に /auth/register で必要なトークンを取って保存しておく。
            amazon_register_login(
                email=email,
                password=password,
                domain=settings.AMAZON_DOMAIN,
                otp_code=(otp_code.strip() if otp_code else None) or None,
            )

            # 保存済みトークンをベースに NoKindle を立ち上げる（refresh 経路で通る）。
            out_dir = settings.OUTPUT_DIR / "DOWNLOADS"
            out_dedrm_dir = settings.OUTPUT_DIR / "DEDRMS"
            out_epub_dir = settings.OUTPUT_DIR
            client = NoKindle(
                email=email,
                password="__already_registered__",
                domain=settings.AMAZON_DOMAIN,
                out_dir=str(out_dir),
                out_dedrm_dir=str(out_dedrm_dir),
                out_epub_dir=str(out_epub_dir),
            )
            if not getattr(client, "tokens", None):
                raise RuntimeError(
                    "トークンの保存には成功しましたが NoKindle の初期化に失敗しました。"
                )
            self._client = client
            self._email = email
            self._persist_session(email)
            return self.status()

    def restore_session(self) -> AuthStatus:
        """保存済みのメールアドレスから自動再ログイン（リフレッシュトークン利用）。"""
        if not settings.SESSION_FILE.exists():
            return AuthStatus(authenticated=False)
        try:
            data = json.loads(settings.SESSION_FILE.read_text(encoding="utf-8"))
            email = data.get("email")
            if not email:
                return AuthStatus(authenticated=False)
            # password を保存していなくても、name ハッシュが一致すれば refresh 経由でログインされる。
            # NoKindle の __init__ には password が必須なのでダミーを渡す。
            dummy_password = "__restored__"
            with self._lock:
                out_dir = settings.OUTPUT_DIR / "DOWNLOADS"
                out_dedrm_dir = settings.OUTPUT_DIR / "DEDRMS"
                out_epub_dir = settings.OUTPUT_DIR
                client = NoKindle(
                    email=email,
                    password=dummy_password,
                    domain=settings.AMAZON_DOMAIN,
                    out_dir=str(out_dir),
                    out_dedrm_dir=str(out_dedrm_dir),
                    out_epub_dir=str(out_epub_dir),
                )
                if not getattr(client, "tokens", None):
                    return AuthStatus(authenticated=False)
                self._client = client
                self._email = email
                return self.status()
        except Exception:
            logger.exception("セッション復元に失敗")
            return AuthStatus(authenticated=False)

    def logout(self) -> None:
        with self._lock:
            self._client = None
            self._email = None
            if settings.SESSION_FILE.exists():
                settings.SESSION_FILE.unlink()

    def status(self) -> AuthStatus:
        if not self._client or not self._email:
            return AuthStatus(authenticated=False)
        return AuthStatus(
            authenticated=True,
            email_hash=hashlib.md5(self._email.encode()).hexdigest()[:8],
            domain=settings.AMAZON_DOMAIN,
        )

    def _persist_session(self, email: str) -> None:
        settings.SESSION_FILE.write_text(
            json.dumps({"email": email, "domain": settings.AMAZON_DOMAIN}, ensure_ascii=False),
            encoding="utf-8",
        )

    # --------- 蔵書 ---------
    def fetch_books(self, force: bool = False) -> list[BookItem]:
        """蔵書一覧を取得。キャッシュがあれば使う。"""
        if not force and settings.BOOKS_CACHE_FILE.exists():
            try:
                cached = json.loads(settings.BOOKS_CACHE_FILE.read_text(encoding="utf-8"))
                return [BookItem(**b) for b in cached]
            except Exception:
                logger.exception("蔵書キャッシュの読み込みに失敗したため再取得します")

        client = self._require_client()
        client.make_library()
        items: list[BookItem] = []
        for book in client.ebooks:
            asin = book.get("ASIN") or book.get("asin") or ""
            title = book.get("title") or "(無題)"
            authors_raw = book.get("authors") or book.get("author") or ""
            if isinstance(authors_raw, list):
                authors = ", ".join(str(a) for a in authors_raw)
            else:
                authors = str(authors_raw)
            items.append(
                BookItem(
                    asin=asin,
                    title=title,
                    authors=authors,
                    acquired_at=str(book.get("purchase_date") or book.get("acquired_at") or ""),
                    content_type=str(book.get("cde_contenttype") or book.get("content_type") or ""),
                )
            )
        settings.BOOKS_CACHE_FILE.write_text(
            json.dumps([i.model_dump() for i in items], ensure_ascii=False),
            encoding="utf-8",
        )
        return items

    # --------- ダウンロード ---------
    def download_one(self, asin: str) -> DownloadResult:
        client = self._require_client()
        settings.ensure_dirs()
        title = self._lookup_title(asin) or asin
        try:
            # get_book は DRM 解除済み EPUB を out_epub_dir/{title}.epub に保存する。
            client.get_book(asin)
            epub_path = self._guess_output_path(title)
            return DownloadResult(
                asin=asin,
                title=title,
                ok=True,
                output_path=str(epub_path) if epub_path else None,
            )
        except Exception as exc:  # noqa: BLE001  - 上位で UI に返す
            logger.exception("download failed for %s", asin)
            return DownloadResult(asin=asin, title=title, ok=False, message=str(exc))

    def _guess_output_path(self, title: str) -> Optional[Path]:
        # NoKindle._save_to_epub はタイトルをファイル名に使う。拡張子違いのこともあるので推定。
        for ext in (".epub", ".azw", ".mobi"):
            candidate = settings.OUTPUT_DIR / f"{title}{ext}"
            if candidate.exists():
                return candidate
        # 見つからなければ出力ディレクトリそのものを返す
        return settings.OUTPUT_DIR

    def _lookup_title(self, asin: str) -> Optional[str]:
        if not settings.BOOKS_CACHE_FILE.exists():
            return None
        try:
            cached = json.loads(settings.BOOKS_CACHE_FILE.read_text(encoding="utf-8"))
            for b in cached:
                if b.get("asin") == asin:
                    return b.get("title")
        except Exception:
            return None
        return None

    # --------- 内部 ---------
    def _require_client(self) -> NoKindle:
        if not self._client:
            raise RuntimeError("未ログインです。先にログインしてください。")
        return self._client


service = KindleService()
