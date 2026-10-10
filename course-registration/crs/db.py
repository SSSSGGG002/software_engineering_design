"""注册系统自有数据库的访问封装（SQLite）。

设计要点：
- 每次操作新建连接，线程安全，可在后台重试线程中使用；
- transaction() 使用 BEGIN IMMEDIATE：写事务串行化，保证"名额检查 + 写入"原子执行，
  从根本上避免并发超额注册；异常自动回滚，满足用例"失败时系统状态不变"的后置条件；
- busy_timeout 让并发写者排队等待而不是立即报错。

负责人：成员A（架构）
"""
from __future__ import annotations

import logging
import sqlite3
from contextlib import contextmanager
from importlib import resources

log = logging.getLogger(__name__)


class Database:
    def __init__(self, path: str, busy_timeout_ms: int = 10000):
        self.path = path
        self.busy_timeout_ms = busy_timeout_ms

    def connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=self.busy_timeout_ms / 1000, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute(f"PRAGMA busy_timeout = {int(self.busy_timeout_ms)}")
        return conn

    @contextmanager
    def transaction(self):
        """写事务：成功提交，任何异常回滚后原样抛出。"""
        conn = self.connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            try:
                yield conn
            except BaseException:
                conn.execute("ROLLBACK")
                raise
            conn.execute("COMMIT")
        finally:
            conn.close()

    @contextmanager
    def reader(self):
        """只读访问（自动提交模式下的单条查询各自一致）。"""
        conn = self.connect()
        try:
            yield conn
        finally:
            conn.close()

    @contextmanager
    def using(self, conn=None):
        """复用调用方已打开的连接（处于事务中时），否则临时打开一个只读连接。"""
        if conn is not None:
            yield conn
        else:
            with self.reader() as c:
                yield c

    def init_schema(self) -> None:
        sql = resources.files("crs").joinpath("schema.sql").read_text(encoding="utf-8")
        conn = self.connect()
        try:
            try:
                # WAL 允许读写并发；部分文件系统不支持时退回默认日志模式
                conn.execute("PRAGMA journal_mode = WAL")
            except sqlite3.DatabaseError:
                log.warning("WAL 模式不可用，使用默认日志模式")
            conn.executescript(sql)
        finally:
            conn.close()


def get_setting(conn, key: str, default: str | None = None) -> str | None:
    row = conn.execute("SELECT value FROM system_setting WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else default


def set_setting(conn, key: str, value: str) -> None:
    conn.execute(
        "INSERT INTO system_setting(key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, value),
    )
