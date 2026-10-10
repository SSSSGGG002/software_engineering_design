"""外部计费系统（Billing System）的模拟实现。

真实系统中这里是网络调用；本项目用独立的 SQLite 文件模拟对端，
并按幂等键去重——即使注册系统因超时而重发，学生也不会被重复计费。

负责人：成员E    使用者：services/billing.py（成员E）
"""
from __future__ import annotations

import sqlite3

from ..errors import BillingUnavailableError


class SimulatedBillingSystem:
    def __init__(self, path: str, availability=lambda: True):
        self.path = path
        self.availability = availability
        conn = sqlite3.connect(self.path)
        try:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS invoice ("
                " idempotency_key TEXT PRIMARY KEY, student_id INTEGER NOT NULL,"
                " semester TEXT NOT NULL, amount INTEGER NOT NULL, payload TEXT NOT NULL,"
                " received_at TEXT NOT NULL)"
            )
            conn.commit()
        finally:
            conn.close()

    def send(self, idempotency_key: str, student_id: int, semester: str, amount: int,
             payload: str, now: str) -> None:
        if not self.availability():
            raise BillingUnavailableError()
        try:
            conn = sqlite3.connect(self.path, timeout=10)
            try:
                # 幂等接收：重复的键被忽略
                conn.execute(
                    "INSERT OR IGNORE INTO invoice VALUES (?, ?, ?, ?, ?, ?)",
                    (idempotency_key, student_id, semester, amount, payload, now),
                )
                conn.commit()
            finally:
                conn.close()
        except sqlite3.Error as exc:
            raise BillingUnavailableError(f"计费系统写入失败: {exc}") from exc

    def invoices(self):
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        try:
            return [dict(r) for r in conn.execute("SELECT * FROM invoice ORDER BY idempotency_key")]
        finally:
            conn.close()
