"""ログ設定。

グローバル状態を増やさないよう、設定はこの関数からのみ行う。
"""

from __future__ import annotations

import logging
import sys

LOG_FORMAT = "%(asctime)s %(levelname)-7s [%(name)s] %(message)s"
DATE_FORMAT = "%Y-%m-%d %H:%M:%S"


def setup_logging(level: str = "INFO") -> None:
    """標準出力へログを出す（GitHub Actions のログにそのまま残る）。"""
    # Windows のコンソール既定エンコーディング（cp932）だと日本語ログが化けるため、
    # 可能なら UTF-8 に切り替える。
    try:
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
    except (AttributeError, OSError):
        pass

    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format=LOG_FORMAT,
        datefmt=DATE_FORMAT,
        stream=sys.stdout,
        force=True,
    )
    # requests / urllib3 の DEBUG ログは冗長なので抑制する
    logging.getLogger("urllib3").setLevel(logging.WARNING)
