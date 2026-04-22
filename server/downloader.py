"""キャプチャ（Kindle Cloud Reader → PDF）ジョブの管理と SSE 進捗ストリーム。"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, field
from typing import AsyncIterator

from .capture_service import service as capture_service
from .schemas import DownloadProgress

logger = logging.getLogger(__name__)


@dataclass
class Job:
    # (asin, title) のタプルで受け取って、キャプチャ時にタイトルをファイル名に使う
    items: list[tuple[str, str]]
    queue: asyncio.Queue[DownloadProgress | None] = field(default_factory=asyncio.Queue)
    done: bool = False


class DownloadManager:
    """同時実行は 1 ジョブ。Kindle Cloud Reader を 1 枚ずつスクショするためシリアル処理。"""

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._current: Job | None = None

    async def start(self, items: list[tuple[str, str]]) -> Job:
        async with self._lock:
            if self._current and not self._current.done:
                raise RuntimeError("すでに別のジョブが進行中です。")
            job = Job(items=list(items))
            self._current = job

        asyncio.create_task(self._run(job))
        return job

    async def _run(self, job: Job) -> None:
        try:
            for asin, title in job.items:
                await job.queue.put(
                    DownloadProgress(
                        asin=asin, title=title, status="running", message="開始中"
                    )
                )
                try:
                    async for ev in capture_service.capture_book(asin, title):
                        # Playwright 層のイベントを UI 用の DownloadProgress に翻訳
                        if ev.status == "capturing":
                            msg = f"キャプチャ中: {ev.page} ページ目"
                            ui_status = "running"
                        elif ev.status == "writing_pdf":
                            msg = f"PDF 作成中 ({ev.total}ページ)"
                            ui_status = "running"
                        elif ev.status == "done":
                            msg = f"完了 ({ev.total}ページ)"
                            ui_status = "success"
                        elif ev.status == "failed":
                            msg = ev.message or "失敗"
                            ui_status = "failed"
                        else:
                            msg = ev.status
                            ui_status = "running"
                        await job.queue.put(
                            DownloadProgress(
                                asin=ev.asin,
                                title=ev.title,
                                status=ui_status,
                                message=msg,
                                output_path=ev.output_path,
                            )
                        )
                except Exception as exc:  # noqa: BLE001
                    logger.exception("capture failed asin=%s", asin)
                    await job.queue.put(
                        DownloadProgress(
                            asin=asin, title=title, status="failed", message=str(exc)
                        )
                    )
                # 連続キャプチャ時のクールダウン
                await asyncio.sleep(1.0)
        except Exception as exc:  # noqa: BLE001
            logger.exception("job error")
            await job.queue.put(
                DownloadProgress(
                    asin="-",
                    title="-",
                    status="failed",
                    message=f"ジョブ全体で失敗: {exc}",
                )
            )
        finally:
            job.done = True
            await job.queue.put(None)  # 終端シグナル

    def current(self) -> Job | None:
        return self._current


async def sse_stream(job: Job) -> AsyncIterator[bytes]:
    """SSE フォーマットで進捗をストリームする。"""
    while True:
        item = await job.queue.get()
        if item is None:
            # 完了イベント
            yield b"event: done\ndata: {}\n\n"
            return
        payload = json.dumps(item.model_dump(), ensure_ascii=False)
        yield f"data: {payload}\n\n".encode("utf-8")


manager = DownloadManager()
