"""演示数据与命令行工具。

- create_legacy_catalog：生成模拟的遗留课程目录库（相当于注册处另一套系统维护的数据）；
- seed_demo：**通过各成员的服务接口**（而非直接写表）回放一个完整学期的历史：
  开放 2026 春 → 教授选课 → 学生注册 → 关闭注册/计费 → 录入成绩，再开放 2026 秋。
  这本身就是一次端到端集成测试：任何模块的接口不匹配都会在初始化时暴露。

命令：flask --app crs init-demo    重建演示数据
      flask --app crs retry-billing 立即重发待发账单

负责人：成员A（集成）  数据由全员共同设计
"""
from __future__ import annotations

import os
import sqlite3
from datetime import datetime, timedelta

import click

from .clock import FakeClock, SystemClock, to_iso
from .db import set_setting

DEMO_PASSWORD = "123456"

# 课程号, 名称, 学分, 院系, 简介, 先修课
COURSES = [
    ("CS101", "程序设计基础", 3, "CS", "C 语言程序设计与基本算法思想", []),
    ("CS102", "数据结构", 4, "CS", "线性表、树、图及其算法", ["CS101"]),
    ("CS150", "计算机导论", 2, "CS", "计算机科学概览", []),
    ("CS201", "算法设计与分析", 3, "CS", "分治、动态规划、贪心与复杂度", ["CS102"]),
    ("CS210", "数据库系统", 3, "CS", "关系模型、SQL 与事务", ["CS102"]),
    ("CS220", "计算机网络", 3, "CS", "TCP/IP 协议栈", ["CS101"]),
    ("CS230", "操作系统", 4, "CS", "进程、内存与文件系统", ["CS102"]),
    ("CS301", "软件工程", 3, "CS", "软件过程、需求、设计与测试", ["CS210"]),
    ("MATH101", "高等数学", 4, "MATH", "极限、微分与积分", []),
    ("MATH102", "线性代数", 3, "MATH", "矩阵与线性空间", []),
    ("MATH201", "概率论与数理统计", 3, "MATH", "随机变量与统计推断", ["MATH101"]),
    ("MATH210", "离散数学", 3, "MATH", "集合、逻辑与图论", []),
]

# 开课编号, 课程号, 学期, 班号, 星期, 开始, 结束, 教室
OFFERINGS = [
    # ---- 2026 春（历史学期） ----
    (101, "CS101", "2026S", "01", "MW", "08:00", "09:40", "A101"),
    (102, "CS101", "2026S", "02", "TR", "10:00", "11:40", "A102"),
    (103, "CS150", "2026S", "01", "F", "14:00", "15:40", "A103"),
    (104, "MATH101", "2026S", "01", "TR", "08:00", "09:40", "B201"),
    (105, "MATH102", "2026S", "01", "MW", "10:00", "11:40", "B202"),
    (106, "MATH210", "2026S", "01", "MW", "14:00", "15:40", "B203"),
    # ---- 2026 秋（当前学期） ----
    (201, "CS101", "2026F", "01", "MW", "08:00", "09:40", "A101"),
    (202, "CS102", "2026F", "01", "MW", "10:00", "11:40", "A102"),
    (203, "CS102", "2026F", "02", "TR", "14:00", "15:40", "A102"),
    (204, "CS150", "2026F", "01", "F", "14:00", "15:40", "A103"),
    (205, "CS201", "2026F", "01", "TR", "08:00", "09:40", "A201"),
    (206, "CS210", "2026F", "01", "TR", "10:00", "11:40", "A202"),
    (207, "CS220", "2026F", "01", "MW", "14:00", "15:40", "A203"),
    (208, "CS230", "2026F", "01", "F", "08:00", "11:40", "A204"),
    (209, "CS301", "2026F", "01", "W", "16:00", "17:40", "A301"),
    (210, "MATH101", "2026F", "01", "TR", "08:00", "09:40", "B201"),
    (211, "MATH102", "2026F", "01", "MW", "10:00", "11:40", "B202"),
    (212, "MATH201", "2026F", "01", "TR", "10:00", "11:40", "B301"),
    (213, "MATH210", "2026F", "01", "MW", "16:00", "17:40", "B203"),
    # ---- 2027 春（下一学期，用于演示学期循环） ----
    (301, "CS102", "2027S", "01", "MW", "10:00", "11:40", "A102"),
    (302, "CS210", "2027S", "01", "TR", "10:00", "11:40", "A202"),
    (303, "MATH201", "2027S", "01", "TR", "14:00", "15:40", "B301"),
    (304, "CS220", "2027S", "01", "MW", "14:00", "15:40", "A203"),
]

