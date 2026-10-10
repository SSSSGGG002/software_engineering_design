"""Close Registration 用例。

严格按用例规约的基本流实现，全部步骤在**一个写事务**中完成：
任何一步出错（包括课程目录不可用）都会整体回滚，"系统状态保持不变"（后置条件）。

    第1步  注册仍在进行中（加退选未截止） → 拒绝
    第2步  有教授且已注册人数 ≥ 3 的开课 → 确认(COMMITTED)；无教授的开课 → 取消（备选流）
    第3步  "平衡"课表：已提交课表中有效首选不足 4 门的，按顺序从备选中补位
           （备选须：开课未取消、未满 10 人、先修课满足、与已有课程无时间冲突、不是同一门课）
    第4步  关闭所有开课：人数 ≥ 3 → CLOSED；否则 → CANCELLED，并从包含它的课表中取消
    第5步  计算每名学生学费，写入计费发件箱（与本事务一同提交）；事务提交后立即尝试发送，
           计费系统不可用时由后台按退避策略持续重发（备选流 Billing System Unavailable）

说明：未提交（只保存为草稿）的课表不参与平衡，其中条目在关闭时标记为取消，原因"课表未提交"。

负责人：成员E    依赖：semesters(E) offerings(A) prereq(C) billing(E)
"""
from __future__ import annotations

import json
import logging

log = logging.getLogger(__name__)
from dataclasses import dataclass, field

from ..clock import to_iso
from ..domain import MAX_PRIMARY, MAX_SEATS, MIN_SEATS
from ..errors import ConflictError, RegistrationInProgressError
from .prereq import missing_prerequisites, passed_courses
from .semesters import PHASE_IN_PROGRESS


@dataclass
class CloseReport:
    semester: str
    committed: list = field(default_factory=list)       # 第2步确认的开课
    cancelled: dict = field(default_factory=dict)       # 开课 -> 取消原因
    leveled: list = field(default_factory=list)         # (学生, 补入的开课)
    closed: list = field(default_factory=list)          # 最终开出的开课
    bills: int = 0
    billing_sent: int = 0
    billing_pending: int = 0


