# -*- coding: utf-8 -*-
"""同梱の監視設定（話題ごとの YAML）のテスト。

話題ごとに Discord チャンネルを分けるため、Webhook の環境変数と DB は
設定ファイルごとに別でなければならない（混ざると別チャンネルに届いたり、
片方の既読履歴で他方の記事が弾かれたりする）。
"""

from __future__ import annotations

import pytest

from src.config import load_config

WATCH_CONFIGS = ["config/targets.yaml", "config/haikyu.yaml"]


@pytest.mark.parametrize("path", WATCH_CONFIGS)
def test_shipped_configs_load(path: str) -> None:
    config = load_config(path)

    assert config.feeds
    assert config.targets


def test_each_topic_has_its_own_webhook_and_database() -> None:
    configs = [load_config(path) for path in WATCH_CONFIGS]

    webhook_envs = [c.settings.webhook_env for c in configs]
    db_paths = [c.settings.db_path for c in configs]
    assert len(set(webhook_envs)) == len(configs)
    assert len(set(db_paths)) == len(configs)


def test_haikyu_config_targets_haikyu() -> None:
    config = load_config("config/haikyu.yaml")

    assert config.settings.webhook_env == "DISCORD_WEBHOOK_URL_HAIKYU"
    assert [t.name for t in config.targets] == ["ハイキュー"]
