"""Maintain Student Information / Maintain Professor Information 用例。

- 新增时系统生成各自表内递增的唯一编号，并同时开通 s编号 / p编号 登录账号；
- 初始密码默认为 8 位出生日期（如 20050312），首次登录后可修改；
- 删除有历史记录（成绩、已关闭学期的课表或授课）的人员会破坏历史数据完整性，
  因此拒绝删除并提示改为"已毕业/退休"状态——这是对用例"删除"的鲁棒性补充，已在需求文档中说明。

负责人：成员B    使用者：web/registrar.py（成员B）
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import date

from ..domain import DEPARTMENTS, PROFESSOR_STATUS, SSN_PATTERN, STUDENT_STATUS
from ..errors import ConflictError, NotFoundError, ValidationError


@dataclass(frozen=True)
class Student:
    id: int
    name: str
    birth_date: str
    ssn: str
    status: str
    graduation_date: str | None
    username: str | None = None


@dataclass(frozen=True)
class Professor:
    id: int
    name: str
    birth_date: str
    ssn: str
    status: str
    department: str
    username: str | None = None


def _clean(form: dict, key: str) -> str:
    value = form.get(key)
    return value.strip() if isinstance(value, str) else ""


def _check_date(errors: dict, field: str, text: str, label: str, required=True) -> date | None:
    if not text:
        if required:
            errors[field] = f"{label}不能为空"
        return None
    try:
        return date.fromisoformat(text)
    except ValueError:
        errors[field] = f"{label}格式应为 YYYY-MM-DD"
        return None


def _validate_common(form: dict, today: date, errors: dict, status_choices: dict) -> dict:
    data = {
        "name": _clean(form, "name"),
        "birth_date": _clean(form, "birth_date"),
        "ssn": _clean(form, "ssn"),
        "status": _clean(form, "status").upper(),
    }
    if not data["name"]:
        errors["name"] = "姓名不能为空"
    elif len(data["name"]) > 50:
        errors["name"] = "姓名不能超过 50 个字符"
    birth = _check_date(errors, "birth_date", data["birth_date"], "出生日期")
    if birth and not (date(today.year - 100, 1, 1) <= birth <= date(today.year - 10, 12, 31)):
        errors["birth_date"] = "出生日期不在合理范围内"
    if not SSN_PATTERN.match(data["ssn"]):
        errors["ssn"] = "社会保障号格式应为 123-45-6789"
    if data["status"] not in status_choices:
        errors["status"] = "请选择有效的状态"
    data["_birth"] = birth
    return data


class PeopleService:
    def __init__(self, db, clock, auth):
        self.db = db
        self.clock = clock
        self.auth = auth

    # ================= 学生 =================
    def validate_student(self, form: dict) -> dict:
        errors: dict = {}
        data = _validate_common(form, self.clock.now().date(), errors, STUDENT_STATUS)
        data["graduation_date"] = _clean(form, "graduation_date") or None
        grad = _check_date(errors, "graduation_date", data["graduation_date"] or "", "毕业日期", required=False)
        if grad and data["_birth"] and grad <= data["_birth"]:
            errors["graduation_date"] = "毕业日期必须晚于出生日期"
        if errors:
            raise ValidationError(errors)
        data.pop("_birth")
        return data

    def add_student(self, form: dict, initial_password: str | None = None) -> tuple[Student, str]:
        """返回 (学生, 初始密码)。"""
        data = self.validate_student(form)
        password = initial_password or data["birth_date"].replace("-", "")
        try:
            with self.db.transaction() as conn:
                cur = conn.execute(
                    "INSERT INTO student(name, birth_date, ssn, status, graduation_date) VALUES (?, ?, ?, ?, ?)",
                    (data["name"], data["birth_date"], data["ssn"], data["status"], data["graduation_date"]),
                )
                sid = cur.lastrowid
                conn.execute(
                    "INSERT INTO user_account(username, password_hash, role, student_id, display_name) "
                    "VALUES (?, ?, 'student', ?, ?)",
                    (f"s{sid}", self.auth.hash_password(password), sid, data["name"]),
                )
        except sqlite3.IntegrityError as exc:
            raise ConflictError("该社会保障号已登记，不能重复添加") from exc
        return self.get_student(sid), password

    def get_student(self, student_id) -> Student:
        sid = _parse_id(student_id, "学生编号")
        with self.db.reader() as conn:
            row = conn.execute(
                "SELECT s.*, a.username FROM student s LEFT JOIN user_account a ON a.student_id = s.id WHERE s.id = ?",
                (sid,),
            ).fetchone()
        if not row:
            raise NotFoundError(f"编号为 {sid} 的学生不存在")
        return Student(row["id"], row["name"], row["birth_date"], row["ssn"], row["status"],
                       row["graduation_date"], row["username"])

    def list_students(self, keyword: str = "") -> list[Student]:
        kw = f"%{(keyword or '').strip()}%"
        with self.db.reader() as conn:
            rows = conn.execute(
                "SELECT s.*, a.username FROM student s LEFT JOIN user_account a ON a.student_id = s.id "
                "WHERE s.name LIKE ? OR CAST(s.id AS TEXT) LIKE ? ORDER BY s.id",
                (kw, kw),
            ).fetchall()
        return [Student(r["id"], r["name"], r["birth_date"], r["ssn"], r["status"], r["graduation_date"],
                        r["username"]) for r in rows]

    def update_student(self, student_id, form: dict) -> Student:
        sid = self.get_student(student_id).id
        data = self.validate_student(form)
        try:
            with self.db.transaction() as conn:
                conn.execute(
                    "UPDATE student SET name = ?, birth_date = ?, ssn = ?, status = ?, graduation_date = ? WHERE id = ?",
                    (data["name"], data["birth_date"], data["ssn"], data["status"], data["graduation_date"], sid),
                )
                conn.execute("UPDATE user_account SET display_name = ? WHERE student_id = ?", (data["name"], sid))
        except sqlite3.IntegrityError as exc:
            raise ConflictError("该社会保障号已被其他学生使用") from exc
        return self.get_student(sid)

    def delete_student(self, student_id) -> None:
        sid = self.get_student(student_id).id
        with self.db.transaction() as conn:
            has_history = conn.execute(
                "SELECT 1 FROM grade WHERE student_id = ? "
                "UNION SELECT 1 FROM schedule sc JOIN semester se ON se.code = sc.semester "
                "WHERE sc.student_id = ? AND se.status = 'CLOSED' LIMIT 1",
                (sid, sid),
            ).fetchone()
            if has_history:
                raise ConflictError("该学生已有历史选课或成绩记录，不能删除；如已离校，请将状态改为“已毕业”")
            # 本学期课表随学生级联删除，所占名额同时释放
            conn.execute("DELETE FROM student WHERE id = ?", (sid,))

    # ================= 教授 =================
    def validate_professor(self, form: dict) -> dict:
        errors: dict = {}
        data = _validate_common(form, self.clock.now().date(), errors, PROFESSOR_STATUS)
        data["department"] = _clean(form, "department").upper()
        if data["department"] not in DEPARTMENTS:
            errors["department"] = "请选择有效的院系"
        if errors:
            raise ValidationError(errors)
        data.pop("_birth")
        return data

    def add_professor(self, form: dict, initial_password: str | None = None) -> tuple[Professor, str]:
        data = self.validate_professor(form)
        password = initial_password or data["birth_date"].replace("-", "")
        try:
            with self.db.transaction() as conn:
                cur = conn.execute(
                    "INSERT INTO professor(name, birth_date, ssn, status, department) VALUES (?, ?, ?, ?, ?)",
                    (data["name"], data["birth_date"], data["ssn"], data["status"], data["department"]),
                )
                pid = cur.lastrowid
                conn.execute(
                    "INSERT INTO user_account(username, password_hash, role, professor_id, display_name) "
                    "VALUES (?, ?, 'professor', ?, ?)",
                    (f"p{pid}", self.auth.hash_password(password), pid, data["name"]),
                )
        except sqlite3.IntegrityError as exc:
            raise ConflictError("该社会保障号已登记，不能重复添加") from exc
        return self.get_professor(pid), password

    def get_professor(self, professor_id) -> Professor:
        pid = _parse_id(professor_id, "教授编号")
        with self.db.reader() as conn:
            row = conn.execute(
                "SELECT p.*, a.username FROM professor p LEFT JOIN user_account a ON a.professor_id = p.id "
                "WHERE p.id = ?",
                (pid,),
            ).fetchone()
        if not row:
            raise NotFoundError(f"编号为 {pid} 的教授不存在")
        return Professor(row["id"], row["name"], row["birth_date"], row["ssn"], row["status"],
                         row["department"], row["username"])

    def list_professors(self, keyword: str = "") -> list[Professor]:
        kw = f"%{(keyword or '').strip()}%"
        with self.db.reader() as conn:
            rows = conn.execute(
                "SELECT p.*, a.username FROM professor p LEFT JOIN user_account a ON a.professor_id = p.id "
                "WHERE p.name LIKE ? OR CAST(p.id AS TEXT) LIKE ? ORDER BY p.id",
                (kw, kw),
            ).fetchall()
        return [Professor(r["id"], r["name"], r["birth_date"], r["ssn"], r["status"], r["department"],
                          r["username"]) for r in rows]

    def update_professor(self, professor_id, form: dict) -> Professor:
        pid = self.get_professor(professor_id).id
        data = self.validate_professor(form)
        try:
            with self.db.transaction() as conn:
                conn.execute(
                    "UPDATE professor SET name = ?, birth_date = ?, ssn = ?, status = ?, department = ? WHERE id = ?",
                    (data["name"], data["birth_date"], data["ssn"], data["status"], data["department"], pid),
                )
                conn.execute("UPDATE user_account SET display_name = ? WHERE professor_id = ?", (data["name"], pid))
        except sqlite3.IntegrityError as exc:
            raise ConflictError("该社会保障号已被其他教授使用") from exc
        return self.get_professor(pid)

    def delete_professor(self, professor_id) -> None:
        pid = self.get_professor(professor_id).id
        with self.db.transaction() as conn:
            has_history = conn.execute(
                "SELECT 1 FROM grade WHERE recorded_by = ? "
                "UNION SELECT 1 FROM offering_state os JOIN semester se ON se.code = os.semester "
                "WHERE os.professor_id = ? AND se.status = 'CLOSED' LIMIT 1",
                (pid, pid),
            ).fetchone()
            if has_history:
                raise ConflictError("该教授已有历史授课或成绩记录，不能删除；如已离职，请将状态改为“退休”")
            # 本学期已认领的开课释放为"待定教师"，关闭注册时若仍无人认领将被取消
            conn.execute("UPDATE offering_state SET professor_id = NULL WHERE professor_id = ?", (pid,))
            conn.execute("DELETE FROM professor WHERE id = ?", (pid,))


def _parse_id(value, label: str) -> int:
    try:
        num = int(str(value).strip())
    except (TypeError, ValueError):
        raise NotFoundError(f"{label}应为数字")
    if num <= 0:
        raise NotFoundError(f"{label}应为正整数")
    return num
