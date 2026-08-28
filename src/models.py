"""アプリ全体で共有するドメインモデル。

Collector / Filter / Repository / Notifier の各層は、このモジュールの
データクラスだけを介してやり取りする（層同士が直接依存しないようにする）。
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Optional

# タイトル末尾に付く配信元の表記（「 - 朝日新聞」「｜ファミ通」など）
_SOURCE_SUFFIX_PATTERN = re.compile(r"\s*[-–—|｜]\s*[^-–—|｜]{2,25}\s*$")
# これより短いタイトルは、たまたま一致しただけの可能性があるので名寄せに使わない
MIN_TITLE_KEY_LENGTH = 10


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
    # 記事の要約。通知本文に出すため保存する（フィードによっては空）
    summary: str = ""
    # 別ソースで同じ記事を拾ったときの名寄せキー（短いタイトルでは空）
    title_key: str = ""

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
            summary=article.summary,
            title_key=make_title_key(article.title),
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


def make_title_key(title: str) -> str:
    """同じ記事を別ソース・別 URL で拾ったときに名寄せするためのキー。

    同じ記事でも、経路によってタイトルの末尾に配信元が付く
    （Google ニュース経由は「タイトル - 媒体名」、直接ソースは「タイトル」）。
    そこで末尾の配信元表記を落とし、記号と空白を無視した形に正規化する。

    短いタイトルは別記事どうしがたまたま一致しうるので、
    正規化後が短い場合は空文字を返して名寄せの対象外にする
    （空文字のキーは重複判定に使わない）。
    """
    without_source = _SOURCE_SUFFIX_PATTERN.sub("", title.strip())
    normalized = re.sub(r"\W+", "", without_source, flags=re.UNICODE).lower()
    if len(normalized) < MIN_TITLE_KEY_LENGTH:
        return ""
    return normalized


def make_content_hash(title: str, url: str) -> str:
    """重複判定用のハッシュ。

    URL が微妙に変わる（トラッキングパラメータ付与など）ケースに備え、
    タイトルと URL の組み合わせから安定した ID を作る。
    正規化は「前後の空白除去」「小文字化」のみに留める（過剰な正規化はしない）。
    """
    normalized = f"{title.strip().lower()}|{url.strip().lower()}"
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()
