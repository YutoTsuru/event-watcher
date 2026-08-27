"""SQLite への永続化。

Repository の責務は保存と取得だけ。通知や対象判定のロジックは持たない。

- events        : イベント本体（何が起きたか）        -> SQLiteEventRepository
- notifications : 通知状態（通知できたか）            -> SQLiteNotificationRepository

2 つのテーブルは content_hash で紐づく。責務を分けているので、
片方だけを Supabase 実装に差し替えることもできる。
"""

from __future__ import annotations

import json
import logging
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator, Optional

from src.models import Event, NotificationState, NotificationStatus
from src.repositories.base import EventRepository, NotificationRepository

logger = logging.getLogger(__name__)

SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    title           TEXT NOT NULL,
    url             TEXT NOT NULL UNIQUE,
    source          TEXT NOT NULL,
    published_at    TEXT,
    detected_at     TEXT NOT NULL,
    content_hash    TEXT NOT NULL UNIQUE,
    matched_targets TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_events_content_hash ON events (content_hash);
CREATE INDEX IF NOT EXISTS idx_events_detected_at ON events (detected_at);
"""

NOTIFICATION_SCHEMA = """
CREATE TABLE IF NOT EXISTS notifications (
    content_hash TEXT PRIMARY KEY,
    status       TEXT NOT NULL,
    retry_count  INTEGER NOT NULL DEFAULT 0,
    last_error   TEXT,
    created_at   TEXT NOT NULL,
    updated_at   TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_notifications_status ON notifications (status, retry_count);
"""


class _SQLiteRepositoryBase:
    """接続まわりの共通処理。テーブルごとの Repository がこれを継承する。"""

    def __init__(self, db_path: str | Path) -> None:
        self._db_path = Path(db_path)
        parent = self._db_path.parent
        if str(parent):
            parent.mkdir(parents=True, exist_ok=True)

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self._db_path, timeout=10.0)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()


class SQLiteEventRepository(_SQLiteRepositoryBase, EventRepository):
    """events テーブルへのアクセス。"""

    def initialize(self) -> None:
        """テーブル・インデックスを作成する（既に存在すれば何もしない）。"""
        with self._connect() as conn:
            conn.executescript(SCHEMA)
        logger.info("SQLite を初期化しました: %s", self._db_path)

    def exists(self, url: str, content_hash: str) -> bool:
        """URL または content_hash が既に保存済みかどうか。"""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT 1 FROM events WHERE url = ? OR content_hash = ? LIMIT 1",
                (url, content_hash),
            ).fetchone()
        return row is not None

    def save(self, event: Event) -> bool:
        """新規なら保存して True、重複なら何もせず False を返す。"""
        with self._connect() as conn:
            cursor = conn.execute(
                """
                INSERT OR IGNORE INTO events
                    (title, url, source, published_at, detected_at, content_hash, matched_targets)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event.title,
                    event.url,
                    event.source,
                    _to_iso(event.published_at),
                    _to_iso(event.detected_at),
                    event.content_hash,
                    json.dumps(event.matched_targets, ensure_ascii=False),
                ),
            )
            inserted = cursor.rowcount > 0
        if not inserted:
            logger.debug("重複のため保存しませんでした: %s", event.url)
        return inserted

    def find_by_hash(self, content_hash: str) -> Optional[Event]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM events WHERE content_hash = ? LIMIT 1", (content_hash,)
            ).fetchone()
        return _to_event(row) if row else None

    def list_recent(self, limit: int = 20) -> list[Event]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM events ORDER BY detected_at DESC, id DESC LIMIT ?", (limit,)
            ).fetchall()
        return [_to_event(row) for row in rows]

    def count(self) -> int:
        with self._connect() as conn:
            row = conn.execute("SELECT COUNT(*) AS c FROM events").fetchone()
        return int(row["c"])


