"""RSS/Atom フィードからの記事収集。"""

from __future__ import annotations

import html
import logging
import re
from datetime import datetime, timezone
from typing import Any, Optional

import feedparser
import requests

from src.collectors.base import Collector
from src.models import Article

logger = logging.getLogger(__name__)

USER_AGENT = "event-watcher/0.1"

# 要約として保存する最大文字数（通知時はさらに短く切り詰める）
MAX_SUMMARY_LENGTH = 400


class RSSCollector(Collector):
    """1 フィード = 1 インスタンス。

    HTTP 取得は requests で行い（feedparser 単体ではタイムアウトを指定しづらいため）、
    取得したバイト列だけを feedparser に渡す。
    """

    def __init__(self, name: str, url: str, timeout_seconds: float = 10.0) -> None:
        self._name = name
        self._url = url
        self._timeout_seconds = timeout_seconds

    @property
    def name(self) -> str:
        return self._name

    def collect(self) -> list[Article]:
        raw = self._fetch()
        if raw is None:
            return []

        parsed = feedparser.parse(raw)
        # bozo=1 でも entries が取れる場合があるため、警告に留めて解析は続行する。
        if getattr(parsed, "bozo", 0):
            logger.warning(
                "フィードの XML 解析で問題を検出しました（処理は継続します） feed=%s: %s",
                self._name,
                getattr(parsed, "bozo_exception", "unknown"),
            )

        articles: list[Article] = []
        for entry in getattr(parsed, "entries", []):
            article = self._to_article(entry)
            if article is not None:
                articles.append(article)

        logger.info("RSS 取得完了 feed=%s articles=%d", self._name, len(articles))
        return articles

    def _fetch(self) -> Optional[bytes]:
        """フィード本文を取得する。失敗時は None を返し、呼び出し側は処理を継続する。"""
        try:
            response = requests.get(
                self._url,
                timeout=self._timeout_seconds,
                headers={"User-Agent": USER_AGENT},
            )
            response.raise_for_status()
            return response.content
        except requests.RequestException as exc:
            logger.error("RSS 取得に失敗しました feed=%s url=%s: %s", self._name, self._url, exc)
            return None

    def _to_article(self, entry: Any) -> Optional[Article]:
        """1 エントリを Article に変換する。失敗時は None（他エントリの処理は継続）。"""
        try:
            title = (entry.get("title") or "").strip()
            url = (entry.get("link") or "").strip()
            if not title or not url:
                logger.warning("title/link が無いエントリをスキップします feed=%s", self._name)
                return None

            return Article(
                title=title,
                url=url,
                source=self._name,
                published_at=_parse_published_at(entry),
                summary=clean_summary(entry.get("summary") or "", title),
            )
        except Exception:  # noqa: BLE001 - 1 エントリの失敗で全体を止めない
            logger.exception("エントリの解析に失敗しました feed=%s", self._name)
            return None


def clean_summary(raw: str, title: str) -> str:
    """フィードの要約を、そのまま人に見せられる短文に整える。

    フィードによって中身の質がまったく違う。

    - Google ニュース: タイトルと媒体名を繰り返す `<a>` タグだけで、要約としての中身が無い
    - PR TIMES       : 先頭に `[会社名]` や `[画像1: URL]` といった定型が入る
    - GAME Watch / ITmedia: そのまま使える本文

    そこで「タグを落とす → 定型を落とす → タイトルの焼き直しなら捨てる」の順に処理し、
    中身の無いものは空文字にして、通知側で「要約なし」として扱えるようにする。
    """
    if not raw:
        return ""

    text = re.sub(r"(?s)<[^>]*>", " ", raw)  # HTML タグ
    text = html.unescape(text)
    text = re.sub(r"\[[^\]]*\]", " ", text)  # [会社名] / [画像1: ...] などの定型
    text = re.sub(r"https?://\S+", " ", text)  # 裸の URL
    text = re.sub(r"\s+", " ", text).strip()

    if not text or _is_restatement_of_title(text, title):
        return ""
    return text[:MAX_SUMMARY_LENGTH].strip()


def _is_restatement_of_title(text: str, title: str) -> bool:
    """要約がタイトルの焼き直しに過ぎないか。

    記号や空白の違いを無視して比較する。タイトルを含んでいても、それ以上の
    情報がほとんど無いもの（Google ニュースの「タイトル + 媒体名」）は焼き直し扱い。
    """
    normalized_text = _normalize_for_comparison(text)
    normalized_title = _normalize_for_comparison(title)
    if not normalized_text or not normalized_title:
        return False
    if normalized_text in normalized_title:
        return True
    return (
        normalized_title in normalized_text
        and len(normalized_text) <= len(normalized_title) * 1.3
    )


def _normalize_for_comparison(value: str) -> str:
    return re.sub(r"\W+", "", value, flags=re.UNICODE).lower()


def _parse_published_at(entry: Any) -> Optional[datetime]:
    """published/updated の time.struct_time を UTC の datetime に変換する。"""
    for key in ("published_parsed", "updated_parsed"):
        parsed_time = entry.get(key)
        if not parsed_time:
            continue
        try:
            return datetime(*parsed_time[:6], tzinfo=timezone.utc)
        except (TypeError, ValueError) as exc:
            logger.warning("日時の解析に失敗しました key=%s: %s", key, exc)
    return None
