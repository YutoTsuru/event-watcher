"""Discord Webhook への通知。

Webhook URL は環境変数 DISCORD_WEBHOOK_URL から取得する。
コード・設定ファイルには絶対に書かないこと。

Discord は 1 メッセージに最大 10 個の embed を載せられる。過去分をまとめて
通知する場合に 1 件 1 リクエストだとレート制限（429）に掛かるため、
notify_batch() で 10 件ずつ 1 リクエストにまとめて送る。
"""

from __future__ import annotations

import logging
import os
import time
from typing import Any, Optional, Sequence

import requests

from src.models import Event
from src.notifiers.base import Notifier

logger = logging.getLogger(__name__)

WEBHOOK_ENV_VAR = "DISCORD_WEBHOOK_URL"
EMBED_COLOR = 0x5865F2

# Discord の仕様上、1 メッセージあたりの embed 数の上限
MAX_EMBEDS_PER_MESSAGE = 10
# 429 を受けたときに待って再送する最大回数
MAX_RATE_LIMIT_RETRIES = 3
# Retry-After が読めなかった場合の待ち時間（秒）
FALLBACK_RETRY_AFTER_SECONDS = 2.0
# Retry-After が異常に長い場合でも、ここで頭打ちにする（秒）
MAX_RETRY_AFTER_SECONDS = 30.0


class DiscordNotifierError(Exception):
    """Webhook URL が未設定など、Notifier を構築できない場合。"""


class DiscordNotifier(Notifier):
    """Discord Webhook に埋め込みメッセージを送る。"""

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
        if not self._post(build_payload(event), event.url):
            return False
        logger.info("Discord へ通知しました: %s", event.title)
        return True

    def notify_batch(self, events: Sequence[Event]) -> list[bool]:
        """10 件ずつ 1 メッセージにまとめて送る。

        まとめ送信は 1 リクエスト単位でしか成否が分からないため、
        同じチャンク内のイベントには同じ結果を返す。
        """
        results: list[bool] = []
        for chunk in _chunked(list(events), MAX_EMBEDS_PER_MESSAGE):
            payload = {"embeds": [build_embed(event) for event in chunk]}
            label = f"{len(chunk)} 件まとめ送信 (先頭: {chunk[0].url})"
            succeeded = self._post(payload, label)
            if succeeded:
                logger.info("Discord へ %d 件まとめて通知しました", len(chunk))
            results.extend([succeeded] * len(chunk))
        return results

    def _post(self, payload: dict[str, Any], label: str) -> bool:
        """Webhook に POST する。429 のときだけ Retry-After に従って再送する。"""
        for attempt in range(MAX_RATE_LIMIT_RETRIES + 1):
            try:
                response = requests.post(
                    self._webhook_url,
                    json=payload,
                    timeout=self._timeout_seconds,
                )
                if response.status_code == 429 and attempt < MAX_RATE_LIMIT_RETRIES:
                    wait_seconds = _retry_after_seconds(response)
                    logger.warning(
                        "Discord のレート制限に掛かりました。%.1f 秒待って再送します (%d/%d)",
                        wait_seconds,
                        attempt + 1,
                        MAX_RATE_LIMIT_RETRIES,
                    )
                    time.sleep(wait_seconds)
                    continue
                response.raise_for_status()
            except requests.RequestException as exc:
                # URL 自体は秘密情報なのでログに出さない
                logger.error("Discord への通知に失敗しました target=%s: %s", label, exc)
                return False
            return True

        logger.error("Discord のレート制限が解消せず通知できませんでした target=%s", label)
        return False


def _chunked(items: list[Event], size: int) -> list[list[Event]]:
    return [items[i : i + size] for i in range(0, len(items), size)]


def _retry_after_seconds(response: requests.Response) -> float:
    """429 レスポンスから待ち時間を取り出す（読めなければ既定値）。"""
    raw = response.headers.get("Retry-After")
    try:
        seconds = float(raw) if raw is not None else FALLBACK_RETRY_AFTER_SECONDS
    except (TypeError, ValueError):
        seconds = FALLBACK_RETRY_AFTER_SECONDS
    if seconds <= 0:
        seconds = FALLBACK_RETRY_AFTER_SECONDS
    return min(seconds, MAX_RETRY_AFTER_SECONDS)


def build_embed(event: Event) -> dict[str, Any]:
    """Event を Discord の embed 1 個に変換する。"""
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
        "title": event.title[:250],
        "url": event.url,
        "color": EMBED_COLOR,
        "fields": fields,
    }


def build_payload(event: Event) -> dict[str, Any]:
    """Event 1 件を Discord Webhook のリクエストボディに変換する。"""
    return {"embeds": [build_embed(event)]}


def _format_datetime(value: Optional[Any]) -> Optional[str]:
    if value is None:
        return None
    try:
        return value.strftime("%Y-%m-%d %H:%M %Z").strip()
    except (AttributeError, ValueError):
        return str(value)
