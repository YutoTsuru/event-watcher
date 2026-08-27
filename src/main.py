"""アプリケーション全体の処理フロー。

    情報取得 → キーワード判定 → 重複排除 → SQLite 保存 → Discord 通知

外部サービスに依存する具体的な処理は collectors / notifiers 側に置き、
ここではフローの組み立てとエラー時の継続方針だけを記述する。
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
from src.filters.keyword import KeywordFilter
from src.logging_config import setup_logging
from src.models import Article, Event
from src.notifiers.base import Notifier
from src.notifiers.discord import DiscordNotifier, DiscordNotifierError
from src.repositories.sqlite import SQLiteEventRepository

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

    def as_log_message(self) -> str:
        return (
            f"実行結果: 取得={self.collected} 一致={self.matched} "
            f"新規保存={self.saved} 通知成功={self.notified} 通知失敗={self.notify_failed}"
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
    try:
        repository.initialize()
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
    relevance_filter = KeywordFilter(config.targets)

    summary = run(
        collectors=collectors,
        relevance_filter=relevance_filter,
        repository=repository,
        notifier=notifier,
        max_notifications=config.settings.max_notifications_per_run,
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


def run(
    collectors: Sequence[Collector],
    relevance_filter: RelevanceFilter,
    repository: SQLiteEventRepository,
    notifier: Optional[Notifier],
    max_notifications: int,
) -> RunSummary:
    """収集から通知までの 1 サイクル。

    個々のフィード・記事・通知の失敗ではループを止めず、集計だけ記録する。
    """
    summary = RunSummary()
    detected_at = datetime.now(timezone.utc)
    # 通知はここでは行わず、収集をすべて終えてからまとめて送る
    # （1 件ずつ送るとレート制限に掛かりやすいため）
    pending: list[Event] = []
    limit_reported = False

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

            if notifier is None:
                continue
            if len(pending) >= max_notifications:
                if not limit_reported:
                    logger.warning(
                        "1 回あたりの通知上限 (%d) に達したため、以降は保存のみ行います",
                        max_notifications,
                    )
                    limit_reported = True
                continue
            pending.append(event)

    if notifier is not None and pending:
        _notify_safely(notifier, _oldest_first(pending), summary)

    return summary


def _oldest_first(events: list[Event]) -> list[Event]:
    """公開日時の古い順に並べ替える。

    チャットは新しい投稿が下に積まれるため、古い順に送ると時系列どおりに読める。
    公開日時が取れなかったものは末尾に回す。Collector によっては tz なしの
    日時が来るかもしれないので、その場合は UTC とみなして比較する。
    """

    def sort_key(event: Event) -> tuple[int, datetime]:
        published = event.published_at
        if published is None:
            return (1, datetime.min.replace(tzinfo=timezone.utc))
        if published.tzinfo is None:
            published = published.replace(tzinfo=timezone.utc)
        return (0, published)

    return sorted(events, key=sort_key)


def _notify_safely(notifier: Notifier, events: list[Event], summary: RunSummary) -> None:
    """まとめて通知し、成否を集計する。通知の失敗でアプリを止めない。"""
    logger.info("通知を送信します: %d 件", len(events))
    try:
        results = list(notifier.notify_batch(events))
    except Exception:
        logger.exception("通知処理が予期せず失敗しました count=%d", len(events))
        summary.notify_failed += len(events)
        return

    succeeded = sum(1 for result in results if result)
    # notify_batch が events と同じ件数を返さなかった分は失敗として扱う
    summary.notified += succeeded
    summary.notify_failed += len(events) - succeeded


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


def _save_safely(repository: SQLiteEventRepository, event: Event) -> bool:
    """新規保存できたら True。既知 / 保存失敗なら False（通知しない）。"""
    try:
        if repository.exists(event.url, event.content_hash):
            logger.debug("通知済みのためスキップします: %s", event.url)
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
