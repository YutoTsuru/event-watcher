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
from datetime import datetime, timedelta, timezone
from typing import Optional, Sequence

from src.collectors.base import Collector
from src.collectors.rss import RSSCollector
from src.config import AppConfig, ConfigError, load_config
from src.filters.base import RelevanceFilter
from src.filters.feed_requirement import FeedRequirementFilter
from src.filters.keyword import KeywordFilter
from src.logging_config import setup_logging
from src.models import Article, Event, NotificationState, is_similar_title_key
from src.notifiers.base import Notifier
from src.notifiers.discord import DiscordNotifier, DiscordNotifierError
from src.repositories.base import EventRepository, NotificationRepository
from src.repositories.sqlite import SQLiteEventRepository, SQLiteNotificationRepository
from src.event_grouping import EventGrouper, EventSignature
from src.source_priority import SourcePriority

logger = logging.getLogger(__name__)

DEFAULT_CONFIG_PATH = "config/targets.yaml"

# 似たタイトルの名寄せで、どこまで過去の保存済み記事と比べるか。
# Google ニュースの検索フィードが when:30d で遡るので、それより少し長めにとる。
SIMILAR_TITLE_LOOKBACK_DAYS = 45

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
    # 似たタイトルの記事が保存済みだったため、転載とみなして捨てた件数
    near_duplicates: int = 0
    # 公開から日数が経っていたため、保存だけして通知しなかった件数
    too_old: int = 0
    # 同じ出来事（同じコラボ）を最近通知済みだったため、保存だけして通知しなかった件数
    same_event: int = 0

    def as_log_message(self) -> str:
        return (
            f"実行結果: 取得={self.collected} 一致={self.matched} "
            f"新規保存={self.saved} 通知成功={self.notified} "
            f"通知失敗={self.notify_failed} うち再送={self.retried} "
            f"類似タイトル除外={self.near_duplicates} 古い記事で通知せず={self.too_old} "
            f"同じ出来事で通知せず={self.same_event}"
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
                timeout_seconds=config.settings.request_timeout_seconds,
                env_var=config.settings.webhook_env,
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
        max_article_age_days=config.settings.max_article_age_days,
        source_priority=SourcePriority(
            preferred=config.source_priority.preferred,
            deprioritized=config.source_priority.deprioritized,
        ),
        event_grouper=build_event_grouper(config),
        event_window_days=config.event_grouping.window_days,
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


def build_event_grouper(config: AppConfig) -> EventGrouper:
    """同じ出来事の判定器。監視対象のキーワードは話題そのものなので特徴語から外す。"""
    keywords = [keyword for target in config.targets for keyword in target.keywords]
    return EventGrouper(stopwords=[*keywords, *config.event_grouping.stopwords])


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
    max_article_age_days: float = 0,
    source_priority: Optional[SourcePriority] = None,
    event_grouper: Optional[EventGrouper] = None,
    event_window_days: float = 0,
) -> RunSummary:
    """収集から通知までの 1 サイクル。

    個々のフィード・記事・通知の失敗ではループを止めず、集計だけ記録する。
    """
    summary = RunSummary()
    detected_at = datetime.now(timezone.utc)
    # 今回保存した分も足していくので、同じ実行内の別フィードどうしも名寄せされる
    known_title_keys = _load_recent_title_keys(repository, detected_at)
    notify_cutoff = (
        detected_at - timedelta(days=max_article_age_days) if max_article_age_days > 0 else None
    )
    # 最近通知した記事の特徴語。これと同じ出来事の記事は通知しない
    grouper = event_grouper if event_window_days > 0 else None
    notified_event_tokens = (
        _load_recent_notified_tokens(
            repository,
            notification_repository,
            grouper,
            detected_at - timedelta(days=event_window_days),
        )
        if grouper is not None
        else []
    )

    candidates: list[Event] = []
    for collector in collectors:
        articles = _collect_safely(collector)
        summary.collected += len(articles)

        for article in articles:
            event = _to_event_if_matched(article, relevance_filter, detected_at)
            if event is None:
                continue
            summary.matched += 1
            candidates.append(event)

    # 同じ記事が複数の媒体から届いたら、良い版から先に保存する。
    # 後から来た版は下の重複判定（完全一致・類似）で弾かれるので、良い版だけが残る
    for event in _best_versions_first(candidates, source_priority or SourcePriority()):
        if _has_similar_title(event, known_title_keys):
            summary.near_duplicates += 1
            logger.debug("似たタイトルの記事が保存済みのためスキップします: %s", event.title)
            continue

        if not _save_safely(repository, event):
            continue
        summary.saved += 1
        if event.title_key:
            known_title_keys.append(event.title_key)

        if _is_too_old(event, notify_cutoff):
            # 保存はしてあるので、以後この記事が再び流れてきても重複として弾かれる
            summary.too_old += 1
            continue

        if grouper is not None:
            tokens = grouper.signature(event.title)
            if any(grouper.is_same_event(tokens, known) for known in notified_event_tokens):
                summary.same_event += 1
                logger.debug("同じ出来事を通知済みのため通知しません: %s", event.title)
                continue
            notified_event_tokens.append(tokens)

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