SEMESTERS = [("2026S", "2026 年春季学期", 1), ("2026F", "2026 年秋季学期", 2), ("2027S", "2027 年春季学期", 3)]

PROFESSORS = [  # 姓名, 出生日期, 社保号, 状态, 院系
    ("王建国", "1972-05-14", "500-10-0001", "ACTIVE", "CS"),
    ("李敏", "1980-11-02", "500-10-0002", "ACTIVE", "CS"),
    ("张伟", "1985-03-21", "500-10-0003", "ACTIVE", "CS"),
    ("陈静", "1978-07-09", "500-10-0004", "ACTIVE", "MATH"),
    ("刘洋", "1983-01-30", "500-10-0005", "ACTIVE", "MATH"),
    ("赵磊", "1960-09-18", "500-10-0006", "RETIRED", "CS"),
]

STUDENT_NAMES = ["林晓", "周可", "吴昊", "郑雪", "孙哲", "何雨", "高远", "马丽", "罗杰", "梁晨",
                 "宋佳", "唐宁", "许诺", "韩冰", "冯涛", "曹颖", "彭博", "袁野", "邓超", "叶青"]


def create_legacy_catalog(path: str) -> None:
    """生成遗留课程目录库（覆盖已有文件）。"""
    if os.path.exists(path):
        os.remove(path)
    conn = sqlite3.connect(path)
    try:
        conn.executescript("""
            CREATE TABLE course (course_id TEXT PRIMARY KEY, title TEXT NOT NULL, credits INTEGER NOT NULL,
                                 department TEXT NOT NULL, description TEXT);
            CREATE TABLE prerequisite (course_id TEXT NOT NULL, prereq_id TEXT NOT NULL,
                                       PRIMARY KEY (course_id, prereq_id));
            CREATE TABLE course_offering (offering_id INTEGER PRIMARY KEY, course_id TEXT NOT NULL,
                                          semester TEXT NOT NULL, section TEXT NOT NULL, days TEXT NOT NULL,
                                          start_time TEXT NOT NULL, end_time TEXT NOT NULL, room TEXT);
        """)
        conn.executemany("INSERT INTO course VALUES (?, ?, ?, ?, ?)", [c[:5] for c in COURSES])
        conn.executemany("INSERT INTO prerequisite VALUES (?, ?)",
                         [(c[0], p) for c in COURSES for p in c[5]])
        conn.executemany("INSERT INTO course_offering VALUES (?, ?, ?, ?, ?, ?, ?, ?)", OFFERINGS)
        conn.commit()
    finally:
        conn.close()


def _remove_db(path: str) -> None:
    for suffix in ("", "-wal", "-shm"):
        if os.path.exists(path + suffix):
            os.remove(path + suffix)