class SQLiteNotificationRepository(_SQLiteRepositoryBase, NotificationRepository):
    """notifications テーブルへのアクセス。

    「上限に達したか」の判断そのものは持たず、上限値を引数で受け取って
    条件に合う行を返すだけにしている（ポリシーは呼び出し側 = main.py）。
    """

    def initialize(self) -> None:
        with self._connect() as conn:
            conn.executescript(NOTIFICATION_SCHEMA)
        logger.info("notifications テーブルを初期化しました: %s", self._db_path)

    def create_pending(self, content_hash: str) -> bool:
        now = _now_iso()
        with self._connect() as conn:
            cursor = conn.execute(
                """
                INSERT OR IGNORE INTO notifications
                    (content_hash, status, retry_count, last_error, created_at, updated_at)
                VALUES (?, ?, 0, NULL, ?, ?)
                """,
                (content_hash, NotificationStatus.PENDING.value, now, now),
            )
            created = cursor.rowcount > 0
        if not created:
            logger.debug("通知状態は既に存在します: %s", content_hash)
        return created

    def mark_sent(self, content_hash: str) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE notifications
                   SET status = ?, last_error = NULL, updated_at = ?
                 WHERE content_hash = ?
                """,
                (NotificationStatus.SENT.value, _now_iso(), content_hash),
            )

    def mark_failed(self, content_hash: str, error: Optional[str] = None) -> int:
        """失敗として記録し、増加後の retry_count を返す。"""
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE notifications
                   SET status = ?, retry_count = retry_count + 1, last_error = ?, updated_at = ?
                 WHERE content_hash = ?
                """,
                (NotificationStatus.FAILED.value, error, _now_iso(), content_hash),
            )
            row = conn.execute(
                "SELECT retry_count FROM notifications WHERE content_hash = ?",
                (content_hash,),
            ).fetchone()
        return int(row["retry_count"]) if row else 0

    def get(self, content_hash: str) -> Optional[NotificationState]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM notifications WHERE content_hash = ? LIMIT 1", (content_hash,)
            ).fetchone()
        return _to_notification_state(row) if row else None

    def list_dispatchable(self, max_retry_count: int, limit: int) -> list[NotificationState]:
        """pending と、リトライ上限に達していない failed を古い順に返す。"""
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM notifications
                 WHERE status = ?
                    OR (status = ? AND retry_count < ?)
                 ORDER BY retry_count ASC, updated_at ASC
                 LIMIT ?
                """,
                (
                    NotificationStatus.PENDING.value,
                    NotificationStatus.FAILED.value,
                    max_retry_count,
                    limit,
                ),
            ).fetchall()
        return [_to_notification_state(row) for row in rows]

    def count_exhausted(self, max_retry_count: int) -> int:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT COUNT(*) AS c FROM notifications WHERE status = ? AND retry_count >= ?",
                (NotificationStatus.FAILED.value, max_retry_count),
            ).fetchone()
        return int(row["c"])


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _to_notification_state(row: sqlite3.Row) -> NotificationState:
    try:
        status = NotificationStatus(row["status"])
    except ValueError:
        logger.warning(
            "未知の通知状態を pending として扱います: %s", row["status"]
        )
        status = NotificationStatus.PENDING

    return NotificationState(
        content_hash=row["content_hash"],
        status=status,
        retry_count=int(row["retry_count"]),
        last_error=row["last_error"],
        updated_at=_from_iso(row["updated_at"]),
    )


def _to_iso(value: Optional[datetime]) -> Optional[str]:
    return value.isoformat() if value else None


def _from_iso(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        logger.warning("日時のパースに失敗しました: %s", value)
        return None


def _to_event(row: sqlite3.Row) -> Event:
    try:
        matched_targets = json.loads(row["matched_targets"])
    except (json.JSONDecodeError, TypeError):
        logger.warning("matched_targets のパースに失敗しました id=%s", row["id"])
        matched_targets = []

    return Event(
        title=row["title"],
        url=row["url"],
        source=row["source"],
        published_at=_from_iso(row["published_at"]),
        detected_at=_from_iso(row["detected_at"]),
        content_hash=row["content_hash"],
        matched_targets=matched_targets,
    )
