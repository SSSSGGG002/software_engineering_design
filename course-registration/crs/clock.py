"""时钟抽象：业务代码不直接调用 datetime.now()，测试中可注入 FakeClock 控制时间。

负责人：成员A（架构）
"""
from __future__ import annotations

from datetime import datetime, timedelta


def to_iso(dt: datetime) -> str:
    return dt.replace(microsecond=0).isoformat(sep=" ")


def from_iso(text: str) -> datetime:
    return datetime.fromisoformat(text)


class SystemClock:
    def now(self) -> datetime:
        return datetime.now().replace(microsecond=0)


class FakeClock:
    """测试用时钟，可手动推进。"""

    def __init__(self, start: datetime | None = None):
        self._now = start or datetime(2026, 9, 10, 9, 0, 0)

    def now(self) -> datetime:
        return self._now

    def set(self, dt: datetime) -> None:
        self._now = dt

    def advance(self, **kwargs) -> None:
        self._now += timedelta(**kwargs)
