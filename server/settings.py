"""アプリ全体で共有する設定値。

- `OUTPUT_DIR`: EPUB の保存先（/Users/makotofalcon/kindle をデフォルト）
- `STATE_DIR`: セッション情報など内部データの保存先（~/.kindle-web）
"""

from __future__ import annotations

import os
from pathlib import Path


def _resolve_output_dir() -> Path:
    # 環境変数で上書き可能にしつつ、既定値はユーザーの希望通り。
    raw = os.environ.get("KINDLE_WEB_OUTPUT_DIR", "/Users/makotofalcon/kindle")
    return Path(raw).expanduser()


OUTPUT_DIR: Path = _resolve_output_dir()
STATE_DIR: Path = Path(os.environ.get("KINDLE_WEB_STATE_DIR", str(Path.home() / ".kindle-web")))
SESSION_FILE: Path = STATE_DIR / "session.json"
BOOKS_CACHE_FILE: Path = STATE_DIR / "books.json"

# amazon.co.jp 固定運用
AMAZON_DOMAIN: str = "co.jp"

# API ポート
API_HOST: str = os.environ.get("KINDLE_WEB_API_HOST", "127.0.0.1")
API_PORT: int = int(os.environ.get("KINDLE_WEB_API_PORT", "8001"))


def ensure_dirs() -> None:
    """必要なディレクトリを作成。"""
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    STATE_DIR.mkdir(parents=True, exist_ok=True)
