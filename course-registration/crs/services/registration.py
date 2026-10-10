"""Register for Courses 用例（创建 / 更新 / 删除 / 保存 / 提交课表）。

关键设计（对应需求中的鲁棒性要求）：
1. 原子性：一次保存或提交在一个写事务（BEGIN IMMEDIATE）内完成，任一课程被拒则整体回滚，
   满足"失败时系统状态不变"；
2. 名额不超卖：名额检查与写入处于同一写事务，事务串行执行，不会出现两人同时抢到第 10 个名额；
3. 乐观锁：课表带 version，两个浏览器窗口同时编辑时，后提交者收到"请刷新"提示，不会静默覆盖；
4. 满员通知：提交时在事务内重新检查名额；课表页面还会轮询 /student/api/seats 实时提示满员
   （补充规约："课程在学生编排课表期间满员，必须通知学生"）。

课表条目状态：
    SELECTED  已选未提交（保存草稿）      ENROLLED  已注册（占用名额）
    COMMITTED 关闭注册后确认               CANCELLED 关闭注册时被取消

负责人：成员C    依赖：semesters(E) offerings(A) prereq(C) grade 表(D)
"""
from __future__ import annotations

from dataclasses import dataclass

from ..clock import to_iso
from ..domain import ITEM_KIND, MAX_ALTERNATE, MAX_PRIMARY, MAX_SEATS, find_conflicts
from ..errors import (ConflictError, NotFoundError, PermissionDeniedError, ScheduleRejectedError,
                      ValidationError)
from .offerings import OfferingView, enrolled_count
from .prereq import missing_prerequisites, passed_courses


@dataclass(frozen=True)
class ScheduleItemView:
    offering: OfferingView
    kind: str
    priority: int
    status: str
    note: str | None

    @property
    def kind_label(self) -> str:
        return ITEM_KIND[self.kind]


@dataclass(frozen=True)
class ScheduleView:
    id: int
    student_id: int
    semester: str
    status: str
    version: int
    updated_at: str
    items: tuple

    @property
    def primaries(self):
        return [i for i in self.items if i.kind == "PRIMARY"]

    @property
    def alternates(self):
        return [i for i in self.items if i.kind == "ALTERNATE"]

    @property
    def enrolled_credits(self) -> int:
        return sum(i.offering.credits for i in self.items if i.status in ("ENROLLED", "COMMITTED"))


def parse_id_list(values) -> list[int]:
    """把表单里的多值字段转换为去掉空值的整数列表，非法值报校验错误。"""
    result = []
    for v in values or []:
        v = str(v).strip()
        if not v:
            continue
        if not v.isdigit():
            raise ValidationError({"offering": f"非法的开课编号: {v}"})
        result.append(int(v))
    return result


