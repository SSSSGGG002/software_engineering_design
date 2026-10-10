"""学期与注册周期管理。

注册周期（状态机）：
    PLANNED --开放--> OPEN(加退选进行中) --加退选截止--> OPEN(待关闭) --关闭注册--> CLOSED

"注册进行中"的判定：学期为 OPEN 且当前时间早于加退选截止时间。
- 学生选课（成员C）要求注册进行中；
- 教授选择授课（成员D）要求学期未关闭；
- 关闭注册（成员E）要求注册**不在**进行中（用例 Close Registration 第 1 步）。

负责人：成员E    使用者：成员C、成员D
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from ..clock import from_iso, to_iso
from ..errors import ConflictError, NotFoundError, RegistrationClosedError, ValidationError

PHASE_IN_PROGRESS = "IN_PROGRESS"     # 加退选进行中
PHASE_AWAITING_CLOSE = "AWAITING_CLOSE"
PHASE_CLOSED = "CLOSED"
PHASE_PLANNED = "PLANNED"

PHASE_LABELS = {
    PHASE_IN_PROGRESS: "加退选进行中",
    PHASE_AWAITING_CLOSE: "加退选已截止，待关闭注册",
    PHASE_CLOSED: "注册已关闭",
    PHASE_PLANNED: "未开放",
}


@dataclass(frozen=True)
class Semester:
    code: str
    name: str
    seq: int
    status: str
    add_drop_end: datetime | None
    closed_at: str | None

    @classmethod
    def from_row(cls, row) -> "Semester":
        return cls(row["code"], row["name"], row["seq"], row["status"],
                   from_iso(row["add_drop_end"]) if row["add_drop_end"] else None, row["closed_at"])


class SemesterService:
    def __init__(self, db, clock, catalog=None, offerings=None):
        self.db = db
        self.clock = clock
        self.catalog = catalog
        self.offerings = offerings

    # ---- 查询 ----
    def list_all(self) -> list[Semester]:
        with self.db.reader() as conn:
            return [Semester.from_row(r) for r in conn.execute("SELECT * FROM semester ORDER BY seq")]

    def get(self, code: str, conn=None) -> Semester:
        with self.db.using(conn) as c:
            row = c.execute("SELECT * FROM semester WHERE code = ?", (code,)).fetchone()
        if not row:
            raise NotFoundError(f"学期 {code} 不存在")
        return Semester.from_row(row)

    def current(self, conn=None) -> Semester | None:
        """当前开放的学期（至多一个）。"""
        with self.db.using(conn) as c:
            row = c.execute("SELECT * FROM semester WHERE status = 'OPEN'").fetchone()
        return Semester.from_row(row) if row else None

    def closed_semesters(self, conn=None) -> list[Semester]:
        with self.db.using(conn) as c:
            rows = c.execute("SELECT * FROM semester WHERE status = 'CLOSED' ORDER BY seq DESC").fetchall()
        return [Semester.from_row(r) for r in rows]

    def phase(self, sem: Semester | None) -> str:
        if sem is None or sem.status == "CLOSED":
            return PHASE_CLOSED
        if sem.status == "PLANNED":
            return PHASE_PLANNED
        if sem.add_drop_end and self.clock.now() < sem.add_drop_end:
            return PHASE_IN_PROGRESS
        return PHASE_AWAITING_CLOSE

    # ---- 守卫（供其他服务在事务内调用） ----
    def require_registration_in_progress(self, conn=None) -> Semester:
        sem = self.current(conn)
        phase = self.phase(sem)
        if phase != PHASE_IN_PROGRESS:
            if sem is not None and phase == PHASE_AWAITING_CLOSE:
                raise RegistrationClosedError(f"{sem.name}加退选期已于 {to_iso(sem.add_drop_end)} 截止，不能再修改课表")
            raise RegistrationClosedError("本学期选课注册已关闭，不能再选课")
        return sem

    def require_semester_open(self, conn=None) -> Semester:
        sem = self.current(conn)
        if sem is None:
            raise RegistrationClosedError("本学期注册已关闭，不能再修改授课安排")
        return sem

    # ---- 注册员操作 ----
    def open_semester(self, code: str, add_drop_end_text: str) -> Semester:
        try:
            add_drop_end = datetime.fromisoformat(add_drop_end_text.strip().replace("T", " "))
        except (ValueError, AttributeError):
            raise ValidationError({"add_drop_end": "加退选截止时间格式应为 YYYY-MM-DD HH:MM"})
        if add_drop_end.tzinfo is not None:
            raise ValidationError({"add_drop_end": "请使用服务器本地时间，不附带时区偏移"})
        if add_drop_end <= self.clock.now():
            raise ValidationError({"add_drop_end": "加退选截止时间必须晚于当前时间"})
        # 慢速遗留库访问放在写事务之外，避免长时间持有写锁
        snapshot = self.catalog.snapshot(code)
        with self.db.transaction() as conn:
            sem = self.get(code, conn)
            if sem.status != "PLANNED":
                raise ConflictError(f"{sem.name}当前状态为 {sem.status}，只有未开放的学期才能开放")
            if self.current(conn):
                raise ConflictError("已有开放中的学期，请先关闭该学期的注册")
            conn.execute("UPDATE semester SET status = 'OPEN', add_drop_end = ? WHERE code = ?",
                         (to_iso(add_drop_end), code))
            self.offerings.ensure_states(conn, snapshot)
        return self.get(code)

    def end_add_drop(self, code: str) -> Semester:
        """演示/运维用：立即结束加退选期（等价于把截止时间设为现在）。"""
        with self.db.transaction() as conn:
            sem = self.get(code, conn)
            if sem.status != "OPEN":
                raise ConflictError(f"{sem.name}不是开放中的学期")
            conn.execute("UPDATE semester SET add_drop_end = ? WHERE code = ?", (to_iso(self.clock.now()), code))
        return self.get(code)
