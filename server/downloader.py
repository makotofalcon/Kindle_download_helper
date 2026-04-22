"""ダウンロードキューと SSE 進捗ストリーム。"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, field
from typing import AsyncIterator

from .kindle_service import service
from .schemas import DownloadProgress

logger = logging.getLogger(__name__)


@dataclass
class Job:
    asins: list[str]
    queue: asyncio.Queue[DownloadProgress | None] = field(default_factory=asyncio.Queue)
    done: bool = False


class DownloadManager:
    """同時実行は 1 ジョブ。Amazon 側のリスク管理を避けるためシリアル処理。"""

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._current: Job | None = None

    async def start(self, asins: list[str]) -> Job:
        async with self._lock:
            if self._current and not self._current.done:
                raise RuntimeError("すでに別のダウンロードが進行中です。")
            job = Job(asins=list(asins))
            self._current = job

        asyncio.create_task(self._run(job))
        return job

    async def _run(self, job: Job) -> None:
        try:
            for asin in job.asins:
                await job.queue.put(
                    DownloadProgress(asin=asin, title=asin, status="running")
                )
                result = await asyncio.to_thread(service.download_one, asin)
                await job.queue.put(
                    DownloadProgress(
                        asin=result.asin,
                        title=result.title,
                        status="success" if result.ok else "failed",
                        message=result.message,
                        output_path=result.output_path,
                    )
                )
                # Amazon 側のリスク制御を避けるための軽いディレイ
                await asyncio.sleep(1.5)
        except Exception as exc:  # noqa: BLE001
            logger.exception("download job error")
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
