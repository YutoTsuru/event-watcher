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
    MAX_DESCRIPTION_LENGTH,
    WEBHOOK_ENV_VAR,
    DiscordNotifier,
    DiscordNotifierError,
    build_embed,
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
    """Discord Webhook に送るペイロードの形（embeds/title/url/description/footer）を検証する。"""
    event = make_event(summary="ピカチュウデザインのカメラが10月6日に発売されます。")

    payload = build_payload(event)

    assert "embeds" in payload
    assert len(payload["embeds"]) == 1
    embed = payload["embeds"][0]
    assert embed["title"] == event.title
    assert embed["url"] == event.url
    assert embed["color"] == 0x5865F2
    assert embed["description"] == event.summary
    assert embed["footer"]["text"].startswith("2026-01-01")


def test_build_embed_omits_source_and_matched_targets() -> None:
    """ソース・一致した対象は毎回見る必要が無いので載せない。"""
    event = make_event(source="テストフィード", matched_targets=["ポケモン"])

    embed = build_embed(event)

    rendered = str(embed)
    assert "ソース" not in rendered
    assert "一致した対象" not in rendered
    assert "fields" not in embed


def test_build_embed_omits_description_when_summary_is_empty() -> None:
    """要約を持たないフィード（Google ニュースなど）では description を付けない。"""
    embed = build_embed(make_event(summary=""))

    assert "description" not in embed


def test_build_embed_shortens_long_summary() -> None:
    """長い要約は通知に載る長さに切り詰め、末尾を … にする。"""
    event = make_event(summary="あ" * 500)

    embed = build_embed(event)

    assert len(embed["description"]) == MAX_DESCRIPTION_LENGTH
    assert embed["description"].endswith("…")


def test_build_embed_without_published_at_omits_footer() -> None:
    embed = build_embed(make_event(published_at=None))

    assert "footer" not in embed


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


def test_from_env_reads_the_given_env_var(monkeypatch: pytest.MonkeyPatch) -> None:
    """話題ごとにチャンネルを分けるため、読む環境変数を差し替えられる。"""
    monkeypatch.delenv(WEBHOOK_ENV_VAR, raising=False)
    monkeypatch.setenv("DISCORD_WEBHOOK_URL_OTHER", "https://discord.example.com/webhook/other")

    notifier = DiscordNotifier.from_env(env_var="DISCORD_WEBHOOK_URL_OTHER")

    assert isinstance(notifier, DiscordNotifier)
    with pytest.raises(DiscordNotifierError):
        DiscordNotifier.from_env()


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


def test_notify_batch_sends_up_to_ten_embeds_per_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """25 件なら 10 + 10 + 5 の 3 リクエストにまとまる。"""
    notifier = DiscordNotifier("https://discord.example.com/webhook/xyz")
    events = [make_event(url=f"https://example.com/pokemon-{i}") for i in range(25)]

    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.raise_for_status.return_value = None
    mock_post = MagicMock(return_value=mock_response)
    monkeypatch.setattr("src.notifiers.discord.requests.post", mock_post)

    results = notifier.notify_batch(events)

    assert results == [True] * 25
    assert mock_post.call_count == 3
    embed_counts = [len(call.kwargs["json"]["embeds"]) for call in mock_post.call_args_list]
    assert embed_counts == [10, 10, 5]


def test_notify_batch_marks_whole_chunk_failed_on_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """まとめ送信は 1 リクエスト単位でしか成否が分からないため、失敗はチャンク全体に及ぶ。"""
    notifier = DiscordNotifier("https://discord.example.com/webhook/xyz")
    events = [make_event(url=f"https://example.com/pokemon-{i}") for i in range(12)]

    ok_response = MagicMock()
    ok_response.status_code = 200
    ok_response.raise_for_status.return_value = None
    mock_post = MagicMock(
        side_effect=[requests.RequestException("接続エラー"), ok_response]
    )
    monkeypatch.setattr("src.notifiers.discord.requests.post", mock_post)

    results = notifier.notify_batch(events)

    assert results == [False] * 10 + [True] * 2


def test_notify_retries_after_rate_limit_and_succeeds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """429 を受けたら Retry-After 秒だけ待って再送し、成功すれば True を返す。"""
    notifier = DiscordNotifier("https://discord.example.com/webhook/xyz")

    limited = MagicMock()
    limited.status_code = 429
    limited.headers = {"Retry-After": "1.5"}
    ok_response = MagicMock()
    ok_response.status_code = 200
    ok_response.raise_for_status.return_value = None
    mock_post = MagicMock(side_effect=[limited, ok_response])
    monkeypatch.setattr("src.notifiers.discord.requests.post", mock_post)

    slept: list[float] = []
    monkeypatch.setattr("src.notifiers.discord.time.sleep", slept.append)

    assert notifier.notify(make_event()) is True
    assert mock_post.call_count == 2
    assert slept == [1.5]


def test_notify_gives_up_when_rate_limit_never_clears(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """429 が続いたら上限回数で諦めて False を返す（例外は投げない）。"""
    notifier = DiscordNotifier("https://discord.example.com/webhook/xyz")

    limited = MagicMock()
    limited.status_code = 429
    limited.headers = {"Retry-After": "0.1"}
    limited.raise_for_status.side_effect = requests.HTTPError("429 Too Many Requests")
    mock_post = MagicMock(return_value=limited)
    monkeypatch.setattr("src.notifiers.discord.requests.post", mock_post)
    monkeypatch.setattr("src.notifiers.discord.time.sleep", lambda _: None)

    assert notifier.notify(make_event()) is False


def test_retry_after_falls_back_when_header_unusable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Retry-After が無い / 壊れている / 長すぎる場合の待ち時間を検証する。"""
    from src.notifiers.discord import (
        FALLBACK_RETRY_AFTER_SECONDS,
        MAX_RETRY_AFTER_SECONDS,
        _retry_after_seconds,
    )

    def response_with(headers: dict[str, str]) -> Any:
        response = MagicMock()
        response.headers = headers
        return response

    assert _retry_after_seconds(response_with({})) == FALLBACK_RETRY_AFTER_SECONDS
    assert _retry_after_seconds(response_with({"Retry-After": "abc"})) == (
        FALLBACK_RETRY_AFTER_SECONDS
    )
    assert _retry_after_seconds(response_with({"Retry-After": "0"})) == (
        FALLBACK_RETRY_AFTER_SECONDS
    )
    assert _retry_after_seconds(response_with({"Retry-After": "9999"})) == (
        MAX_RETRY_AFTER_SECONDS
    )
