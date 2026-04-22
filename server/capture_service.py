"""Kindle Cloud Reader (https://read.amazon.co.jp) を Playwright で自動操作し、
各ページをスクリーンショットして PDF に綴じるサービス。

設計ポイント:
  - Playwright の永続プロファイル (`CHROME_PROFILE_DIR`) を使い、初回だけユーザに
    手動ログイン（OTP を含む）してもらえば以降はセッションが持続する。
  - ページ送りは keyboard `ArrowRight` を押して 0.8 秒待ち、スクショをハッシュ比較。
    連続 3 回変化しなければ本の終端とみなして終了。
  - 出力: `OUTPUT_DIR/{サニタイズ済みタイトル}.pdf`
"""

from __future__ import annotations

import asyncio
import hashlib
import io
import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import AsyncIterator, Optional

from PIL import Image
from playwright.async_api import (
    BrowserContext,
    Page,
    TimeoutError as PWTimeoutError,
    async_playwright,
)

from . import settings
from .schemas import BookItem

logger = logging.getLogger(__name__)

# 1 冊で想定される最大ページ数（暴走防止）
_MAX_PAGES = 3000
# ページ送り後、描画安定を待つ時間（秒）
_PAGE_SETTLE_SEC = 0.9
# 終端判定: 連続して同一ハッシュが続いたら終わり
_END_REPEAT_THRESHOLD = 3


@dataclass
class PageEvent:
    asin: str
    title: str
    page: int
    total: Optional[int]
    status: str  # "starting" | "capturing" | "writing_pdf" | "done" | "failed"
    message: Optional[str] = None
    output_path: Optional[str] = None


def _sanitize_filename(name: str, max_len: int = 120) -> str:
    cleaned = re.sub(r'[\\/:*?"<>|\r\n\t]', "_", name).strip() or "book"
    if len(cleaned) > max_len:
        cleaned = cleaned[: max_len - 3] + "..."
    return cleaned


