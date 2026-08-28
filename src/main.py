"""アプリケーション全体の処理フロー。

    情報取得 → キーワード判定 → 重複排除 → SQLite 保存 → Discord 通知

外部サービスに依存する具体的な処理は collectors / notifiers 側に置き、
ここではフローの組み立てとエラー時の継続方針だけを記述する。

通知は「保存したその場で送る」のではなく、notifications テーブルに
「未通知（pending）」として積んでから、収集がすべて終わった後にまとめて送る。
送信結果は sent / failed として書き戻すので、失敗したものは次回実行で再送される
（再送回数の上限は settings.max_retry_count）。
"""

from __future__ import annotations

import argparse
import logging
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional, Sequence

from src.collectors.base import Collector
from src.collectors.rss import RSSCollector
from src.config import AppConfig, ConfigError, load_config
from src.filters.base import RelevanceFilter
from src.filters.feed_requirement import FeedRequirementFilter
from src.filters.keyword import KeywordFilter
from src.logging_config import setup_logging
from src.models import Article, Event, NotificationState
from src.notifiers.base import Notifier
from src.notifiers.discord import DiscordNotifier, DiscordNotifierError
from src.repositories.base import EventRepository, NotificationRepository
from src.repositories.sqlite import SQLiteEventRepository, SQLiteNotificationRepository

logger = logging.getLogger(__name__)

DEFAULT_CONFIG_PATH = "config/targets.yaml"

EXIT_OK = 0
EXIT_FATAL = 2


@dataclass
class RunSummary:
    """1 回の実行結果。ログ出力とテストのために使う。"""

    collected: int = 0
    matched: int = 0
    saved: int = 0
    notified: int = 0
    notify_failed: int = 0
    # 通知したもののうち、過去の実行で失敗して再送になった件数
    retried: int = 0

    def as_log_message(self) -> str:
        return (
            f"実行結果: 取得={self.collected} 一致={self.matched} "
            f"新規保存={self.saved} 通知成功={self.notified} "
            f"通知失敗={self.notify_failed} うち再送={self.retried}"
        )


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = _parse_args(argv)
    setup_logging(args.log_level)

    # --- ここから先の致命的エラーは終了コード 2 で終了する ---
    try:
        config = load_config(args.config)
    except ConfigError as exc:
        logger.error("設定の読み込みに失敗しました: %s", exc)
        return EXIT_FATAL

    db_path = args.db or config.settings.db_path
    repository = SQLiteEventRepository(db_path)
    notification_repository = SQLiteNotificationRepository(db_path)
    try:
        repository.initialize()
        notification_repository.initialize()
    except Exception:
        logger.exception("データベースの初期化に失敗しました: %s", db_path)
        return EXIT_FATAL

    notifier: Optional[Notifier] = None
    if args.dry_run:
        logger.warning("--dry-run 指定のため Discord 通知は行いません")
    else:
        try:
            notifier = DiscordNotifier.from_env(
                timeout_seconds=config.settings.request_timeout_seconds
            )
        except DiscordNotifierError as exc:
            logger.error("Discord Notifier を初期化できません: %s", exc)
            return EXIT_FATAL

    collectors = build_collectors(config)
    relevance_filter = build_filter(config)

    summary = run(
        collectors=collectors,
        relevance_filter=relevance_filter,
        repository=repository,
        notification_repository=notification_repository,
        notifier=notifier,
        max_notifications=config.settings.max_notifications_per_run,
        max_retry_count=config.settings.max_retry_count,
    )
    logger.info(summary.as_log_message())
    return EXIT_OK


def build_collectors(config: AppConfig) -> list[Collector]:
    """設定から Collector を組み立てる（今は RSS のみ）。"""
    return [
        RSSCollector(
            name=feed.name,
            url=feed.url,
            timeout_seconds=config.settings.request_timeout_seconds,
        )
        for feed in config.feeds
    ]