class ClosingService:
    def __init__(self, db, clock, semesters, offerings, billing, tuition_per_credit: int):
        self.db = db
        self.clock = clock
        self.semesters = semesters
        self.offerings = offerings
        self.billing = billing
        self.tuition_per_credit = tuition_per_credit

    def close_registration(self) -> CloseReport:
        sem = self.semesters.current()
        if sem is None:
            raise ConflictError("当前没有开放中的学期")
        if self.semesters.phase(sem) == PHASE_IN_PROGRESS:
            raise RegistrationInProgressError(
                f"{sem.name}的加退选期尚未结束（截止 {to_iso(sem.add_drop_end)}），不能关闭注册")
        snapshot = self.offerings.catalog.snapshot(sem.code)   # 不可用则直接失败，状态不变

        def lbl(oid):
            off = snapshot.offerings.get(oid)
            return f"{off.course_id}-{off.section}" if off else str(oid)

        report = CloseReport(sem.code)
        now = to_iso(self.clock.now())
        with self.db.transaction() as conn:
            # 事务内复核，防止两名注册员同时关闭
            sem = self.semesters.current(conn)
            if sem is None or sem.code != report.semester:
                raise ConflictError("该学期注册已被关闭")

            states = {r["offering_id"]: dict(r) for r in conn.execute(
                "SELECT * FROM offering_state WHERE semester = ?", (sem.code,))}

            def count(oid):
                return conn.execute("SELECT COUNT(*) AS n FROM schedule_item WHERE offering_id = ? "
                                    "AND status IN ('ENROLLED', 'COMMITTED')", (oid,)).fetchone()["n"]

            def cancel(oid, reason):
                states[oid]["status"] = "CANCELLED"
                report.cancelled[lbl(oid)] = reason
                conn.execute("UPDATE offering_state SET status = 'CANCELLED', cancel_reason = ? WHERE offering_id = ?",
                             (reason, oid))
                conn.execute("UPDATE schedule_item SET status = 'CANCELLED', note = ? WHERE offering_id = ? "
                             "AND status IN ('ENROLLED', 'COMMITTED')", (f"开课取消：{reason}", oid))

            # 草稿课表中的条目、已提交课表中未注册的条目都不再有效
            conn.execute(
                "UPDATE schedule_item SET status = 'CANCELLED', note = '课表未提交' WHERE status = 'SELECTED' "
                "AND schedule_id IN (SELECT id FROM schedule WHERE semester = ? AND status = 'DRAFT')", (sem.code,))

            # 保留有人员记录的目录完整性；源目录删除开课时拒绝关闭，避免悄悄丢课。
            if any(oid not in snapshot.offerings for oid in states):
                raise ConflictError("本地开课与课程目录不一致，请核对目录后重试")
            # ---- 第2步：确认 / 无教授取消 ----
            for oid in sorted(states):
                if states[oid]["professor_id"] is None:
                    cancel(oid, "无教授授课")
                elif count(oid) >= MIN_SEATS:
                    states[oid]["status"] = "COMMITTED"
                    report.committed.append(lbl(oid))
                    conn.execute("UPDATE offering_state SET status = 'COMMITTED' WHERE offering_id = ?", (oid,))
                    conn.execute("UPDATE schedule_item SET status = 'COMMITTED' WHERE offering_id = ? "
                                 "AND status = 'ENROLLED'", (oid,))

            # ---- 第3步：平衡课表（先提交者优先） ----
            schedules = conn.execute(
                "SELECT * FROM schedule WHERE semester = ? AND status = 'SUBMITTED' ORDER BY updated_at, id",
                (sem.code,)).fetchall()
            for sc in schedules:
                self._level(conn, sc, sem, snapshot, states, count, report, lbl)

            # ---- 第4步：关闭全部开课 ----
            for oid in sorted(states):
                if states[oid]["status"] == "CANCELLED":
                    continue
                if count(oid) >= MIN_SEATS:
                    report.closed.append(lbl(oid))
                    conn.execute("UPDATE offering_state SET status = 'CLOSED' WHERE offering_id = ?", (oid,))
                    conn.execute("UPDATE schedule_item SET status = 'COMMITTED' WHERE offering_id = ? "
                                 "AND status = 'ENROLLED'", (oid,))
                else:
                    cancel(oid, f"选课人数不足 {MIN_SEATS} 人")
            # 剩余未生效的条目（未注册的首选、未被补入的备选）统一标记
            conn.execute(
                "UPDATE schedule_item SET status = 'CANCELLED', note = COALESCE(note, '未被录取') "
                "WHERE status = 'SELECTED' AND schedule_id IN (SELECT id FROM schedule WHERE semester = ?)",
                (sem.code,))

            # ---- 第5步：计算学费，写入发件箱 ----
            report.bills = self._create_bills(conn, sem, snapshot, now)

            conn.execute("UPDATE semester SET status = 'CLOSED', closed_at = ? WHERE code = ?", (now, sem.code))

        # 事务已提交：尝试立即发送账单，失败的留待后台重发
        try:
            sent, pending = self.billing.dispatch_due()
        except Exception:
            # 业务事务已经提交；投递故障由持久化发件箱和后台线程恢复。
            log.exception("注册已关闭，立即投递失败，账单保留待重试")
            sent, pending = 0, report.bills
        report.billing_sent, report.billing_pending = sent, pending
        return report

    def _level(self, conn, sc, sem, snapshot, states, count, report, lbl):
        items = conn.execute("SELECT * FROM schedule_item WHERE schedule_id = ? ORDER BY kind DESC, priority",
                             (sc["id"],)).fetchall()
        active = [it["offering_id"] for it in items if it["status"] in ("ENROLLED", "COMMITTED")]
        missing_slots = MAX_PRIMARY - len(active)
        if missing_slots <= 0:
            return
        passed = passed_courses(conn, sc["student_id"], sem.seq)
        for it in items:
            if missing_slots <= 0:
                break
            if it["kind"] != "ALTERNATE" or it["status"] != "SELECTED":
                continue
            oid = it["offering_id"]
            off = snapshot.get_offering(oid)
            state = states.get(oid)
            if off is None or state is None or state["status"] == "CANCELLED":
                continue
            if count(oid) >= MAX_SEATS:
                continue
            if missing_prerequisites(snapshot.courses[off.course_id], passed):
                continue
            taken = [snapshot.offerings[a] for a in active if a in snapshot.offerings]
            if any(t.course_id == off.course_id or t.slot.overlaps(off.slot) for t in taken):
                continue
            new_status = "COMMITTED" if state["status"] == "COMMITTED" else "ENROLLED"
            conn.execute("UPDATE schedule_item SET status = ?, note = '关闭注册时由备选补入' WHERE id = ?",
                         (new_status, it["id"]))
            active.append(oid)
            missing_slots -= 1
            report.leveled.append((sc["student_id"], lbl(oid)))

    def _create_bills(self, conn, sem, snapshot, now) -> int:
        rows = conn.execute(
            "SELECT sc.student_id, si.offering_id FROM schedule_item si JOIN schedule sc ON sc.id = si.schedule_id "
            "WHERE sc.semester = ? AND si.status = 'COMMITTED' ORDER BY sc.student_id, si.offering_id",
            (sem.code,)).fetchall()
        per_student: dict = {}
        for r in rows:
            per_student.setdefault(r["student_id"], []).append(r["offering_id"])
        for sid, oids in per_student.items():
            courses = []
            for oid in oids:
                off = snapshot.offerings[oid]
                c = snapshot.courses[off.course_id]
                courses.append({"offering_id": oid, "course_id": c.course_id, "section": off.section,
                                "title": c.title, "credits": c.credits, "time": str(off.slot)})
            amount = sum(c["credits"] for c in courses) * self.tuition_per_credit
            payload = json.dumps({"student_id": sid, "semester": sem.code, "amount": amount,
                                  "final_schedule": courses}, ensure_ascii=False)
            conn.execute(
                "INSERT INTO billing_outbox(idempotency_key, student_id, semester, amount, payload, status, "
                "attempts, next_attempt_at, created_at) VALUES (?, ?, ?, ?, ?, 'PENDING', 0, ?, ?)",
                (f"{sem.code}:{sid}", sid, sem.code, amount, payload, now, now))
        return len(per_student)
