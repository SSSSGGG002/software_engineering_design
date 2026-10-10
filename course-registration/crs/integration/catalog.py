"""遗留课程目录系统（Ingres on DEC VAX）的访问适配层。

问题陈述约束：
- 新系统只读访问遗留库，不更新它（这里用 SQLite 只读 URI 模拟，写操作在驱动层即被拒绝）；
- 遗留库性能差，需要"创造性地利用中间层处理能力"——
  本模块用 **按学期批量拉取 + 中间层缓存（TTL）+ 单飞锁** 解决：
  2000 个并发用户浏览同一学期课程时，只有第一个请求真正访问遗留库。
- 遗留库不可用时抛出 CatalogUnavailableError，由上层按用例备选流显示错误。

负责人：成员A    使用者：成员C（选课）、成员D（选择授课/成绩）、成员E（关闭注册）
"""
from __future__ import annotations

import logging
import sqlite3
import threading
import time
from dataclasses import dataclass, field

from ..domain import TimeSlot
from ..errors import CatalogUnavailableError

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Course:
    course_id: str
    title: str
    credits: int
    department: str
    description: str
    prerequisites: tuple = ()


@dataclass(frozen=True)
class Offering:
    offering_id: int
    course_id: str
    semester: str
    section: str
    slot: TimeSlot
    room: str


@dataclass(frozen=True)
class CatalogSnapshot:
    """某学期课程目录的一致快照。"""

    semester: str
    offerings: dict = field(default_factory=dict)   # offering_id -> Offering
    courses: dict = field(default_factory=dict)     # course_id -> Course（全量，供先修课名称展示）

    def course_of(self, offering_id: int) -> Course:
        return self.courses[self.offerings[offering_id].course_id]

    def get_offering(self, offering_id: int) -> Offering | None:
        return self.offerings.get(offering_id)


class LegacyCatalogClient:
    """模拟"开放 SQL 接口"访问遗留库。latency 模拟遗留系统的慢速响应。"""

    def __init__(self, path: str, latency_seconds: float = 0.0, availability=lambda: True):
        self.path = path
        self.latency_seconds = latency_seconds
        self.availability = availability
        self.fetch_count = 0          # 用于测试/演示：真正访问遗留库的次数

    def fetch_semester(self, semester: str) -> CatalogSnapshot:
        if not self.availability():
            raise CatalogUnavailableError()
        self.fetch_count += 1
        if self.latency_seconds:
            time.sleep(self.latency_seconds)
        try:
            # mode=ro：只读打开，保证"不更新遗留库"
            conn = sqlite3.connect(f"file:{self.path}?mode=ro", uri=True, timeout=10)
        except sqlite3.Error as exc:
            log.error("无法连接遗留课程目录: %s", exc)
            raise CatalogUnavailableError() from exc
        try:
            conn.row_factory = sqlite3.Row
            prereqs: dict = {}
            for r in conn.execute("SELECT course_id, prereq_id FROM prerequisite ORDER BY prereq_id"):
                prereqs.setdefault(r["course_id"], []).append(r["prereq_id"])
            courses = {
                r["course_id"]: Course(
                    r["course_id"], r["title"], int(r["credits"]), r["department"],
                    r["description"] or "", tuple(prereqs.get(r["course_id"], ())),
                )
                for r in conn.execute("SELECT * FROM course")
            }
            offerings = {}
            for r in conn.execute(
                "SELECT * FROM course_offering WHERE semester = ? ORDER BY course_id, section", (semester,)
            ):
                try:
                    slot = TimeSlot.parse(r["days"], r["start_time"], r["end_time"])
                except ValueError:
                    # 遗留数据质量问题不能拖垮整个系统：记录日志并跳过该条
                    log.warning("遗留库开课 %s 时间数据非法，已跳过", r["offering_id"])
                    continue
                if r["course_id"] not in courses:
                    log.warning("遗留库开课 %s 引用了不存在的课程 %s，已跳过", r["offering_id"], r["course_id"])
                    continue
                offerings[int(r["offering_id"])] = Offering(
                    int(r["offering_id"]), r["course_id"], r["semester"], r["section"], slot, r["room"] or ""
                )
            return CatalogSnapshot(semester, offerings, courses)
        except sqlite3.Error as exc:
            log.error("读取遗留课程目录失败: %s", exc)
            raise CatalogUnavailableError() from exc
        finally:
            conn.close()


class CatalogService:
    """中间层缓存。线程安全；同一学期并发请求只触发一次遗留库访问。"""

    def __init__(self, client: LegacyCatalogClient, ttl_seconds: float, clock):
        self.client = client
        self.ttl_seconds = ttl_seconds
        self.clock = clock
        self._cache: dict = {}            # semester -> (snapshot, fetched_at)
        self._lock = threading.Lock()

    def snapshot(self, semester: str) -> CatalogSnapshot:
        cached = self._fresh(semester)
        if cached:
            return cached
        with self._lock:                 # 单飞：其他线程等待第一个线程的结果
            cached = self._fresh(semester)
            if cached:
                return cached
            snap = self.client.fetch_semester(semester)
            self._cache[semester] = (snap, self.clock.now())
            return snap

    def _fresh(self, semester: str):
        entry = self._cache.get(semester)
        if entry and (self.clock.now() - entry[1]).total_seconds() < self.ttl_seconds:
            return entry[0]
        return None

    def invalidate(self) -> None:
        with self._lock:
            self._cache.clear()