class RegistrationService:
    def __init__(self, db, clock, semesters, offerings):
        self.db = db
        self.clock = clock
        self.semesters = semesters
        self.offerings = offerings

    # ------------------------------------------------------------------ 查询
    def get_schedule(self, student_id: int, semester: str | None = None) -> ScheduleView | None:
        sem = self.semesters.get(semester) if semester else self.semesters.current()
        if sem is None:
            return None
        with self.db.reader() as conn:
            row = conn.execute("SELECT * FROM schedule WHERE student_id = ? AND semester = ?",
                               (student_id, sem.code)).fetchone()
            if not row:
                return None
            views = self.offerings.views_by_id(sem.code, conn)
            return self._build_view(conn, row, views)

    def _build_view(self, conn, row, views) -> ScheduleView:
        items = []
        for it in conn.execute("SELECT * FROM schedule_item WHERE schedule_id = ? ORDER BY kind DESC, priority",
                               (row["id"],)):
            view = views.get(it["offering_id"])
            if view is None:
                raise ConflictError(f"开课 {it['offering_id']} 已从目录移除，请联系注册员核对课表")
            items.append(ScheduleItemView(view, it["kind"], it["priority"], it["status"], it["note"]))
        return ScheduleView(row["id"], row["student_id"], row["semester"], row["status"], row["version"],
                            row["updated_at"], tuple(items))

    def available_offerings(self) -> list[OfferingView]:
        """当前学期可选的开课（Create a Schedule 第 1 步）。遗留库不可用时抛 CatalogUnavailableError。"""
        sem = self.semesters.require_registration_in_progress()
        return [v for v in self.offerings.list_views(sem.code) if v.status == "OPEN"]

    def seat_status(self) -> dict:
        sem = self.semesters.current()
        if sem is None:
            return {}
        return {v.offering_id: {"enrolled": v.enrolled, "left": v.seats_left, "full": v.is_full}
                for v in self.offerings.list_views(sem.code)}

    # ------------------------------------------------------------------ 保存 / 提交
    def save_schedule(self, student_id: int, primary_ids, alternate_ids, expected_version, submit: bool):
        """创建或更新课表。submit=False 对应"保存"备选流，submit=True 对应 Submit Schedule 子流。

        返回 (ScheduleView, 本次新注册的开课列表)。
        """
        primary_ids = parse_id_list(primary_ids)
        alternate_ids = parse_id_list(alternate_ids)
        sem = self.semesters.require_registration_in_progress()
        snapshot = self.offerings.catalog.snapshot(sem.code)    # 慢速访问放在事务外
        self._validate_selection(primary_ids, alternate_ids, snapshot, submit)

        now = to_iso(self.clock.now())
        with self.db.transaction() as conn:
            # 事务内再次确认（防止在本次请求处理期间注册被关闭）
            sem = self.semesters.require_registration_in_progress(conn)
            if sem.code != snapshot.semester:
                raise ConflictError("学期已切换，请刷新页面后重新选择")
            self.offerings.ensure_states(conn, snapshot)
            self._require_active_student(conn, student_id)
            row = conn.execute("SELECT * FROM schedule WHERE student_id = ? AND semester = ?",
                               (student_id, sem.code)).fetchone()
            if row is None:
                if expected_version not in (None, "", 0, "0"):
                    raise ConflictError("课表已在其他窗口被删除，请刷新页面后重试")
                cur = conn.execute(
                    "INSERT INTO schedule(student_id, semester, status, version, created_at, updated_at) "
                    "VALUES (?, ?, 'DRAFT', 1, ?, ?)", (student_id, sem.code, now, now))
                schedule_id, version = cur.lastrowid, 1
            else:
                if str(expected_version) != str(row["version"]):
                    raise ConflictError("课表已在其他窗口被修改，请刷新页面后再操作")
                schedule_id, version = row["id"], row["version"]

            existing = {r["offering_id"]: r for r in
                        conn.execute("SELECT * FROM schedule_item WHERE schedule_id = ?", (schedule_id,))}
            wanted = [(oid, "PRIMARY", i + 1) for i, oid in enumerate(primary_ids)] + \
                     [(oid, "ALTERNATE", i + 1) for i, oid in enumerate(alternate_ids)]
            wanted_ids = {oid for oid, _, _ in wanted}

            # 1) 退选：不在新选择中的条目删除（已注册的同时释放名额）
            for oid in existing:
                if oid not in wanted_ids:
                    conn.execute("DELETE FROM schedule_item WHERE schedule_id = ? AND offering_id = ?",
                                 (schedule_id, oid))
            # 2) 新增 / 调整
            for oid, kind, priority in wanted:
                old = existing.get(oid)
                if old is None:
                    conn.execute(
                        "INSERT INTO schedule_item(schedule_id, offering_id, kind, priority, status) "
                        "VALUES (?, ?, ?, ?, 'SELECTED')", (schedule_id, oid, kind, priority))
                else:
                    # 已注册的首选课被改为备选，视为退选该课，释放名额
                    status = old["status"]
                    if kind == "ALTERNATE" and status == "ENROLLED":
                        status = "SELECTED"
                    conn.execute("UPDATE schedule_item SET kind = ?, priority = ?, status = ?, note = NULL "
                                 "WHERE id = ?", (kind, priority, status, old["id"]))

            newly_enrolled = []
            if submit:
                newly_enrolled = self._submit(conn, student_id, schedule_id, sem, snapshot)

            has_enrolled = conn.execute(
                "SELECT 1 FROM schedule_item WHERE schedule_id = ? AND status = 'ENROLLED' LIMIT 1",
                (schedule_id,)).fetchone()
            conn.execute("UPDATE schedule SET status = ?, version = ?, updated_at = ? WHERE id = ?",
                         ("SUBMITTED" if has_enrolled else "DRAFT", version + 1, now, schedule_id))
            result_row = conn.execute("SELECT * FROM schedule WHERE id = ?", (schedule_id,)).fetchone()
            result = self._build_view(conn, result_row, self.offerings.views_by_id(sem.code, conn, snapshot))
        return result, newly_enrolled

    def _validate_selection(self, primary_ids, alternate_ids, snapshot, submit):
        errors = {}
        if len(primary_ids) > MAX_PRIMARY:
            errors["primary"] = f"首选课程最多 {MAX_PRIMARY} 门"
        if len(alternate_ids) > MAX_ALTERNATE:
            errors["alternate"] = f"备选课程最多 {MAX_ALTERNATE} 门"
        if submit and not primary_ids:
            errors["primary"] = "提交注册至少需要选择 1 门首选课程"
        all_ids = primary_ids + alternate_ids
        if len(set(all_ids)) != len(all_ids):
            errors["duplicate"] = "同一开课不能重复选择"
        unknown = [str(i) for i in all_ids if snapshot.get_offering(i) is None]
        if unknown:
            errors["unknown"] = f"开课 {', '.join(unknown)} 不属于本学期课程目录"
        else:
            courses = [snapshot.offerings[i].course_id for i in all_ids]
            dup_courses = sorted({c for c in courses if courses.count(c) > 1})
            if dup_courses and "duplicate" not in errors:
                errors["duplicate_course"] = f"同一门课程只能选择一个班次：{', '.join(dup_courses)}"
        if errors:
            raise ValidationError(errors)

    def _require_active_student(self, conn, student_id):
        row = conn.execute("SELECT status FROM student WHERE id = ?", (student_id,)).fetchone()
        if row is None:
            raise NotFoundError("学生信息不存在，请联系注册员")
        if row["status"] != "ENROLLED":
            raise PermissionDeniedError("只有“在读”状态的学生可以选课，请联系注册员")

    def _submit(self, conn, student_id, schedule_id, sem, snapshot):
        """Submit Schedule 子流：逐门检查先修课、开放状态、名额、时间冲突。任一失败则整体拒绝。"""
        items = conn.execute("SELECT * FROM schedule_item WHERE schedule_id = ? ORDER BY kind DESC, priority",
                             (schedule_id,)).fetchall()
        states = {r["offering_id"]: r for r in conn.execute(
            "SELECT * FROM offering_state WHERE semester = ?", (sem.code,))}
        passed = passed_courses(conn, student_id, sem.seq)
        problems = []

        def label(oid):
            off = snapshot.offerings[oid]
            return f"{off.course_id}-{off.section} {snapshot.courses[off.course_id].title}"

        to_enroll = []
        for it in items:
            oid = it["offering_id"]
            course = snapshot.course_of(oid)
            missing = missing_prerequisites(course, passed)
            if missing:
                names = "、".join(f"{p} {snapshot.courses[p].title}" if p in snapshot.courses else p
                                 for p in missing)
                problems.append(f"{label(oid)}：未通过先修课程 {names}")
                continue
            if it["kind"] != "PRIMARY" or it["status"] == "ENROLLED":
                continue
            state = states.get(oid)
            if state is None or state["status"] != "OPEN":
                problems.append(f"{label(oid)}：该开课已不再开放")
                continue
            if enrolled_count(conn, oid) >= MAX_SEATS:
                problems.append(f"{label(oid)}：课程已满（{MAX_SEATS}/{MAX_SEATS}），请改选其他班次或改为备选")
                continue
            to_enroll.append(oid)

        primary_slots = {it["offering_id"]: snapshot.offerings[it["offering_id"]].slot
                         for it in items if it["kind"] == "PRIMARY"}
        for a, b in find_conflicts(primary_slots):
            problems.append(f"时间冲突：{label(a)}（{primary_slots[a]}）与 {label(b)}（{primary_slots[b]}）")

        if problems:
            raise ScheduleRejectedError("课表未能提交，请处理以下问题后重试（也可以先保存课表）", problems)

        for oid in to_enroll:
            conn.execute("UPDATE schedule_item SET status = 'ENROLLED' WHERE schedule_id = ? AND offering_id = ?",
                         (schedule_id, oid))
        return [label(oid) for oid in to_enroll]

    # ------------------------------------------------------------------ 删除
    def delete_schedule(self, student_id: int, expected_version) -> None:
        with self.db.transaction() as conn:
            sem = self.semesters.require_registration_in_progress(conn)
            row = conn.execute("SELECT * FROM schedule WHERE student_id = ? AND semester = ?",
                               (student_id, sem.code)).fetchone()
            if row is None:
                raise NotFoundError("未找到您本学期的课表")
            if str(expected_version) != str(row["version"]):
                raise ConflictError("课表已在其他窗口被修改，请刷新页面后再操作")
            # 级联删除条目，已注册课程的名额随之释放
            conn.execute("DELETE FROM schedule WHERE id = ?", (row["id"],))
