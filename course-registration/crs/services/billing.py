"""计费发件箱投递（Close Registration 备选流：计费系统不可用时持续重发）。

- 关闭注册时账单与业务数据在同一事务写入 billing_outbox，不会出现"注册已关闭但账单丢失"；
- 投递在事务之外进行，失败则 attempts+1，按指数退避（10s, 20s, 40s … 上限 300s）安排下次重试；
- 计费系统按幂等键去重，重复投递不会重复计费；
- 后台线程 BillingWorker 周期调用 dispatch_due()，异常只记录日志，线程不会退出。

负责人：成员E
"""
from __future__ import annotations

import logging
import threading
from datetime import timedelta

from ..clock import to_iso
from ..errors import BillingUnavailableError

log = logging.getLogger(__name__)


class BillingService:
    def __init__(self, db, clock, gateway, retry_base_seconds: int, retry_max_seconds: int):
        self.db = db
        self.clock = clock
        self.gateway = gateway
        self.retry_base_seconds = retry_base_seconds
        self.retry_max_seconds = retry_max_seconds
        self._lock = threading.Lock()     # 同一进程内同一时刻只有一个投递者

    def backoff_seconds(self, attempts: int) -> int:
        return min(self.retry_base_seconds * (2 ** min(max(attempts - 1, 0), 20)), self.retry_max_seconds)

    def dispatch_due(self, force: bool = False) -> tuple[int, int]:
        """投递到期的待发账单。force=True 时忽略退避时间（注册员手动重试）。返回 (成功数, 剩余待发数)。"""
        with self._lock:
            now = self.clock.now()
            with self.db.reader() as conn:
                sql = "SELECT * FROM billing_outbox WHERE status = 'PENDING'"
                args = []
                if not force:
                    sql += " AND next_attempt_at <= ?"
                    args.append(to_iso(now))
                due = conn.execute(sql + " ORDER BY id", args).fetchall()
            sent = 0
            for row in due:
                try:
                    self.gateway.send(row["idempotency_key"], row["student_id"], row["semester"],
                                      row["amount"], row["payload"], to_iso(now))
                except BillingUnavailableError as exc:
                    attempts = row["attempts"] + 1
                    nxt = now + timedelta(seconds=self.backoff_seconds(attempts))
                    with self.db.transaction() as conn:
                        conn.execute("UPDATE billing_outbox SET attempts = ?, last_error = ?, next_attempt_at = ? "
                                     "WHERE id = ?", (attempts, exc.message, to_iso(nxt), row["id"]))
                    log.warning("账单 %s 投递失败（第 %d 次），%s 重试", row["idempotency_key"], attempts, to_iso(nxt))
                    # 对端不可用时，本轮剩余账单也不再尝试，等待下次
                    break
                with self.db.transaction() as conn:
                    conn.execute("UPDATE billing_outbox SET status = 'SENT', attempts = attempts + 1, "
                                 "last_error = NULL, sent_at = ? WHERE id = ?", (to_iso(now), row["id"]))
                sent += 1
            return sent, self.pending_count()

    def pending_count(self) -> int:
        with self.db.reader() as conn:
            return conn.execute("SELECT COUNT(*) AS n FROM billing_outbox WHERE status = 'PENDING'").fetchone()["n"]

    def list_outbox(self, semester: str | None = None):
        with self.db.reader() as conn:
            sql = ("SELECT b.*, s.name AS student_name FROM billing_outbox b "
                   "LEFT JOIN student s ON s.id = b.student_id")
            args = []
            if semester:
                sql += " WHERE b.semester = ?"
                args.append(semester)
            return [dict(r) for r in conn.execute(sql + " ORDER BY b.id", args)]


class BillingWorker(threading.Thread):
    def __init__(self, billing: BillingService, interval_seconds: float):
        super().__init__(name="billing-worker", daemon=True)
        self.billing = billing
        self.interval_seconds = interval_seconds
        self._stop_event = threading.Event()

    def run(self):
        log.info("计费重发线程已启动，间隔 %ss", self.interval_seconds)
        while not self._stop_event.wait(self.interval_seconds):
            try:
                self.billing.dispatch_due()
            except Exception:       # 后台线程不能因任何异常退出
                log.exception("计费重发线程本轮执行出错，将在下一轮继续")

    def stop(self):
        self._stop_event.set()
