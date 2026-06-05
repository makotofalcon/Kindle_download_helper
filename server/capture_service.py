"""Kindle Cloud Reader (https://read.amazon.co.jp) を Playwright で自動操作し、
各ページをスクリーンショットして PDF に綴じるサービス。

設計ポイント:
  - Playwright の永続プロファイル (`CHROME_PROFILE_DIR`) を使い、初回だけユーザに
    手動ログイン（OTP を含む）してもらえば以降はセッションが持続する。
  - リーダー下部の "Page/Location X of Y ● Z%" を構造化して進行・終端を判定する。
    ページ送りはナビボタン（左右どちらの配置でも aria-label で特定）をクリックし、
    クリック後に「current/percent が増える or 画面ハッシュが変わる」のを待ってから安定化。
  - 終端は「Next ボタンが消える ＋ percent≧99/current≧total」が揃ったときのみ確定する。
    単なる進行検知失敗を終端へ昇格しないことで、早期終了とページめくり失敗を防ぐ。
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

# 1 冊で想定される最大スクリーン数（暴走防止）
_MAX_PAGES = 4000
# ハッシュ安定化の 1 サンプルあたりの待ち時間（秒）
_POLL_INTERVAL_SEC = 0.3
# 連続して同ハッシュが続いたら "描画安定" と判定する回数
_STABLE_SAMPLES = 4
# ページ送り後に「進行が起きるか」を待つ最大秒数（重い固定レイアウト本に余裕を持たせる）
_ADVANCE_TIMEOUT_SEC = 25.0
# 進行検知後、ハッシュが安定するまで待つ最大秒数
_STABLE_TIMEOUT_SEC = 12.0
# ページめくり失敗時のリトライ回数（フォールバック手段を順に試す）
_TURN_RETRY_MAX = 4
# 終端と見なすパーセンテージのしきい値
_END_PERCENT = 99

# キャプチャ領域: 画面上下の UI chrome (Kindle Library ボタン / フッターの
# 進捗シークバー) を除外。下端には読書進捗バー(seek bar)があるので広めに削る。
_CROP_TOP = 50
_CROP_BOTTOM_MARGIN = 78
# 左右のナビ矢印ボタン (kr-chevron-*) は x=46-94 (prev) / x=1306-1354 (next) に固定
# で width=48。余裕をみて 98px だけ除外すると、本文は幅広く保ちつつ矢印を確実に排除できる。
_CROP_SIDE = 98
_VIEWPORT_W = 1400
_VIEWPORT_H = 1800

# "Page 228 of 230 ● 100%" / "Location 51 of 220 ● 22%" の両形式を構造化する。
_PROGRESS_RE = re.compile(
    r"(Page|Location)\s+([\d,]+)\s+of\s+([\d,]+).*?(\d+)\s*%", re.IGNORECASE
)


@dataclass(frozen=True)
class Progress:
    """リーダー下部のフッターから読み取った進行状況。"""

    unit: str  # "Page" or "Location"
    current: int
    total: int
    percent: int
    raw: str

    @property
    def at_end(self) -> bool:
        return self.percent >= _END_PERCENT or self.current >= self.total


def _parse_progress(text: str) -> Optional[Progress]:
    """フッターテキストを Progress に変換。形式が合わなければ None。"""
    if not text:
        return None
    m = _PROGRESS_RE.search(text)
    if not m:
        return None
    try:
        return Progress(
            unit=m.group(1),
            current=int(m.group(2).replace(",", "")),
            total=int(m.group(3).replace(",", "")),
            percent=int(m.group(4)),
            raw=text.strip(),
        )
    except (ValueError, IndexError):
        return None


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

            # --- DOM 問い合わせヘルパ ------------------------------------
            async def has_next() -> bool:
                try:
                    return await page.evaluate(
                        "()=>document.querySelectorAll('button[aria-label=\"Next page\"]').length>0"
                    )
                except Exception:  # noqa: BLE001
                    return False

            async def has_prev() -> bool:
                try:
                    return await page.evaluate(
                        "()=>document.querySelectorAll('button[aria-label=\"Previous page\"]').length>0"
                    )
                except Exception:  # noqa: BLE001
                    return False

            async def read_progress() -> Optional[Progress]:
                """フッターの "Page/Location X of Y ● Z%" を構造化して返す。"""
                try:
                    txt = await page.evaluate(
                        "() => {"
                        "  const el = document.querySelector('.footer-label.position');"
                        "  return el ? (el.textContent || '').trim() : '';"
                        "}"
                    )
                except Exception:  # noqa: BLE001
                    return None
                return _parse_progress(str(txt))

            async def click_label(label: str, force: bool = False) -> bool:
                """aria-label でナビボタンを Playwright 実クリック。

                `force=True` は actionability チェック（特に hit-test）を飛ばす。
                通常クリックが loader オーバーレイに横取りされて timeout する場合の
                フォールバックに使う。
                """
                try:
                    await page.locator(f'button[aria-label="{label}"]').first.click(
                        timeout=4_000, force=force
                    )
                    return True
                except Exception:  # noqa: BLE001
                    return False

            async def loader_active() -> bool:
                """ページ描画中に出る `.loader`/spinner が現在アクティブかを返す。

                これがアクティブな間にナビボタンをクリックすると、loader が
                pointer event を横取りしてページめくりが空振りする（ページめくり失敗の主因）。
                """
                try:
                    return await page.evaluate(
                        r"""() => {
                            const ls = [...document.querySelectorAll(
                                '.loader, [class*="loader" i], .kg-spinner, [class*="spinner" i]')];
                            return ls.some(l => {
                                const cs = getComputedStyle(l);
                                return l.offsetParent !== null
                                    && cs.display !== 'none'
                                    && parseFloat(cs.opacity || '1') > 0.1
                                    && l.getBoundingClientRect().width > 0;
                            });
                        }"""
                    )
                except Exception:  # noqa: BLE001
                    return False

            async def wait_loader_idle(timeout: float = 15.0) -> bool:
                """loader/spinner が消えるまで待つ。クリック直前の描画完了ゲート。"""
                deadline = asyncio.get_event_loop().time() + timeout
                while asyncio.get_event_loop().time() < deadline:
                    if not await loader_active():
                        return True
                    await asyncio.sleep(0.2)
                return False

            async def screenshot_hash() -> tuple[bytes, str]:
                png = await page.screenshot(type="png", clip=clip)
                return png, hashlib.md5(png).hexdigest()

            # --- 先頭まで巻き戻す（current が下がるのを毎回待つ） ---------
            async def go_to_start(max_clicks: int = 800) -> None:
                pr = await read_progress()
                last_current = pr.current if pr else None
                stuck = 0
                clicks = 0
                for _ in range(max_clicks):
                    pr = await read_progress()
                    if pr and (pr.current <= 1 or pr.percent <= 0):
                        break
                    if not await has_prev():
                        break
                    await wait_loader_idle(timeout=8.0)
                    if not await click_label("Previous page"):
                        # 通常クリックが横取りされたら force で再試行
                        await click_label("Previous page", force=True)
                    clicks += 1
                    # current が下がる（または先頭に到達する）のを最大 5 秒待つ
                    moved = False
                    deadline = asyncio.get_event_loop().time() + 5.0
                    while asyncio.get_event_loop().time() < deadline:
                        await asyncio.sleep(0.25)
                        pr = await read_progress()
                        if pr is None:
                            continue
                        if pr.current <= 1 or pr.percent <= 0:
                            moved = True
                            last_current = pr.current
                            break
                        if last_current is not None and pr.current < last_current:
                            moved = True
                            last_current = pr.current
                            break
                    if not moved:
                        stuck += 1
                        if stuck >= 4:
                            break
                    else:
                        stuck = 0
                final = await read_progress()
                logger.info(
                    "go_to_start: %d prev-clicks, final=%r", clicks, final.raw if final else None
                )

            # --- 現在画面のハッシュが安定するまで待ってスクショ ----------
            async def settle_and_shot(
                initial_hash: Optional[str] = None,
            ) -> tuple[bytes, str]:
                # 描画 loader が消えてからハッシュ安定化（スピナー混入を防ぐ）
                await wait_loader_idle(timeout=_STABLE_TIMEOUT_SEC)
                prev_h: Optional[str] = initial_hash
                stable = 0
                last_png = b""
                deadline = asyncio.get_event_loop().time() + _STABLE_TIMEOUT_SEC
                while asyncio.get_event_loop().time() < deadline:
                    await asyncio.sleep(_POLL_INTERVAL_SEC)
                    png, h = await screenshot_hash()
                    last_png = png
                    if h == prev_h:
                        stable += 1
                        if stable >= _STABLE_SAMPLES:
                            return png, h
                    else:
                        stable = 1
                        prev_h = h
                return last_png, prev_h or ""

            # --- ページ送り 1 回ぶん。戻り値: ("advanced"|"end"|"stuck", png, hash, progress)
            async def turn_and_capture(
                prev_hash: str, prev_progress: Optional[Progress]
            ) -> tuple[str, Optional[bytes], str, Optional[Progress]]:
                # Next ボタンが無い＝終端候補。percent を見て本当の終端か確認。
                if not await has_next():
                    pr = await read_progress()
                    if pr is None or pr.at_end:
                        return "end", None, prev_hash, pr
                    # 終端でないのに Next が無い＝一時的に消えている可能性。少し待って再確認。
                    await asyncio.sleep(1.0)
                    if not await has_next():
                        pr2 = await read_progress()
                        if pr2 and pr2.at_end:
                            return "end", None, prev_hash, pr2
                        # それでも無い＆終端でない → ページめくり手段なし。stuck 扱い。
                        return "stuck", None, prev_hash, pr2 or pr

                # ページめくり手段。ページめくり失敗の主因は「描画中(loader)に
                # クリック→loader が pointer を横取り」なので、各試行前に loader が
                # 消えるのを待ってからクリックする。Next ボタンの再クリックを基本とし、
                # 2回目以降は force=True（hit-test 回避）。ArrowRight は最後の保険。
                async def do_next(force: bool) -> None:
                    await wait_loader_idle(timeout=12.0)
                    ok = await click_label("Next page", force=force)
                    if not ok:
                        await click_label("Next page", force=True)

                turn_methods = [
                    lambda: do_next(False),
                    lambda: do_next(True),
                    lambda: do_next(True),
                    lambda: page.keyboard.press("ArrowRight"),
                ]
                for attempt in range(_TURN_RETRY_MAX):
                    method = turn_methods[min(attempt, len(turn_methods) - 1)]
                    try:
                        await method()
                    except Exception:  # noqa: BLE001
                        pass
                    # 進行（hash変化 or current/percent増加）を待つ
                    deadline = asyncio.get_event_loop().time() + _ADVANCE_TIMEOUT_SEC
                    while asyncio.get_event_loop().time() < deadline:
                        await asyncio.sleep(_POLL_INTERVAL_SEC)
                        png, h = await screenshot_hash()
                        pr = await read_progress()
                        progressed = h != prev_hash
                        if not progressed and pr and prev_progress:
                            progressed = (
                                pr.current > prev_progress.current
                                or pr.percent > prev_progress.percent
                            )
                        if progressed:
                            # 描画が安定するまで待つ
                            spng, sh = await settle_and_shot(h)
                            spr = await read_progress()
                            return "advanced", spng, sh, spr or pr
                    # この試行では進まなかった → 次のフォールバック手段へ
                    logger.info(
                        "turn attempt %d did not advance (prog=%r); retrying",
                        attempt + 1,
                        (await read_progress()),
                    )
                # 全リトライ尽きても進まず
                pr = await read_progress()
                if pr and pr.at_end:
                    return "end", None, prev_hash, pr
                return "stuck", None, prev_hash, pr

            # === 本編 ===
            await asyncio.sleep(2.0)
            initial = await read_progress()
            logger.info(
                "initial progress=%r (will rewind to start)", initial.raw if initial else None
            )

            await go_to_start()
            await asyncio.sleep(1.0)

            first_png, first_hash = await settle_and_shot()
            images: list[Image.Image] = [
                Image.open(io.BytesIO(first_png)).convert("RGB")
            ]
            prev_hash = first_hash
            prev_progress = await read_progress()
            yield PageEvent(
                asin=asin,
                title=title,
                page=1,
                total=None,
                status="capturing",
                message=prev_progress.raw if prev_progress else None,
            )

            ended_cleanly = False
            for _ in range(2, _MAX_PAGES + 1):
                outcome, png, new_hash, new_progress = await turn_and_capture(
                    prev_hash, prev_progress
                )
                if outcome == "end":
                    logger.info(
                        "end of book reached (progress=%r)",
                        new_progress.raw if new_progress else None,
                    )
                    ended_cleanly = True
                    break
                if outcome == "stuck":
                    # 終端でないのにページめくりできなかった → 失敗として明示。
                    logger.warning(
                        "page turn stuck before end (progress=%r)",
                        new_progress.raw if new_progress else None,
                    )
                    yield PageEvent(
                        asin=asin,
                        title=title,
                        page=len(images),
                        total=None,
                        status="failed",
                        message=(
                            "ページめくりに失敗しました（終端ではありません）。"
                            f" 進行={new_progress.raw if new_progress else '不明'}。"
                            " 途中までの内容は保存します。"
                        ),
                    )
                    break

                # advanced
                if png is not None:
                    images.append(Image.open(io.BytesIO(png)).convert("RGB"))
                prev_hash = new_hash
                prev_progress = new_progress  # P1: 進行のたびに必ず更新
                logger.info(
                    "captured page %d (progress=%r)",
                    len(images),
                    new_progress.raw if new_progress else None,
                )
                yield PageEvent(
                    asin=asin,
                    title=title,
                    page=len(images),
                    total=None,
                    status="capturing",
                    message=new_progress.raw if new_progress else None,
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

            logger.info(
                "capture finished: %d screens, ended_cleanly=%s", len(images), ended_cleanly
            )
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