def _best_versions_first(events: Sequence[Event], priority: SourcePriority) -> list[Event]:
    """同じ記事の版のうち、残したいものが先に来るよう並べる。

    1. 媒体の優先順位（config の source_priority）が高い
    2. 要約を持っている（通知本文に要約を載せられる）
    3. config の feeds で先に書かれたフィード（元の並び。sorted は安定ソート）
    """
    return sorted(events, key=lambda event: (priority.rank_event(event), not event.summary))


def _load_recent_notified_tokens(
    repository: EventRepository,
    notification_repository: NotificationRepository,
    grouper: EventGrouper,
    since: datetime,
) -> list[EventSignature]:
    """since 以降に通知した（通知対象にした）記事の特徴語。

    「保存したが通知しなかった記事」（古い記事・同じ出来事の続報）は含めない。
    含めると、記事が出続ける限り同じ出来事の判定が延々と延長され、
    window_days を過ぎても新しい続報（発売開始など）が通知されなくなるため。
    """
    try:
        notified = notification_repository.list_hashes_created_since(since)
        events = repository.list_since(since)
    except Exception:
        logger.exception("通知済み記事の取得に失敗しました。同じ出来事の判定なしで続行します")
        return []
    return [grouper.signature(event.title) for event in events if event.content_hash in notified]


def _load_recent_title_keys(repository: EventRepository, now: datetime) -> list[str]:
    """似たタイトルの名寄せに使う、最近保存した記事の title_key。

    取得に失敗しても実行は止めない（完全一致の名寄せは exists 側で効く）。
    """
    since = now - timedelta(days=SIMILAR_TITLE_LOOKBACK_DAYS)
    try:
        return list(repository.list_title_keys_since(since))
    except Exception:
        logger.exception("名寄せ用タイトルの取得に失敗しました。類似判定なしで続行します")
        return []


def _has_similar_title(event: Event, known_title_keys: Sequence[str]) -> bool:
    """同じ記事の転載（見出しが少しだけ違う）が保存済みかどうか。

    完全一致は exists 側で判定するので、ここでは「似ているが同一ではない」ものを拾う。
    """
    if not event.title_key:
        return False
    return any(
        key != event.title_key and is_similar_title_key(event.title_key, key)
        for key in known_title_keys
    )


def _is_too_old(event: Event, cutoff: Optional[datetime]) -> bool:
    """公開日時が cutoff より前か。公開日時が分からない記事は古いとみなさない。"""
    if cutoff is None or event.published_at is None:
        return False
    published = event.published_at
    if published.tzinfo is None:
        published = published.replace(tzinfo=timezone.utc)
    return published < cutoff


def _save_safely(repository: EventRepository, event: Event) -> bool:
    """新規保存できたら True。既知 / 保存失敗なら False（通知対象にしない）。"""
    try:
        if repository.exists(event.url, event.content_hash, event.title_key):
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
