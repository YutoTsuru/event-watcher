# -*- coding: utf-8 -*-
"""DiscordNotifier のテスト。

requests.post は必ずモックし、実際のネットワークアクセスは行わない。
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from unittest.mock import MagicMock

import pytest
import requests

from src.models import Event, make_content_hash
from src.notifiers.discord import (
    WEBHOOK_ENV_VAR,
    DiscordNotifier,
    DiscordNotifierError,
    build_payload,
)


def make_event(**overrides: Any) -> Event:
    defaults: dict[str, Any] = dict(
        title="ポケモン新作発表",
        url="https://example.com/pokemon-1",
        source="テストフィード",
        published_at=datetime(2026, 1, 1, 9, 0, tzinfo=timezone.utc),
        detected_at=datetime(2026, 1, 1, 10, 0, tzinfo=timezone.utc),
        matched_targets=["ポケモン"],
    )
    defaults.update(overrides)
    content_hash = make_content_hash(defaults["title"], defaults["url"])
    return Event(content_hash=content_hash, **defaults)


def test_build_payload_shape() -> None:
    """Discord Webhook に送るペイロードの形（embeds/title/url/fields）を検証する。"""
    event = make_event()

    payload = build_payload(event)

    assert "embeds" in payload
    assert len(payload["embeds"]) == 1
    embed = payload["embeds"][0]
    assert embed["title"] == event.title
    assert embed["url"] == event.url
    assert embed["color"] == 0x5865F2
    field_names = [f["name"] for f in embed["fields"]]
    assert "ソース" in field_names
    assert "一致した対象" in field_names


def test_build_payload_without_matched_targets_omits_field() -> None:
    event = make_event(matched_targets=[])

    payload = build_payload(event)

    field_names = [f["name"] for f in payload["embeds"][0]["fields"]]
    assert "一致した対象" not in field_names


def test_notifier_requires_non_empty_webhook_url() -> None:
    with pytest.raises(DiscordNotifierError):
        DiscordNotifier("")


def test_from_env_raises_when_env_var_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(WEBHOOK_ENV_VAR, raising=False)

    with pytest.raises(DiscordNotifierError):
        DiscordNotifier.from_env()


def test_from_env_builds_notifier_when_env_var_set(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(WEBHOOK_ENV_VAR, "https://discord.example.com/webhook/abc")

    notifier = DiscordNotifier.from_env()

    assert isinstance(notifier, DiscordNotifier)


def test_notify_success_posts_expected_payload_and_url(monkeypatch: pytest.MonkeyPatch) -> None:
    """成功時: requests.post が正しい URL・payload で呼ばれ、notify は True を返す。"""
    webhook_url = "https://discord.example.com/webhook/xyz"
    notifier = DiscordNotifier(webhook_url, timeout_seconds=5.0)
    event = make_event()

    mock_response = MagicMock()
    mock_response.raise_for_status.return_value = None
    mock_post = MagicMock(return_value=mock_response)
    monkeypatch.setattr("src.notifiers.discord.requests.post", mock_post)

    result = notifier.notify(event)

    assert result is True
    mock_post.assert_called_once()
    call_args, call_kwargs = mock_post.call_args
    assert call_args[0] == webhook_url
    assert call_kwargs["json"] == build_payload(event)
    assert call_kwargs["timeout"] == 5.0


def test_notify_failure_returns_false_and_does_not_raise(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """requests.RequestException が発生しても notify は例外を送出せず False を返す。"""
    notifier = DiscordNotifier("https://discord.example.com/webhook/xyz")
    event = make_event()

    mock_post = MagicMock(side_effect=requests.RequestException("接続エラー"))
    monkeypatch.setattr("src.notifiers.discord.requests.post", mock_post)

    result = notifier.notify(event)

    assert result is False


def test_notify_failure_on_http_error_status_returns_false(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """raise_for_status() が RequestException 系を送出するケース（4xx/5xx）でも False。"""
    notifier = DiscordNotifier("https://discord.example.com/webhook/xyz")
    event = make_event()

    mock_response = MagicMock()
    mock_response.raise_for_status.side_effect = requests.HTTPError("500 Server Error")
    mock_post = MagicMock(return_value=mock_response)
    monkeypatch.setattr("src.notifiers.discord.requests.post", mock_post)

    result = notifier.notify(event)

    assert result is False
