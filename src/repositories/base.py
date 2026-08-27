"""Repository の共通インターフェース。

main.py や Collector / Notifier は、この抽象だけに依存する。
SQLite 固有の処理（sqlite3 の接続や SQL 文）は実装クラスの内側に閉じる。

将来 Supabase (PostgreSQL) へ移行する場合は、この 2 つの ABC を実装した
SupabaseEventRepository / SupabaseNotificationRepository を追加し、
main.py の組み立て部分だけ差し替えればよい。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Optional

from src.models import Event, NotificationState


class EventRepository(ABC):
    """イベント本体（何が起きたか）の保存・取得のみを担当する。"""

    @abstractmethod
    def initialize(self) -> None:
        """テーブルなどの初期化。"""

    @abstractmethod
    def exists(self, url: str, content_hash: str) -> bool:
        """URL または content_hash が既に保存済みかどうか。"""

    @abstractmethod
    def save(self, event: Event) -> bool:
        """新規なら保存して True、重複なら False。"""

    @abstractmethod
    def find_by_hash(self, content_hash: str) -> Optional[Event]:
        """content_hash で 1 件取得する。"""

    @abstractmethod
    def list_recent(self, limit: int = 20) -> list[Event]:
        """検出日時の新しい順に取得する。"""

    @abstractmethod
    def count(self) -> int:
        """保存件数。"""


class NotificationRepository(ABC):
    """通知状態（通知できたか）の保存・取得のみを担当する。

    リトライするか否かの判断（上限との比較）は呼び出し側 / モデルの責務で、
    ここでは条件に合う行を返す・状態を更新することだけを行う。
    """

    @abstractmethod
    def initialize(self) -> None:
        """テーブルなどの初期化。"""

    @abstractmethod
    def create_pending(self, content_hash: str) -> bool:
        """未通知（pending）として登録する。既にあれば False。"""

    @abstractmethod
    def mark_sent(self, content_hash: str) -> None:
        """通知成功として記録する。"""

    @abstractmethod
    def mark_failed(self, content_hash: str, error: Optional[str] = None) -> int:
        """通知失敗として記録し、増加後の retry_count を返す。"""

    @abstractmethod
    def get(self, content_hash: str) -> Optional[NotificationState]:
        """通知状態を 1 件取得する。"""

    @abstractmethod
    def list_dispatchable(self, max_retry_count: int, limit: int) -> list[NotificationState]:
        """通知すべき状態（pending と、上限未満の failed）を古い順に返す。"""

    @abstractmethod
    def count_exhausted(self, max_retry_count: int) -> int:
        """リトライ上限に達して再送されなくなった件数。"""
