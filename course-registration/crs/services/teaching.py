"""Select Courses to Teach / Submit Grades / 花名册 / View Report Card 用例。

成员D 负责"成绩数据"的写入（教授录入）与读取（学生成绩单），读写由同一人负责，
保证成绩规则（A/B/C/D/F/I、留空表示暂不录入）只有一处实现。

负责人：成员D    依赖：semesters(E) offerings(A) catalog(A)
"""
from __future__ import annotations

from dataclasses import dataclass

from ..clock import to_iso
from ..domain import GRADES, find_conflicts, gpa
from ..errors import (CatalogUnavailableError, ConflictError, NotFoundError, PermissionDeniedError,
                      ValidationError)
from .registration import parse_id_list


@dataclass(frozen=True)
class RosterEntry:
    student_id: int
    student_name: str
    kind: str
    status: str
    grade: str | None


@dataclass(frozen=True)
class ReportCardLine:
    course_id: str
    title: str
    section: str
    credits: int | None
    professor_name: str | None
    grade: str | None


@dataclass(frozen=True)
class ReportCard:
    semester_code: str
    semester_name: str
    lines: tuple
    gpa: float | None
    catalog_degraded: bool        # 遗留目录不可用时，只显示课程号（成绩数据仍然可用）


class TeachingService:
    def __init__(self, db, clock, semesters, offerings):
        self.db = db
        self.clock = clock
        self.semesters = semesters
        self.offerings = offerings

    # ================================================================ 选择授课
    def _professor(self, conn, professor_id):
        row = conn.execute("SELECT * FROM professor WHERE id = ?", (professor_id,)).fetchone()
        if row is None:
            raise NotFoundError("教授信息不存在，请联系注册员")
        return row

    def teaching_options(self, professor_id: int):
        """返回 (学期, 可授开课列表, 已选开课编号集合)。

        有资格的开课：本学期、本院系、尚未被其他教授认领、未取消。
        """
        sem = self.semesters.require_semester_open()
        if self.semesters.phase(sem) == "CLOSED":
            raise ConflictError("本学期注册已关闭")
        with self.db.reader() as conn:
            prof = self._professor(conn, professor_id)
            views = self.offerings.list_views(sem.code, conn)
        if prof["status"] == "RETIRED":
            raise PermissionDeniedError("退休状态的教授不能选择授课")
        eligible = [v for v in views if v.department == prof["department"] and v.status == "OPEN"
                    and v.professor_id in (None, professor_id)]
        if not eligible:
            raise NotFoundError("本学期没有您可以选择讲授的开课")
        mine = {v.offering_id for v in views if v.professor_id == professor_id}
        return sem, eligible, mine

    def select_courses_to_teach(self, professor_id: int, selected_ids) -> dict:
        """原子地更新授课安排。返回 {'added': [...], 'removed': [...]}。

        - 检测所选开课之间的时间冲突（列出冲突课程），有冲突则整体不生效；
        - 并发：若某开课在此期间已被其他教授认领，报冲突，整体不生效。
        """
        selected = set(parse_id_list(selected_ids))
        sem = self.semesters.require_semester_open()
        snapshot = self.offerings.catalog.snapshot(sem.code)
        with self.db.transaction() as conn:
            current = self.semesters.require_semester_open(conn)
            if current.code != sem.code:
                raise ConflictError("学期已切换，请刷新授课目录")
            self.offerings.ensure_states(conn, snapshot)
            prof = self._professor(conn, professor_id)
            if prof["status"] == "RETIRED":
                raise PermissionDeniedError("退休状态的教授不能选择授课")
            states = {r["offering_id"]: r for r in conn.execute(
                "SELECT * FROM offering_state WHERE semester = ?", (sem.code,))}
            for oid in selected:
                off = snapshot.get_offering(oid)
                state = states.get(oid)
                if off is None or state is None:
                    raise ValidationError({"offering": f"开课 {oid} 不属于本学期课程目录"})
                if snapshot.courses[off.course_id].department != prof["department"]:
                    raise PermissionDeniedError(f"{off.course_id} 不属于您所在院系，不能选择讲授")
                if state["status"] != "OPEN":
                    raise ConflictError(f"{off.course_id}-{off.section} 已不再开放")
                if state["professor_id"] not in (None, professor_id):
                    raise ConflictError(f"{off.course_id}-{off.section} 已由其他教授认领，请刷新后重新选择")

            slots = {oid: snapshot.offerings[oid].slot for oid in selected}
            conflicts = find_conflicts(slots)
            if conflicts:
                def lbl(o):
                    off = snapshot.offerings[o]
                    return f"{off.course_id}-{off.section}（{off.slot}）"
                raise ConflictError("所选开课存在时间冲突，请取消其中一门后重试",
                                    [f"{lbl(a)} 与 {lbl(b)}" for a, b in conflicts])

            current = {oid for oid, st in states.items() if st["professor_id"] == professor_id}
            removed, added = current - selected, selected - current
            for oid in removed:
                conn.execute("UPDATE offering_state SET professor_id = NULL WHERE offering_id = ? AND professor_id = ?",
                             (oid, professor_id))
            for oid in added:
                cur = conn.execute("UPDATE offering_state SET professor_id = ? WHERE offering_id = ? "
                                   "AND professor_id IS NULL", (professor_id, oid))
                if cur.rowcount != 1:      # 并发保护：只认领无人认领的开课
                    raise ConflictError("部分开课刚被其他教授认领，请刷新后重新选择")

        def names(ids):
            return sorted(f"{snapshot.offerings[o].course_id}-{snapshot.offerings[o].section}" for o in ids)
        return {"added": names(added), "removed": names(removed)}

    # ================================================================ 花名册
    def teaching_offerings(self, professor_id: int, semester_code: str):
        with self.db.reader() as conn:
            rows = conn.execute("SELECT offering_id FROM offering_state WHERE semester = ? AND professor_id = ?",
                                (semester_code, professor_id)).fetchall()
            ids = {r["offering_id"] for r in rows}
            if not ids:
                return []
            views = self.offerings.list_views(semester_code, conn)
        return [v for v in views if v.offering_id in ids]

    def roster(self, professor_id: int, offering_id: int) -> list[RosterEntry]:
        with self.db.reader() as conn:
            self._require_owner(conn, professor_id, offering_id)
            rows = conn.execute(
                "SELECT s.id, s.name, si.kind, si.status, g.grade FROM schedule_item si "
                "JOIN schedule sc ON sc.id = si.schedule_id JOIN student s ON s.id = sc.student_id "
                "LEFT JOIN grade g ON g.student_id = s.id AND g.offering_id = si.offering_id "
                "WHERE si.offering_id = ? AND si.status IN ('ENROLLED', 'COMMITTED') ORDER BY s.id",
                (offering_id,),
            ).fetchall()
        return [RosterEntry(r["id"], r["name"], r["kind"], r["status"], r["grade"]) for r in rows]

    def _require_owner(self, conn, professor_id, offering_id):
        row = conn.execute("SELECT * FROM offering_state WHERE offering_id = ?", (offering_id,)).fetchone()
        if row is None:
            raise NotFoundError("开课不存在")
        if row["professor_id"] != professor_id:
            # 补充规约：教授不能操作其他教授的开课
            raise PermissionDeniedError("您不是该开课的任课教授，无权查看或录入成绩")
        return row

    # ================================================================ 提交成绩
    def previous_semester(self):
        closed = self.semesters.closed_semesters()
        return closed[0] if closed else None

    def gradable_offerings(self, professor_id: int):
        """上一学期该教授讲授且未被取消的开课（Submit Grades 第 1 步）。"""
        sem = self.previous_semester()
        offerings = []
        if sem:
            offerings = [v for v in self._views_degradable(sem.code, professor_id) if v["status"] == "CLOSED"]
        if not offerings:
            raise NotFoundError("您在上一学期没有讲授任何课程，无需提交成绩")
        return sem, offerings

    def _views_degradable(self, semester_code, professor_id=None):
        """读取开课视图；遗留目录不可用时降级为只有课程号的简化视图，成绩功能仍可用。"""
        with self.db.reader() as conn:
            sql = "SELECT * FROM offering_state WHERE semester = ?"
            args = [semester_code]
            if professor_id is not None:
                sql += " AND professor_id = ?"
                args.append(professor_id)
            states = conn.execute(sql + " ORDER BY course_id, offering_id", args).fetchall()
        try:
            views = self.offerings.views_by_id(semester_code)
        except CatalogUnavailableError:
            views = {}
        result = []
        for st in states:
            v = views.get(st["offering_id"])
            result.append({
                "offering_id": st["offering_id"], "course_id": st["course_id"], "status": st["status"],
                "title": v.title if v else "（课程目录暂不可用）", "section": v.section if v else "",
                "credits": v.credits if v else None, "slot": str(v.slot) if v else "",
                "professor_name": v.professor_name if v else None, "degraded": v is None,
            })
        return result

    def submit_grades(self, professor_id: int, offering_id: int, grades: dict) -> int:
        """grades: {student_id: 'A'|'B'|...|''}；空值表示跳过（保留原成绩或暂不录入）。返回更新条数。"""
        sem = self.previous_semester()
        cleaned, errors = {}, {}
        for sid, g in (grades or {}).items():
            g = (g or "").strip().upper()
            if not g:
                continue
            if g not in GRADES:
                errors[str(sid)] = f"学生 {sid} 的成绩“{g}”无效，只能为 {'/'.join(GRADES)}"
                continue
            try:
                cleaned[int(sid)] = g
            except ValueError:
                errors[str(sid)] = f"非法的学生编号 {sid}"
        if errors:
            raise ValidationError(errors)

        now = to_iso(self.clock.now())
        with self.db.transaction() as conn:
            state = self._require_owner(conn, professor_id, offering_id)
            if sem is None or state["semester"] != sem.code or state["status"] != "CLOSED":
                raise PermissionDeniedError("只能为上一学期已结课的开课录入成绩")
            roster = {r["student_id"] for r in conn.execute(
                "SELECT sc.student_id FROM schedule_item si JOIN schedule sc ON sc.id = si.schedule_id "
                "WHERE si.offering_id = ? AND si.status = 'COMMITTED'", (offering_id,))}
            outsiders = sorted(set(cleaned) - roster)
            if outsiders:
                raise ValidationError({"roster": f"学生 {', '.join(map(str, outsiders))} 不在该课程花名册中"})
            changed = 0
            for sid, g in cleaned.items():
                cur = conn.execute(
                    "INSERT INTO grade(student_id, offering_id, semester, course_id, grade, recorded_by, updated_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?) "
                    "ON CONFLICT(student_id, offering_id) DO UPDATE SET grade = excluded.grade, "
                    "recorded_by = excluded.recorded_by, updated_at = excluded.updated_at "
                    "WHERE grade.grade <> excluded.grade",
                    (sid, offering_id, state["semester"], state["course_id"], g, professor_id, now),
                )
                changed += cur.rowcount
        return changed

    # ================================================================ 成绩单
    def report_card(self, student_id: int, semester_code: str | None = None) -> ReportCard:
        closed = self.semesters.closed_semesters()
        if semester_code:
            sem = next((s for s in closed if s.code == semester_code), None)
            if sem is None:
                raise NotFoundError("只能查看已结束学期的成绩单")
        else:
            sem = closed[0] if closed else None
        if sem is None:
            raise NotFoundError("暂无已结束学期的成绩信息")
        with self.db.reader() as conn:
            rows = conn.execute(
                "SELECT si.offering_id, os.course_id, g.grade FROM schedule_item si "
                "JOIN schedule sc ON sc.id = si.schedule_id "
                "JOIN offering_state os ON os.offering_id = si.offering_id "
                "LEFT JOIN grade g ON g.student_id = sc.student_id AND g.offering_id = si.offering_id "
                "WHERE sc.student_id = ? AND sc.semester = ? AND si.status = 'COMMITTED' ORDER BY os.course_id",
                (student_id, sem.code),
            ).fetchall()
        if not rows or all(r["grade"] is None for r in rows):
            raise NotFoundError(f"{sem.name}暂无您的成绩信息")
        views = {v["offering_id"]: v for v in self._views_degradable(sem.code)}
        lines = []
        for r in rows:
            v = views.get(r["offering_id"], {})
            lines.append(ReportCardLine(r["course_id"], v.get("title", ""), v.get("section", ""),
                                        v.get("credits"), v.get("professor_name"), r["grade"]))
        degraded = any(v.get("degraded") for v in views.values())
        return ReportCard(sem.code, sem.name, tuple(lines), gpa(l.grade for l in lines), degraded)
