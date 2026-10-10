"""先修课检查：学生必须在**更早的已关闭学期**中以及格成绩（A–D）通过先修课程。

负责人：成员C    使用者：registration.py（成员C，提交课表）、closing.py（成员E，备选补位）
依赖：grade 表（成员D 写入）
"""
from __future__ import annotations

from ..domain import PASSING_GRADES


def passed_courses(conn, student_id: int, before_seq: int) -> set:
    rows = conn.execute(
        "SELECT g.course_id, g.grade FROM grade g JOIN semester s ON s.code = g.semester "
        "WHERE g.student_id = ? AND s.seq < ? AND s.status = 'CLOSED'",
        (student_id, before_seq),
    ).fetchall()
    return {r["course_id"] for r in rows if r["grade"] in PASSING_GRADES}


def missing_prerequisites(course, passed: set) -> list:
    """返回尚未通过的先修课程号（保持目录中的顺序）。"""
    return [p for p in course.prerequisites if p not in passed]
