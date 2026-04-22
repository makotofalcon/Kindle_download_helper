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
# ハッシュ安定化の 1 サンプルあたりの待ち時間（秒）
_POLL_INTERVAL_SEC = 0.3
# 連続して同ハッシュが続いたら "描画安定" と判定する回数
_STABLE_SAMPLES = 4
# 安定化待ちの最大試行数（実時間 = これ * POLL_INTERVAL_SEC）
_MAX_STABLE_POLLS = 40  # 12 秒上限
# "終端" 判定: 同じ安定ハッシュが続いた回数（ArrowRight でも変化しなかった）
_END_REPEAT_THRESHOLD = 3

# キャプチャ領域: 画面上下の UI chrome (Kindle Library ボタン / Location バー) を除外
_CROP_TOP = 50
_CROP_BOTTOM_MARGIN = 50
# 左右のナビ矢印ボタン (kr-chevron-*) は x=46-94 (prev) / x=1306-1354 (next) に固定
# で width=48。余裕をみて 98px だけ除外すると、本文は幅広く保ちつつ矢印を確実に排除できる。
_CROP_SIDE = 98
_VIEWPORT_W = 1400
_VIEWPORT_H = 1800



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
            # 以前の異常終了で残った SingletonLock を掃除する（残っていると
            # "Failed to create a ProcessSingleton" で launch できない）
            for name in ("SingletonLock", "SingletonSocket", "SingletonCookie"):
                stale = settings.CHROME_PROFILE_DIR / name
                try:
                    stale.unlink(missing_ok=True)
                except Exception:  # noqa: BLE001
                    pass

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

        実際の DOM（2026-04 時点）:
            <li id="library-item-option-{ASIN}" role="listitem">
              <div id="title-{ASIN}"><p>...タイトル...</p></div>
              <div id="author-{ASIN}"><p>...著者...</p></div>
            </li>

        仮想化されているので、End キーでスクロールして追加ロードを繰り返す。
        """
        ctx = await self._ensure_context()
        page = await ctx.new_page()
        try:
            await page.goto(
                "https://read.amazon.co.jp/kindle-library",
                wait_until="domcontentloaded",
                timeout=60_000,
            )
            try:
                await page.wait_for_selector(
                    "li[id^='library-item-option-']", timeout=30_000
                )
            except PWTimeoutError as exc:
                raise RuntimeError(
                    "蔵書ページの描画待ちでタイムアウト。Cloud Reader にログインしていますか？"
                ) from exc

            # フォーカスを移してから End キーで末尾まで読み込む
            await page.click("body")
            seen = 0
            stale = 0
            for _ in range(120):
                count = await page.evaluate(
                    "() => document.querySelectorAll(\"li[id^='library-item-option-']\").length"
                )
                if count == seen:
                    stale += 1
                    if stale >= 4:
                        break
                else:
                    stale = 0
                    seen = count
                await page.keyboard.press("End")
                await asyncio.sleep(0.5)

            raw = await page.evaluate(
                """() => {
                    const items = [];
                    document.querySelectorAll("li[id^='library-item-option-']").forEach(li => {
                        const asin = li.id.replace(/^library-item-option-/, '');
                        if (!asin) return;
                        const titleEl = li.querySelector(`#title-${asin} p`)
                            || li.querySelector("[id^='title-'] p")
                            || li.querySelector("p");
                        const authorEl = li.querySelector(`#author-${asin} p`)
                            || li.querySelector("[id^='author-'] p");
                        const title = (titleEl?.textContent || '').trim();
                        const authors = (authorEl?.textContent || '').trim();
                        items.push({ asin, title, authors });
                    });
                    // 重複排除
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
                    authors=str(item.get("authors") or ""),
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
            logger.info("capture_book: open reader asin=%s title=%s", asin, title)
            page = await ctx.new_page()
            await page.goto(
                f"https://read.amazon.co.jp/?asin={asin}",
                wait_until="domcontentloaded",
                timeout=90_000,
            )
            logger.info("capture_book: page loaded, url=%s", page.url)
            try:
                await page.wait_for_load_state("networkidle", timeout=60_000)
            except PWTimeoutError:
                logger.warning("networkidle timeout (続行)")
            await asyncio.sleep(2.0)

            # UI chrome（ナビ矢印・ブックマーク・進捗バー等）は意図的に非表示化しない。
            # `display:none`/`visibility:hidden` すると React の onClick が届かず、
            # `opacity:0` でも chrome 自体の click 判定は残るためキャプチャに写らない工夫が必要。
            # → ナビ矢印は画面端に固定されているので、`clip` で左右マージンを除外する方式に切替。

            # Kindle Cloud Reader は初回オープン時に "Most Recent Page Read" ダイアログ
            # （別端末で進めた位置へ飛ぶか？）を表示することがある。これを閉じないと
            # 全てのクリックがダイアログに吸われて次ページに進めない。No を押す。
            # 念のため他の似たダイアログも閉じる。
            for _ in range(3):
                closed = False
                try:
                    dialog = page.locator(
                        'ion-alert .alert-button:has-text("No")'
                    ).first
                    if await dialog.count() > 0 and await dialog.is_visible():
                        await dialog.click(timeout=2_000)
                        logger.info("dismissed 'Most Recent Page Read' dialog")
                        closed = True
                except Exception:  # noqa: BLE001
                    pass
                if not closed:
                    break
                await asyncio.sleep(1.0)

            # 本文エリアだけをキャプチャする clip（上下バーとナビ矢印を除外）
            clip = {
                "x": _CROP_SIDE,
                "y": _CROP_TOP,
                "width": _VIEWPORT_W - 2 * _CROP_SIDE,
                "height": _VIEWPORT_H - _CROP_TOP - _CROP_BOTTOM_MARGIN,
            }

            async def click_next_page() -> bool:
                """Playwright の実クリックで `aria-label="Next page"` ボタンを押す。
                React の onClick は native click event で発火するので Playwright の
                mouse pointer 経由クリックが確実。ボタンが消えていればタイムアウトして False。
                """
                try:
                    await page.locator(
                        'button[aria-label="Next page"]'
                    ).first.click(timeout=5_000)
                    return True
                except Exception:  # noqa: BLE001
                    return False

            async def click_prev_page() -> bool:
                try:
                    await page.locator(
                        'button[aria-label="Previous page"]'
                    ).first.click(timeout=5_000)
                    return True
                except Exception:  # noqa: BLE001
                    return False

            async def go_to_start(max_clicks: int = 600) -> None:
                """Previous page ボタンを連打して先頭まで戻る。Location が変化しなくなったら終了。"""
                last_loc = ""
                stale = 0
                for idx in range(max_clicks):
                    ok = await click_prev_page()
                    if not ok:
                        break
                    await asyncio.sleep(0.25)
                    curr = await get_location()
                    if curr == last_loc:
                        stale += 1
                        if stale >= 5:
                            break
                    else:
                        stale = 0
                        last_loc = curr
                logger.info("go_to_start: clicked %d times, last_loc=%r", idx + 1, last_loc)

            async def get_location() -> str:
                """リーダー下部の "Location X of Y ● Z%" テキストを返す。
                CSS で hidden にした要素でも textContent なら値が取れる。
                これにより「本物の白紙ページ」と「ローディング中の白画面」を区別する。
                """
                try:
                    txt = await page.evaluate(
                        "() => {"
                        "  const el = document.querySelector('.footer-label.position');"
                        "  return el ? (el.textContent || '').trim() : '';"
                        "}"
                    )
                    return str(txt)
                except Exception:  # noqa: BLE001
                    return ""

            async def wait_location_change(
                prev: str, timeout_sec: float = 30.0
            ) -> tuple[bool, str]:
                """location が prev 以外の「非空な値」になるまで待つ。
                遷移中に `.footer-label.position` が一時的に DOM から消えて
                `''` を返すことがあるが、それはタイムアウトに数えず polling を継続する。
                """
                deadline = asyncio.get_event_loop().time() + timeout_sec
                last_non_empty = prev
                while asyncio.get_event_loop().time() < deadline:
                    await asyncio.sleep(0.3)
                    curr = await get_location()
                    if not curr:
                        # 遷移中の一時的消失。無視して再ポーリング。
                        continue
                    if curr != prev:
                        return True, curr
                    last_non_empty = curr
                return False, last_non_empty

            async def capture_stable() -> tuple[bytes, str]:
                """現在の画面が変化しなくなるまで待ち、安定したスクショを返す。"""
                prev: Optional[str] = None
                stable = 0
                last_png = b""
                for _ in range(_MAX_STABLE_POLLS):
                    await asyncio.sleep(_POLL_INTERVAL_SEC)
                    png = await page.screenshot(type="png", clip=clip)
                    h = hashlib.md5(png).hexdigest()
                    if h == prev:
                        stable += 1
                        if stable >= _STABLE_SAMPLES:
                            return png, h
                    else:
                        stable = 0
                        prev = h
                        last_png = png
                # タイムアウト: 最後のサンプルを返す
                return last_png, prev or ""

            # 初回描画: Location が確定するまで少し待つ（描画完了を待つ）
            await asyncio.sleep(2.0)
            initial_location = await get_location()
            logger.info("initial location=%r (will rewind to start)", initial_location)

            # 先頭まで戻ってから計測開始
            await go_to_start()
            await asyncio.sleep(1.5)
            prev_location = await get_location()
            logger.info("location after rewind=%r", prev_location)

            first_png, first_hash = await capture_stable()
            images: list[Image.Image] = [
                Image.open(io.BytesIO(first_png)).convert("RGB")
            ]
            prev_hash = first_hash
            yield PageEvent(
                asin=asin, title=title, page=1, total=None, status="capturing"
            )

            # 終端判定: 「Location も hash も変わらない」が連続すれば終わり。
            # `.footer-label.position` はページ遷移中に一時的に空になるため、Location
            # だけでは誤検知しやすい。本が重いと Location 表示の反映が遅いので、
            # hash 変化も併用することで確実にページ送りを検出する。
            still_count = 0
            for i in range(2, _MAX_PAGES + 1):
                ok = await click_next_page()
                if not ok:
                    logger.info("click_next_page failed; treating as end of book")
                    break

                changed_loc, new_location = await wait_location_change(
                    prev_location, timeout_sec=10.0
                )
                # いずれにせよ hash 安定化を待つ（本が重い本では Location 反映より
                # 画面描画の方が早く終わるケースがある）
                png, new_hash = await capture_stable()

                if changed_loc and new_location:
                    # Location が明らかに変わった = 新ページ確定
                    advance_reason = f"loc {prev_location!r}->{new_location!r}"
                    prev_location = new_location
                elif new_hash != prev_hash:
                    # Location 表示は追随してないが、画面は描画し直されている。
                    # → 新ページと見なす
                    advance_reason = f"hash changed ({prev_hash[:8]}->{new_hash[:8]}) while loc stayed"
                else:
                    # どちらも変わらず
                    still_count += 1
                    logger.info(
                        "page did not advance (loc=%r hash unchanged, still_count=%d)",
                        new_location,
                        still_count,
                    )
                    if still_count >= _END_REPEAT_THRESHOLD:
                        logger.info("end of book detected")
                        break
                    continue

                still_count = 0
                prev_hash = new_hash
                images.append(Image.open(io.BytesIO(png)).convert("RGB"))
                logger.info("captured page %d (%s)", len(images), advance_reason)
                yield PageEvent(
                    asin=asin,
                    title=title,
                    page=len(images),
                    total=None,
                    status="capturing",
                    message=advance_reason,
                )

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
