"""开课读模型：把"遗留目录（只读、慢）"与"本地状态（教师、状态、人数）"合并成统一视图。

这是全组共享的咬合点：
- 成员C 用它展示课程目录、检查名额；
- 成员D 用它展示可授课程、花名册；
- 成员E 用它在关闭注册时统计人数。

负责人：成员A
"""
from __future__ import annotations

from dataclasses import dataclass

from ..domain import MAX_SEATS, TimeSlot

# 计入名额的课表条目状态
SEAT_STATUSES = ("ENROLLED", "COMMITTED")


@dataclass(frozen=True)
class OfferingView:
    offering_id: int
    course_id: str
    title: str
    credits: int
    department: str
    description: str
    prerequisites: tuple          # ((course_id, title), ...)
    section: str
    slot: TimeSlot
    room: str
    semester: str
    status: str
    professor_id: int | None
    professor_name: str | None
    enrolled: int

    @property
    def seats_left(self) -> int:
        return max(MAX_SEATS - self.enrolled, 0)

    @property
    def is_full(self) -> bool:
        return self.enrolled >= MAX_SEATS

    @property
    def label(self) -> str:
        return f"{self.course_id}-{self.section} {self.title}"


def enrolled_count(conn, offering_id: int) -> int:
    row = conn.execute(
        "SELECT COUNT(*) AS n FROM schedule_item WHERE offering_id = ? AND status IN (?, ?)",
        (offering_id, *SEAT_STATUSES),
    ).fetchone()
    return row["n"]


class OfferingQueries:
    def __init__(self, db, catalog):
        self.db = db
        self.catalog = catalog

    @staticmethod
    def ensure_states(conn, snapshot) -> None:
        """为快照中尚无本地状态的开课补建记录（幂等）。必须在写事务中调用。"""
        conn.executemany(
            "INSERT OR IGNORE INTO offering_state(offering_id, semester, course_id, status) VALUES (?, ?, ?, 'OPEN')",
            [(o.offering_id, o.semester, o.course_id) for o in snapshot.offerings.values()],
        )

    def list_views(self, semester: str, conn=None, snapshot=None) -> list[OfferingView]:
        snapshot = snapshot or self.catalog.snapshot(semester)
        with self.db.using(conn) as c:
            states = {
                r["offering_id"]: r
                for r in c.execute(
                    "SELECT os.*, p.name AS professor_name FROM offering_state os "
                    "LEFT JOIN professor p ON p.id = os.professor_id WHERE os.semester = ?",
                    (semester,),
                )
            }
            counts = {
                r["offering_id"]: r["n"]
                for r in c.execute(
                    "SELECT si.offering_id, COUNT(*) AS n FROM schedule_item si "
                    "JOIN offering_state os ON os.offering_id = si.offering_id "
                    "WHERE os.semester = ? AND si.status IN (?, ?) GROUP BY si.offering_id",
                    (semester, *SEAT_STATUSES),
                )
            }
        views = []
        for off in snapshot.offerings.values():
            course = snapshot.courses[off.course_id]
            st = states.get(off.offering_id)
            views.append(OfferingView(
                offering_id=off.offering_id,
                course_id=off.course_id,
                title=course.title,
                credits=course.credits,
                department=course.department,
                description=course.description,
                prerequisites=tuple(
                    (p, snapshot.courses[p].title if p in snapshot.courses else p) for p in course.prerequisites
                ),
                section=off.section,
                slot=off.slot,
                room=off.room,
                semester=off.semester,
                status=st["status"] if st else "OPEN",
                professor_id=st["professor_id"] if st else None,
                professor_name=st["professor_name"] if st else None,
                enrolled=counts.get(off.offering_id, 0),
            ))
        views.sort(key=lambda v: (v.course_id, v.section))
        return views

    def views_by_id(self, semester: str, conn=None, snapshot=None) -> dict:
        return {v.offering_id: v for v in self.list_views(semester, conn, snapshot)}