def build_filter(config: AppConfig) -> RelevanceFilter:
    """設定から判定フィルタを組み立てる。

    キーワード判定（OR）を、フィードごとの必須キーワード（AND）で包む。
    require_any を持つフィードが 1 つも無ければ、包む意味が無いので素通しにする。
    """
    keyword_filter = KeywordFilter(config.targets)
    requirements = {
        feed.name: feed.require_any for feed in config.feeds if feed.require_any
    }
    if not requirements:
        return keyword_filter
    return FeedRequirementFilter(keyword_filter, requirements)


def run(
    collectors: Sequence[Collector],
    relevance_filter: RelevanceFilter,
    repository: EventRepository,
    notification_repository: NotificationRepository,
    notifier: Optional[Notifier],
    max_notifications: int,
    max_retry_count: int = 3,
) -> RunSummary:
    """収集から通知までの 1 サイクル。

    個々のフィード・記事・通知の失敗ではループを止めず、集計だけ記録する。
    """
    summary = RunSummary()
    detected_at = datetime.now(timezone.utc)

    for collector in collectors:
        articles = _collect_safely(collector)
        summary.collected += len(articles)

        for article in articles:
            event = _to_event_if_matched(article, relevance_filter, detected_at)
            if event is None:
                continue
            summary.matched += 1

            if not _save_safely(repository, event):
                continue
            summary.saved += 1
            # この時点では未通知。実際の送信は収集がすべて終わってから行う
            _mark_pending_safely(notification_repository, event.content_hash)

    if notifier is None:
        return summary

    targets = _dispatch_targets(
        repository, notification_repository, max_retry_count, max_notifications
    )
    if targets:
        summary.retried = sum(1 for state, _ in targets if state.retry_count > 0)
        _notify_safely(notifier, notification_repository, targets, summary)

    _warn_if_retries_exhausted(notification_repository, max_retry_count)
    return summary


def _dispatch_targets(
    repository: EventRepository,
    notification_repository: NotificationRepository,
    max_retry_count: int,
    limit: int,
) -> list[tuple[NotificationState, Event]]:
    """今回送るべき通知状態を、対応する Event と組にして返す。

    今回保存したものだけでなく、過去の実行で送れなかったものもここに含まれる。
    並び順は記事の公開日時の古い順（チャットで時系列に読めるようにするため）。
    """
    try:
        states = notification_repository.list_dispatchable(max_retry_count, limit)
    except Exception:
        logger.exception("通知対象の取得に失敗しました")
        return []

    targets: list[tuple[NotificationState, Event]] = []
    for state in states:
        try:
            event = repository.find_by_hash(state.content_hash)
        except Exception:
            logger.exception("イベントの取得に失敗しました hash=%s", state.content_hash)
            continue

        if event is None:
            # events 側だけを作り直した場合など。送りようがないので再送対象から外す
            logger.warning(
                "通知状態に対応するイベントがありません。再送対象から外します hash=%s",
                state.content_hash,
            )
            _mark_sent_safely(notification_repository, state.content_hash)
            continue

        targets.append((state, event))

    return sorted(targets, key=lambda target: _published_sort_key(target[1]))


def _published_sort_key(event: Event) -> tuple[int, datetime]:
    """公開日時の古い順に並べるためのキー。

    チャットは新しい投稿が下に積まれるため、古い順に送ると時系列どおりに読める。
    公開日時が取れなかったものは末尾に回す。Collector によっては tz なしの
    日時が来るかもしれないので、その場合は UTC とみなして比較する。
    """
    published = event.published_at
    if published is None:
        return (1, datetime.min.replace(tzinfo=timezone.utc))
    if published.tzinfo is None:
        published = published.replace(tzinfo=timezone.utc)
    return (0, published)


