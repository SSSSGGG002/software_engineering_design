"""Login 用例。

- 用户名 + 密码校验，密码只保存加盐哈希；
- 连续失败 LOGIN_MAX_FAILURES 次后临时锁定，抵御暴力猜测；
- 错误提示不区分"用户不存在"与"密码错误"，避免枚举账号；
- verify_password 供"查看成绩单前再次确认密码"使用（补充规约：成绩属于敏感信息）。

负责人：成员A    使用者：web/auth.py、web/student.py（成员C 的成绩单二次确认）
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta

from werkzeug.security import check_password_hash, generate_password_hash

from ..clock import from_iso, to_iso
from ..errors import AuthenticationError, NotFoundError, ValidationError

# 用于用户不存在时也做一次哈希比较，使响应时间一致
_DUMMY_HASH = generate_password_hash("dummy-password", method="pbkdf2:sha256:1000")


@dataclass(frozen=True)
class Account:
    id: int
    username: str
    role: str
    display_name: str
    student_id: int | None
    professor_id: int | None

    @classmethod
    def from_row(cls, row) -> "Account":
        return cls(row["id"], row["username"], row["role"], row["display_name"],
                   row["student_id"], row["professor_id"])


class AuthService:
    def __init__(self, db, clock, hash_method: str, max_failures: int, lock_minutes: int):
        self.db = db
        self.clock = clock
        self.hash_method = hash_method
        self.max_failures = max_failures
        self.lock_minutes = lock_minutes

    def hash_password(self, password: str) -> str:
        return generate_password_hash(password, method=self.hash_method)

    def authenticate(self, username: str, password: str) -> Account:
        username = (username or "").strip()
        if not username or not password:
            raise AuthenticationError("请输入用户名和密码")
        now = self.clock.now()
        with self.db.transaction() as conn:
            row = conn.execute("SELECT * FROM user_account WHERE username = ?", (username,)).fetchone()
            if row is None:
                check_password_hash(_DUMMY_HASH, password)
                raise AuthenticationError("用户名或密码错误")
            if row["locked_until"] and from_iso(row["locked_until"]) > now:
                raise AuthenticationError(f"登录失败次数过多，账号已临时锁定至 {row['locked_until']}")
            if not check_password_hash(row["password_hash"], password):
                failures = row["failed_attempts"] + 1
                locked_until = None
                if failures >= self.max_failures:
                    locked_until = to_iso(now + timedelta(minutes=self.lock_minutes))
                    failures = 0
                conn.execute("UPDATE user_account SET failed_attempts = ?, locked_until = ? WHERE id = ?",
                             (failures, locked_until, row["id"]))
                # 失败计数需要持久化，因此提交事务后再抛出异常
                error = AuthenticationError(
                    f"登录失败次数过多，账号已临时锁定 {self.lock_minutes} 分钟" if locked_until else "用户名或密码错误"
                )
            else:
                conn.execute("UPDATE user_account SET failed_attempts = 0, locked_until = NULL WHERE id = ?",
                             (row["id"],))
                return Account.from_row(row)
        raise error

    def verify_password(self, account_id: int, password: str) -> bool:
        with self.db.reader() as conn:
            row = conn.execute("SELECT password_hash FROM user_account WHERE id = ?", (account_id,)).fetchone()
        return bool(row) and check_password_hash(row["password_hash"], password or "")

    def get_account(self, account_id: int) -> Account:
        with self.db.reader() as conn:
            row = conn.execute("SELECT * FROM user_account WHERE id = ?", (account_id,)).fetchone()
        if not row:
            raise NotFoundError("账号不存在")
        return Account.from_row(row)

    def change_password(self, account_id: int, old: str, new: str, confirm: str) -> None:
        errors = {}
        if not self.verify_password(account_id, old):
            errors["old"] = "原密码不正确"
        if len(new or "") < 6:
            errors["new"] = "新密码至少 6 位"
        elif new != confirm:
            errors["confirm"] = "两次输入的新密码不一致"
        if errors:
            raise ValidationError(errors)
        with self.db.transaction() as conn:
            conn.execute("UPDATE user_account SET password_hash = ? WHERE id = ?", (self.hash_password(new), account_id))

    def create_registrar(self, username: str, password: str, display_name: str = "注册员") -> None:
        with self.db.transaction() as conn:
            conn.execute(
                "INSERT INTO user_account(username, password_hash, role, display_name) VALUES (?, ?, 'registrar', ?)",
                (username, self.hash_password(password), display_name),
            )
