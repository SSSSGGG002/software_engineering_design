"""领域常量与纯函数（无数据库依赖，便于单元测试）。

负责人：成员A（架构）  评审：全员
被以下模块依赖：registration(C)、teaching(D)、closing(E)、people(B)
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date

# ---- 业务规则常量（来自问题陈述与用例规约） ----
MAX_PRIMARY = 4          # 每名学生最多 4 门首选课程
MAX_ALTERNATE = 2        # 每名学生最多 2 门备选课程
MAX_SEATS = 10           # 每个开课最多 10 名学生
MIN_SEATS = 3            # 少于 3 名学生的开课将被取消

GRADES = ("A", "B", "C", "D", "F", "I")
PASSING_GRADES = frozenset({"A", "B", "C", "D"})
GRADE_POINTS = {"A": 4.0, "B": 3.0, "C": 2.0, "D": 1.0, "F": 0.0}  # I（未完成）不计入绩点

STUDENT_STATUS = {"ENROLLED": "在读", "SUSPENDED": "休学", "GRADUATED": "已毕业"}
PROFESSOR_STATUS = {"ACTIVE": "在职", "VISITING": "访问", "RETIRED": "退休"}
DEPARTMENTS = {"CS": "计算机系", "MATH": "数学系"}

ITEM_KIND = {"PRIMARY": "首选", "ALTERNATE": "备选"}
ITEM_STATUS = {
    "SELECTED": "已选（未提交）",
    "ENROLLED": "已注册",
    "COMMITTED": "已确认",
    "CANCELLED": "已取消",
}
OFFERING_STATUS = {"OPEN": "开放", "COMMITTED": "已确认", "CLOSED": "已关闭", "CANCELLED": "已取消"}

DAY_NAMES = {"M": "周一", "T": "周二", "W": "周三", "R": "周四", "F": "周五"}

SSN_PATTERN = re.compile(r"^\d{3}-\d{2}-\d{4}$")
TIME_PATTERN = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")


@dataclass(frozen=True)
class TimeSlot:
    """开课的上课时间：星期集合 + [start, end) 分钟区间。"""

    days: frozenset
    start: int
    end: int

    @classmethod
    def parse(cls, days: str, start: str, end: str) -> "TimeSlot":
        day_set = frozenset(days.strip().upper())
        if not day_set or not day_set <= DAY_NAMES.keys():
            raise ValueError(f"非法的星期字符串: {days!r}")
        s, e = _to_minutes(start), _to_minutes(end)
        if s >= e:
            raise ValueError(f"开始时间必须早于结束时间: {start}-{end}")
        return cls(day_set, s, e)

    def overlaps(self, other: "TimeSlot") -> bool:
        # 同一天且时间区间相交即为冲突；首尾相接（10:00 结束、10:00 开始）不算冲突
        return bool(self.days & other.days) and self.start < other.end and other.start < self.end

    def __str__(self) -> str:
        days = "、".join(DAY_NAMES[d] for d in "MTWRF" if d in self.days)
        return f"{days} {_fmt(self.start)}-{_fmt(self.end)}"


def _to_minutes(hhmm: str) -> int:
    m = TIME_PATTERN.match(hhmm.strip())
    if not m:
        raise ValueError(f"非法的时间: {hhmm!r}")
    return int(m.group(1)) * 60 + int(m.group(2))


def _fmt(minutes: int) -> str:
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


def find_conflicts(slots: dict) -> list[tuple]:
    """返回所有时间冲突的键对 (k1, k2)，k1 < k2，结果有序、确定。

    slots: {key: TimeSlot}
    """
    keys = sorted(slots)
    conflicts = []
    for i, a in enumerate(keys):
        for b in keys[i + 1:]:
            if slots[a].overlaps(slots[b]):
                conflicts.append((a, b))
    return conflicts


def gpa(grades) -> float | None:
    """计算平均绩点；I 不计入。没有可计入的成绩时返回 None。"""
    points = [GRADE_POINTS[g] for g in grades if g in GRADE_POINTS]
    if not points:
        return None
    return round(sum(points) / len(points), 2)


def parse_iso_date(text: str) -> date:
    return date.fromisoformat(text.strip())
