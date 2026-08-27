"""アプリ全体で共有するドメインモデル。

Collector / Filter / Repository / Notifier の各層は、このモジュールの
データクラスだけを介してやり取りする（層同士が直接依存しないようにする）。
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Optional


@dataclass(frozen=True)
class Article:
    """Collector が取得した「生の記事」。まだ対象判定はされていない。"""

    title: str
    url: str
    source: str
    published_at: Optional[datetime] = None
    summary: str = ""

    @property
    def searchable_text(self) -> str:
        """キーワード判定に使うテキスト（タイトル＋要約）。"""
        return f"{self.title}\n{self.summary}"


@dataclass
class Event:
    """通知・保存の対象になると判定された記事。"""

    title: str
    url: str
    source: str
    published_at: Optional[datetime]
    detected_at: datetime
    content_hash: str
    matched_targets: list[str] = field(default_factory=list)

    @classmethod
    def from_article(
        cls,
        article: Article,
        matched_targets: list[str],
        detected_at: Optional[datetime] = None,
    ) -> "Event":
        return cls(
            title=article.title,
            url=article.url,
            source=article.source,
            published_at=article.published_at,
            detected_at=detected_at or datetime.now(timezone.utc),
            content_hash=make_content_hash(article.title, article.url),
            matched_targets=list(matched_targets),
        )


class NotificationStatus(str, Enum):
    """通知状態。

    pending: 保存済みだが未通知
    sent   : 通知成功（終了状態）
    failed : 通知失敗（retry_count が上限未満なら次回実行で再送される）
    """

    PENDING = "pending"
    SENT = "sent"
    FAILED = "failed"


@dataclass
class NotificationState:
    """イベント 1 件の通知状態。

    Event（何が起きたか）と NotificationState（それを通知できたか）は
    別テーブル・別 Repository で管理する。両者は content_hash で紐づく。
    """

    content_hash: str
    status: NotificationStatus
    retry_count: int = 0
    last_error: Optional[str] = None
    updated_at: Optional[datetime] = None

    def is_retryable(self, max_retry_count: int) -> bool:
        """再送対象かどうか（上限に達した failed は対象外）。"""
        if self.status is NotificationStatus.SENT:
            return False
        if self.status is NotificationStatus.PENDING:
            return True
        return self.retry_count < max_retry_count


def make_content_hash(title: str, url: str) -> str:
    """重複判定用のハッシュ。

    URL が微妙に変わる（トラッキングパラメータ付与など）ケースに備え、
    タイトルと URL の組み合わせから安定した ID を作る。
    正規化は「前後の空白除去」「小文字化」のみに留める（過剰な正規化はしない）。
    """
    normalized = f"{title.strip().lower()}|{url.strip().lower()}"
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()