def _notify_safely(
    notifier: Notifier,
    notification_repository: NotificationRepository,
    targets: list[tuple[NotificationState, Event]],
    summary: RunSummary,
) -> None:
    """まとめて通知し、成否を集計して notifications テーブルに書き戻す。"""
    events = [event for _, event in targets]
    logger.info("通知を送信します: %d 件（うち再送 %d 件）", len(events), summary.retried)

    try:
        results = list(notifier.notify_batch(events))
    except Exception:
        logger.exception("通知処理が予期せず失敗しました count=%d", len(events))
        summary.notify_failed += len(events)
        for _, event in targets:
            _mark_failed_safely(
                notification_repository, event.content_hash, "通知処理が予期せず失敗しました"
            )
        return

    for index, (_, event) in enumerate(targets):
        # notify_batch が events と同じ件数を返さなかった分は失敗として扱う
        succeeded = results[index] if index < len(results) else False
        if succeeded:
            summary.notified += 1
            _mark_sent_safely(notification_repository, event.content_hash)
        else:
            summary.notify_failed += 1
            _mark_failed_safely(
                notification_repository, event.content_hash, "通知の送信に失敗しました"
            )


def _mark_pending_safely(
    notification_repository: NotificationRepository, content_hash: str
) -> None:
    """通知状態の記録に失敗しても、収集・保存は続行する。"""
    try:
        notification_repository.create_pending(content_hash)
    except Exception:
        logger.exception("通知状態の登録に失敗しました hash=%s", content_hash)


def _mark_sent_safely(
    notification_repository: NotificationRepository, content_hash: str
) -> None:
    try:
        notification_repository.mark_sent(content_hash)
    except Exception:
        logger.exception("通知成功の記録に失敗しました hash=%s", content_hash)


def _mark_failed_safely(
    notification_repository: NotificationRepository, content_hash: str, error: str
) -> None:
    try:
        notification_repository.mark_failed(content_hash, error)
    except Exception:
        logger.exception("通知失敗の記録に失敗しました hash=%s", content_hash)


def _warn_if_retries_exhausted(
    notification_repository: NotificationRepository, max_retry_count: int
) -> None:
    """再送上限に達して諦めた件数がある場合だけ警告する。"""
    try:
        exhausted = notification_repository.count_exhausted(max_retry_count)
    except Exception:
        logger.exception("再送上限に達した件数の取得に失敗しました")
        return

    if exhausted:
        logger.warning(
            "再送上限 (%d 回) に達して通知を諦めたイベントが %d 件あります",
            max_retry_count,
            exhausted,
        )


def _collect_safely(collector: Collector) -> list[Article]:
    """Collector 側で拾いきれなかった例外もここで受け止め、他のフィードへ進む。"""
    try:
        return collector.collect()
    except Exception:
        logger.exception("収集処理が予期せず失敗しました source=%s", collector.name)
        return []


def _to_event_if_matched(
    article: Article,
    relevance_filter: RelevanceFilter,
    detected_at: datetime,
) -> Optional[Event]:
    try:
        matched = relevance_filter.match(article)
    except Exception:
        logger.exception("対象判定に失敗しました url=%s", article.url)
        return None

    if not matched:
        return None
    return Event.from_article(article, matched, detected_at=detected_at)


def _save_safely(repository: EventRepository, event: Event) -> bool:
    """新規保存できたら True。既知 / 保存失敗なら False（通知対象にしない）。"""
    try:
        if repository.exists(event.url, event.content_hash):
            logger.debug("保存済みのためスキップします: %s", event.url)
            return False
        return repository.save(event)
    except Exception:
        logger.exception("保存に失敗しました url=%s", event.url)
        return False


def _parse_args(argv: Optional[Sequence[str]]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="RSS を監視して Discord に通知する")
    parser.add_argument(
        "--config", default=DEFAULT_CONFIG_PATH, help="設定 YAML のパス"
    )
    parser.add_argument("--db", default=None, help="SQLite ファイルのパス（設定より優先）")
    parser.add_argument(
        "--dry-run", action="store_true", help="Discord へ通知せず、収集と保存だけ行う"
    )
    parser.add_argument("--log-level", default="INFO", help="DEBUG / INFO / WARNING / ERROR")
    return parser.parse_args(argv)


if __name__ == "__main__":
    sys.exit(main())
