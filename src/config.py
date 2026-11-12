"""YAML 設定の読み込み。

設定ファイルが無い / 壊れている場合は「アプリを開始できない致命的エラー」として
ConfigError を送出する（呼び出し側で終了させる）。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)

DEFAULT_REQUEST_TIMEOUT_SECONDS = 10.0
DEFAULT_MAX_NOTIFICATIONS_PER_RUN = 20
DEFAULT_MAX_RETRY_COUNT = 3
# 公開からこの日数を過ぎた記事は保存のみで通知しない（0 以下なら無制限）
DEFAULT_MAX_ARTICLE_AGE_DAYS = 3
DEFAULT_DB_PATH = "data/events.db"
# 通知先 Discord Webhook URL を読む環境変数（話題ごとにチャンネルを分けるときに変える）
DEFAULT_WEBHOOK_ENV = "DISCORD_WEBHOOK_URL"
# 通知した記事からこの日数の間、同じ出来事（同じコラボ）の記事は通知しない
DEFAULT_EVENT_WINDOW_DAYS = 7


class ConfigError(Exception):
    """設定ファイルが存在しない、または構造が不正。"""


@dataclass(frozen=True)
class Target:
    """監視対象（作品・人物・ブランド等）とその判定キーワード。"""

    name: str
    keywords: list[str]


@dataclass(frozen=True)
class FeedConfig:
    """収集元 RSS フィード。"""

    name: str
    url: str
    # このフィードから拾う記事に必須のキーワード（1 つでも含めば通す）。
    # 一般ニュースのフィードから「コラボ関連だけ」を拾うために使う。空なら絞り込まない。
    require_any: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class Settings:
    """実行時の挙動設定。"""

    request_timeout_seconds: float = DEFAULT_REQUEST_TIMEOUT_SECONDS
    max_notifications_per_run: int = DEFAULT_MAX_NOTIFICATIONS_PER_RUN
    # 通知失敗イベントを再送する最大回数（これを超えたら再送しない）
    max_retry_count: int = DEFAULT_MAX_RETRY_COUNT
    # 公開からこの日数を過ぎた記事は、保存（＝以後の重複判定）だけして通知しない。
    # 0 以下なら日数で絞らない。
    max_article_age_days: float = DEFAULT_MAX_ARTICLE_AGE_DAYS
    # DB が空のとき（新しい話題の初回・キャッシュ消失後）は記録だけして通知しない
    seed_on_first_run: bool = True
    db_path: str = DEFAULT_DB_PATH
    # 通知先 Discord Webhook URL を読む環境変数名。URL そのものは設定ファイルに書かない
    webhook_env: str = DEFAULT_WEBHOOK_ENV


@dataclass(frozen=True)
class SourcePriorityConfig:
    """同じ記事が複数の媒体から届いたとき、どの媒体の版を残すか。"""

    # 上から順に優先して残す媒体
    preferred: list[str] = field(default_factory=list)
    # 他に版があればそちらを残したい媒体（転載サイトなど）
    deprioritized: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class EventGroupingConfig:
    """同じ出来事（同じコラボ）を報じた別媒体の記事をまとめる設定。"""

    # 通知した記事からこの日数の間、同じ出来事の記事は通知しない（0 以下で無効）
    window_days: float = DEFAULT_EVENT_WINDOW_DAYS
    # 特徴語から除く、話題そのものを表す語（キャラクター名など）。
    # targets のキーワードは自動で含まれるので書かなくてよい
    stopwords: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class AppConfig:
    feeds: list[FeedConfig]
    targets: list[Target]
    settings: Settings = field(default_factory=Settings)
    source_priority: SourcePriorityConfig = field(default_factory=SourcePriorityConfig)
    event_grouping: EventGroupingConfig = field(default_factory=EventGroupingConfig)


def load_config(path: str | Path) -> AppConfig:
    """YAML を読み込んで AppConfig を返す。"""
    config_path = Path(path)
    if not config_path.exists():
        raise ConfigError(f"設定ファイルが見つかりません: {config_path}")

    try:
        raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ConfigError(f"設定ファイルの YAML 解析に失敗しました: {config_path}") from exc

    if not isinstance(raw, dict):
        raise ConfigError(f"設定ファイルのトップレベルはマッピングである必要があります: {config_path}")

    config = AppConfig(
        feeds=_parse_feeds(raw.get("feeds")),
        targets=_parse_targets(raw.get("targets")),
        settings=_parse_settings(raw.get("settings")),
        source_priority=_parse_source_priority(raw.get("source_priority")),
        event_grouping=_parse_event_grouping(raw.get("event_grouping")),
    )
    logger.info(
        "設定を読み込みました: feeds=%d, targets=%d", len(config.feeds), len(config.targets)
    )
    return config


def _parse_feeds(raw_feeds: Any) -> list[FeedConfig]:
    if not isinstance(raw_feeds, list) or not raw_feeds:
        raise ConfigError("'feeds' に 1 件以上の RSS フィードを指定してください")

    feeds: list[FeedConfig] = []
    for index, item in enumerate(raw_feeds):
        if not isinstance(item, dict):
            raise ConfigError(f"feeds[{index}] はマッピングである必要があります")
        url = item.get("url")
        if not url:
            raise ConfigError(f"feeds[{index}] に 'url' がありません")

        raw_require = item.get("require_any") or []
        if not isinstance(raw_require, list):
            raise ConfigError(f"feeds[{index}] の 'require_any' はリストである必要があります")

        feeds.append(
            FeedConfig(
                name=str(item.get("name") or url),
                url=str(url),
                require_any=[str(keyword) for keyword in raw_require],
            )
        )
    return feeds


def _parse_targets(raw_targets: Any) -> list[Target]:
    if not isinstance(raw_targets, list) or not raw_targets:
        raise ConfigError("'targets' に 1 件以上の監視対象を指定してください")

    targets: list[Target] = []
    for index, item in enumerate(raw_targets):
        if not isinstance(item, dict):
            raise ConfigError(f"targets[{index}] はマッピングである必要があります")
        name = item.get("name")
        keywords = item.get("keywords")
        if not name:
            raise ConfigError(f"targets[{index}] に 'name' がありません")
        if not isinstance(keywords, list) or not keywords:
            raise ConfigError(f"targets[{index}] ({name}) に 'keywords' がありません")
        targets.append(Target(name=str(name), keywords=[str(k) for k in keywords]))
    return targets


def _parse_source_priority(raw: Any) -> SourcePriorityConfig:
    if raw is None:
        return SourcePriorityConfig()
    if not isinstance(raw, dict):
        raise ConfigError("'source_priority' はマッピングである必要があります")

    lists: dict[str, list[str]] = {}
    for key in ("preferred", "deprioritized"):
        value = raw.get(key) or []
        if not isinstance(value, list):
            raise ConfigError(f"'source_priority.{key}' はリストである必要があります")
        lists[key] = [str(name) for name in value]
    return SourcePriorityConfig(**lists)


def _parse_event_grouping(raw: Any) -> EventGroupingConfig:
    if raw is None:
        return EventGroupingConfig()
    if not isinstance(raw, dict):
        raise ConfigError("'event_grouping' はマッピングである必要があります")

    stopwords = raw.get("stopwords") or []
    if not isinstance(stopwords, list):
        raise ConfigError("'event_grouping.stopwords' はリストである必要があります")
    try:
        window_days = float(raw.get("window_days", DEFAULT_EVENT_WINDOW_DAYS))
    except (TypeError, ValueError) as exc:
        raise ConfigError(f"'event_grouping.window_days' の値が不正です: {exc}") from exc
    return EventGroupingConfig(
        window_days=window_days, stopwords=[str(word) for word in stopwords]
    )


def _parse_bool(value: Any) -> bool:
    """YAML の真偽値。文字列の "false" などを True と誤読しないよう厳密に扱う。"""
    if isinstance(value, bool):
        return value
    raise ValueError(f"真偽値（true / false）である必要があります: {value!r}")


def _parse_settings(raw_settings: Any) -> Settings:
    if raw_settings is None:
        return Settings()
    if not isinstance(raw_settings, dict):
        raise ConfigError("'settings' はマッピングである必要があります")

    try:
        return Settings(
            request_timeout_seconds=float(
                raw_settings.get("request_timeout_seconds", DEFAULT_REQUEST_TIMEOUT_SECONDS)
            ),
            max_notifications_per_run=int(
                raw_settings.get("max_notifications_per_run", DEFAULT_MAX_NOTIFICATIONS_PER_RUN)
            ),
            max_retry_count=int(raw_settings.get("max_retry_count", DEFAULT_MAX_RETRY_COUNT)),
            max_article_age_days=float(
                raw_settings.get("max_article_age_days", DEFAULT_MAX_ARTICLE_AGE_DAYS)
            ),
            seed_on_first_run=_parse_bool(raw_settings.get("seed_on_first_run", True)),
            db_path=str(raw_settings.get("db_path", DEFAULT_DB_PATH)),
            webhook_env=str(raw_settings.get("webhook_env", DEFAULT_WEBHOOK_ENV)),
        )
    except (TypeError, ValueError) as exc:
        raise ConfigError(f"'settings' の値が不正です: {exc}") from exc