class CaptureService:
    """Playwright の永続ブラウザコンテキストを 1 個だけ持つシングルトン。"""

    def __init__(self) -> None:
        self._pw = None  # playwright instance
        self._ctx: Optional[BrowserContext] = None
        self._lock = asyncio.Lock()

    async def _ensure_context(self) -> BrowserContext:
        async with self._lock:
            if self._ctx:
                return self._ctx
            settings.ensure_dirs()
            settings.CHROME_PROFILE_DIR.mkdir(parents=True, exist_ok=True)

            self._pw = await async_playwright().start()
            # Kindle Cloud Reader は自動化検知がそれほど強くないため、
            # headed（ユーザが目視できる）で十分。初回ログインのために必須。
            self._ctx = await self._pw.chromium.launch_persistent_context(
                user_data_dir=str(settings.CHROME_PROFILE_DIR),
                headless=False,
                viewport={"width": 1400, "height": 1800},
                device_scale_factor=2,
                locale="ja-JP",
                args=[
                    "--disable-blink-features=AutomationControlled",
                ],
            )
            return self._ctx

    async def fetch_library(self) -> list[BookItem]:
        """Cloud Reader の kindle-library ページから蔵書一覧をスクレイプする。

        ページ上の DOM は頻繁に変わる可能性があるため、以下の手順で頑健に取得する:
          1. library ページを開く
          2. `asin` 属性や data-asin を持つ要素を全て列挙（カード/ボタンのどちらでも）
          3. タイトルは同カード内の aria-label または子要素 text から推定
          4. 遅延ロード対策として PageDown でスクロールしながら繰り返し走査
        """
        ctx = await self._ensure_context()
        page = await ctx.new_page()
        try:
            await page.goto(
                "https://read.amazon.co.jp/kindle-library",
                wait_until="domcontentloaded",
                timeout=60_000,
            )
            # library grid が出るまで待つ
            try:
                await page.wait_for_selector(
                    "[data-asin], [id^='cover-image-']", timeout=30_000
                )
            except PWTimeoutError:
                raise RuntimeError(
                    "蔵書ページの描画待ちでタイムアウト。Cloud Reader にログインしていますか？"
                )

            # 全件ロードのためスクロールして書籍数が増えなくなるまで繰り返す
            seen = 0
            stale = 0
            for _ in range(60):
                count = await page.evaluate(
                    "() => document.querySelectorAll('[data-asin]').length"
                )
                if count == seen:
                    stale += 1
                    if stale >= 3:
                        break
                else:
                    stale = 0
                    seen = count
                await page.keyboard.press("End")
                await asyncio.sleep(0.6)

            raw = await page.evaluate(
                """() => {
                    const items = [];
                    document.querySelectorAll('[data-asin]').forEach(el => {
                        const asin = el.getAttribute('data-asin');
                        if (!asin) return;
                        // タイトルの候補を複数試す
                        const aria = el.getAttribute('aria-label')
                            || el.querySelector('[aria-label]')?.getAttribute('aria-label')
                            || '';
                        const titleEl = el.querySelector('[class*=title i], [data-testid*=title i]');
                        const alt = el.querySelector('img')?.getAttribute('alt') || '';
                        const title = (titleEl?.textContent || aria || alt || '').trim();
                        items.push({ asin, title });
                    });
                    // 重複排除 (asin で)
                    const seen = new Set();
                    return items.filter(i => {
                        if (seen.has(i.asin)) return false;
                        seen.add(i.asin);
                        return true;
                    });
                }"""
            )
            return [
                BookItem(
                    asin=str(item["asin"]),
                    title=str(item.get("title") or item["asin"]),
                )
                for item in raw
            ]
        finally:
            await page.close()

    async def ensure_login(self) -> dict[str, bool | str]:
        """Cloud Reader を開き、ログイン済みなら `authenticated=True` を返す。
        未ログインならブラウザが表示されるので、ユーザーが手動でログインしたあと再呼び出し。
        """
        ctx = await self._ensure_context()
        page = await ctx.new_page()
        try:
            await page.goto(
                "https://read.amazon.co.jp/kindle-library",
                wait_until="domcontentloaded",
                timeout=60_000,
            )
            # Cloud Reader は読み込み後に 'library' grid / 'ap_email' 等で分岐する。
            # login 画面なら URL に /ap/signin が含まれる。
            try:
                await page.wait_for_url(
                    "**/kindle-library**", timeout=15_000
                )
                authenticated = True
                message = "ログイン済みです。"
            except PWTimeoutError:
                authenticated = False
                message = (
                    "ブラウザで amazon.co.jp にログイン（OTP含む）してください。"
                    " このウィンドウ内でログイン完了後、UIの「ログイン確認」を再度押してください。"
                )
            return {"authenticated": authenticated, "message": message}
        finally:
            await page.close()

    async def capture_book(
        self, asin: str, title: str
    ) -> AsyncIterator[PageEvent]:
        """1 冊分のキャプチャを実行し、進捗を非同期イテレータで流す。"""
        settings.ensure_dirs()
        ctx = await self._ensure_context()
        safe_title = _sanitize_filename(title)
        out_pdf = settings.OUTPUT_DIR / f"{safe_title}.pdf"

        yield PageEvent(asin=asin, title=title, page=0, total=None, status="starting")

        page: Optional[Page] = None
        try:
            page = await ctx.new_page()
            # 本ごとのリーダー URL を直接開く
            await page.goto(
                f"https://read.amazon.co.jp/?asin={asin}",
                wait_until="domcontentloaded",
                timeout=90_000,
            )
            # 「本文が表示された」目安: body が現れ、読み込み中インジケータが消える
            await page.wait_for_load_state("networkidle", timeout=60_000)
            await asyncio.sleep(2.0)

            # クリックしてフォーカスを本文に（キーボード送りを有効化するため）
            try:
                await page.click("body")
            except Exception:  # noqa: BLE001
                pass

            images: list[Image.Image] = []
            prev_hash: Optional[str] = None
            repeat = 0
            for i in range(1, _MAX_PAGES + 1):
                await asyncio.sleep(_PAGE_SETTLE_SEC)
                png_bytes = await page.screenshot(type="png", full_page=False)
                h = hashlib.md5(png_bytes).hexdigest()
                if h == prev_hash:
                    repeat += 1
                    if repeat >= _END_REPEAT_THRESHOLD:
                        # 連続で同じ画面 → 終端
                        break
                else:
                    repeat = 0
                    prev_hash = h
                    img = Image.open(io.BytesIO(png_bytes)).convert("RGB")
                    images.append(img)
                    yield PageEvent(
                        asin=asin,
                        title=title,
                        page=len(images),
                        total=None,
                        status="capturing",
                    )
                await page.keyboard.press("ArrowRight")

            if not images:
                yield PageEvent(
                    asin=asin,
                    title=title,
                    page=0,
                    total=0,
                    status="failed",
                    message="スクリーンショットが 1 枚も撮れませんでした。",
                )
                return

            yield PageEvent(
                asin=asin,
                title=title,
                page=len(images),
                total=len(images),
                status="writing_pdf",
            )
            # Pillow で PDF に綴じる
            first, rest = images[0], images[1:]
            out_pdf.parent.mkdir(parents=True, exist_ok=True)
            first.save(
                out_pdf,
                "PDF",
                save_all=True,
                append_images=rest,
                resolution=150.0,
            )

            yield PageEvent(
                asin=asin,
                title=title,
                page=len(images),
                total=len(images),
                status="done",
                output_path=str(out_pdf),
            )
        except Exception as exc:  # noqa: BLE001
            logger.exception("capture failed: %s", asin)
            yield PageEvent(
                asin=asin,
                title=title,
                page=0,
                total=None,
                status="failed",
                message=str(exc),
            )
        finally:
            if page:
                try:
                    await page.close()
                except Exception:  # noqa: BLE001
                    pass

    async def shutdown(self) -> None:
        async with self._lock:
            if self._ctx:
                try:
                    await self._ctx.close()
                except Exception:  # noqa: BLE001
                    pass
                self._ctx = None
            if self._pw:
                try:
                    await self._pw.stop()
                except Exception:  # noqa: BLE001
                    pass
                self._pw = None


service = CaptureService()
