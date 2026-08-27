"""Notifier の共通インターフェース。

Notifier の責務は通知の送信だけ。保存や重複判定は行わない。
将来 Slack / メール等を足す場合も、この基底クラスを実装する。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Sequence

from src.models import Event


class Notifier(ABC):
    """通知先の抽象基底クラス。"""

    @abstractmethod
    def notify(self, event: Event) -> bool:
        """1 件通知する。成功なら True、失敗なら False。

        通知失敗でアプリ全体を止めないため、例外は送出せず False を返す。
        """

    def notify_batch(self, events: Sequence[Event]) -> list[bool]:
        """複数件まとめて通知し、events と同じ長さの成否リストを返す。

        既定では 1 件ずつ notify() を呼ぶだけ。まとめ送信に対応した通知先
        （Discord の複数 embed など）は、このメソッドをオーバーライドする。
        """
        return [self.notify(event) for event in events]