def seed_demo(app, real_clock=None) -> dict:
    """重建全部演示数据，返回演示账号信息。"""
    from . import build_services

    cfg = app.config
    for key in ("DATABASE", "BILLING_DATABASE"):
        _remove_db(cfg[key])
    create_legacy_catalog(cfg["CATALOG_DATABASE"])

    history_clock = FakeClock(datetime(2026, 2, 20, 9, 0))
    s = build_services(app, history_clock)
    s.catalog_client.latency_seconds = 0          # 初始化时不模拟慢速

    with s.db.transaction() as conn:
        conn.executemany("INSERT INTO semester(code, name, seq, status) VALUES (?, ?, ?, 'PLANNED')", SEMESTERS)
        set_setting(conn, "catalog_available", "1")
        set_setting(conn, "billing_available", "1")
    s.auth.create_registrar("admin", "admin123", "教务处注册员")

    profs = []
    for name, birth, ssn, status, dept in PROFESSORS:
        p, _ = s.people.add_professor(dict(name=name, birth_date=birth, ssn=ssn, status=status, department=dept),
                                      DEMO_PASSWORD)
        profs.append(p.id)
    studs = []
    for i, name in enumerate(STUDENT_NAMES):
        st, _ = s.people.add_student(dict(name=name, birth_date=f"2005-{i % 12 + 1:02d}-{i % 27 + 1:02d}",
                                          ssn=f"600-20-{i + 1:04d}", status="ENROLLED",
                                          graduation_date="2029-07-01"), DEMO_PASSWORD)
        studs.append(st.id)
    wang, li, zhang, chen, liu, _zhao = profs

    # ================= 2026 春：完整走一遍学期 =================
    s.semesters.open_semester("2026S", "2026-03-06 18:00")
    s.teaching.select_courses_to_teach(wang, [101])
    s.teaching.select_courses_to_teach(li, [102])
    s.teaching.select_courses_to_teach(zhang, [103])
    s.teaching.select_courses_to_teach(chen, [104, 106])
    s.teaching.select_courses_to_teach(liu, [105])
    for idx, sid in enumerate(studs[:10]):
        primary = [101 if idx % 2 == 0 else 102, 104, 105]
        if idx < 2:
            primary.append(103)               # 计算机导论仅 2 人选 → 关闭时被取消
        alternates = [106]
        s.registration.save_schedule(sid, primary, alternates, None, submit=True)
    history_clock.set(datetime(2026, 3, 7, 9, 0))
    s.closing.close_registration()

    history_clock.set(datetime(2026, 7, 1, 10, 0))
    grade_cycle = ["A", "B", "C", "B", "A", "D", "B", "F", "A", "C", "B", "I"]
    for prof, oid in ((wang, 101), (li, 102), (chen, 104), (liu, 105), (chen, 106)):
        roster = s.teaching.roster(prof, oid)
        grades = {e.student_id: ("B" if oid == 104 else grade_cycle[(e.student_id + oid) % len(grade_cycle)])
                  for e in roster}
        s.teaching.submit_grades(prof, oid, grades)

    # ================= 2026 秋：当前学期，加退选进行中 =================
    now_clock = real_clock or SystemClock()
    s_now = build_services(app, now_clock)
    s_now.catalog_client.latency_seconds = 0
    add_drop_end = to_iso(now_clock.now() + timedelta(days=21))
    s_now.semesters.open_semester("2026F", add_drop_end)
    s_now.teaching.select_courses_to_teach(wang, [201, 205])
    s_now.teaching.select_courses_to_teach(li, [202, 206, 209])
    s_now.teaching.select_courses_to_teach(zhang, [203, 207])      # CS150、CS230 暂无教授
    s_now.teaching.select_courses_to_teach(chen, [210, 212])
    s_now.teaching.select_courses_to_teach(liu, [211, 213])
    # 9 名学生选了概率统计（212），只剩 1 个名额，用于演示"课程满员"
    for sid in studs[1:10]:
        s_now.registration.save_schedule(sid, [212, 211], [213], None, submit=True)
    # 新生选课
    for sid in studs[12:16]:
        s_now.registration.save_schedule(sid, [201, 210, 204], [213], None, submit=True)
    # 一名学生只保存了草稿
    s_now.registration.save_schedule(studs[16], [201, 210], [], None, submit=False)

    return {"registrar": ("admin", "admin123"), "password": DEMO_PASSWORD,
            "students": studs, "professors": profs, "add_drop_end": add_drop_end}


def register_cli(app) -> None:
    @app.cli.command("init-demo")
    @click.option("--yes", is_flag=True, help="确认重建已有演示数据（请先停止服务并备份）")
    def init_demo(yes):
        """重建数据库并写入演示数据。"""
        with app.extensions["crs"].db.reader() as conn:
            existing = conn.execute("SELECT COUNT(*) FROM user_account").fetchone()[0]
        if existing and not yes:
            click.confirm("已有数据。请确认已停止服务并完成备份，是否重建？", abort=True)
        info = seed_demo(app)
        app.extensions["crs"].catalog.invalidate()
        click.echo("演示数据已生成。")
        click.echo(f"  注册员: admin / admin123")
        click.echo(f"  学生:   s{info['students'][0]} … s{info['students'][-1]}，密码 {info['password']}")
        click.echo(f"  教授:   p{info['professors'][0]} … p{info['professors'][-1]}，密码 {info['password']}")
        click.echo(f"  2026 秋加退选截止: {info['add_drop_end']}")

    @app.cli.command("retry-billing")
    def retry_billing():
        """立即重发所有待发账单。"""
        sent, pending = app.extensions["crs"].billing.dispatch_due(force=True)
        click.echo(f"本次发送 {sent} 张，剩余待发 {pending} 张")
