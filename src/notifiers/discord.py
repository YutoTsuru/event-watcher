"""Discord Webhook への通知。

Webhook URL は環境変数 DISCORD_WEBHOOK_URL から取得する。
コード・設定ファイルには絶対に書かないこと。
"""

from __future__ import annotations

import logging
import os
from typing import Any, Optional

import requests

from src.models import Event
from src.notifiers.base import Notifier

logger = logging.getLogger(__name__)

WEBHOOK_ENV_VAR = "DISCORD_WEBHOOK_URL"
EMBED_COLOR = 0x5865F2


class DiscordNotifierError(Exception):
    """Webhook URL が未設定など、Notifier を構築できない場合。"""


class DiscordNotifier(Notifier):
    """Discord Webhook に埋め込みメッセージを 1 件ずつ送る。"""

    def __init__(self, webhook_url: str, timeout_seconds: float = 10.0) -> None:
        if not webhook_url:
            raise DiscordNotifierError("Webhook URL が空です")
        self._webhook_url = webhook_url
        self._timeout_seconds = timeout_seconds

    @classmethod
    def from_env(cls, timeout_seconds: float = 10.0) -> "DiscordNotifier":
        webhook_url = os.environ.get(WEBHOOK_ENV_VAR, "").strip()
        if not webhook_url:
            raise DiscordNotifierError(
                f"環境変数 {WEBHOOK_ENV_VAR} が設定されていません"
            )
        return cls(webhook_url, timeout_seconds=timeout_seconds)

    def notify(self, event: Event) -> bool:
        payload = build_payload(event)
        try:
            response = requests.post(
                self._webhook_url,
                json=payload,
                timeout=self._timeout_seconds,
            )
            response.raise_for_status()
        except requests.RequestException as exc:
            # URL 自体は秘密情報なのでログに出さない
            logger.error("Discord への通知に失敗しました url=%s: %s", event.url, exc)
            return False

        logger.info("Discord へ通知しました: %s", event.title)
        return True


def build_payload(event: Event) -> dict[str, Any]:
    """Event を Discord Webhook のリクエストボディに変換する。"""
    fields: list[dict[str, Any]] = [
        {"name": "ソース", "value": event.source or "unknown", "inline": True},
    ]
    if event.matched_targets:
        fields.append(
            {"name": "一致した対象", "value": ", ".join(event.matched_targets), "inline": True}
        )
    published = _format_datetime(event.published_at)
    if published:
        fields.append({"name": "公開日時", "value": published, "inline": False})

    return {
        "embeds": [
            {
                "title": event.title[:250],
                "url": event.url,
                "color": EMBED_COLOR,
                "fields": fields,
            }
        ]
    }


def _format_datetime(value: Optional[Any]) -> Optional[str]:
    if value is None:
        return None
    try:
        return value.strftime("%Y-%m-%d %H:%M %Z").strip()
    except (AttributeError, ValueError):
        return str(value)
