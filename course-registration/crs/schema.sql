-- 课程注册系统自有数据库（不包含课程目录，课程目录在遗留系统中，只读访问）
-- 负责人：成员A（汇总）；各表注释中标出主要使用者

-- 学生（成员B 维护）
CREATE TABLE IF NOT EXISTS student (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    name            TEXT    NOT NULL,
    birth_date      TEXT    NOT NULL,
    ssn             TEXT    NOT NULL UNIQUE,
    status          TEXT    NOT NULL CHECK (status IN ('ENROLLED', 'SUSPENDED', 'GRADUATED')),
    graduation_date TEXT
);

-- 教授（成员B 维护）
CREATE TABLE IF NOT EXISTS professor (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT    NOT NULL,
    birth_date  TEXT    NOT NULL,
    ssn         TEXT    NOT NULL UNIQUE,
    status      TEXT    NOT NULL CHECK (status IN ('ACTIVE', 'VISITING', 'RETIRED')),
    department  TEXT    NOT NULL
);

-- 登录账号（成员A：Login 用例）。删除学生/教授时级联删除账号
CREATE TABLE IF NOT EXISTS user_account (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    username      TEXT    NOT NULL UNIQUE,
    password_hash TEXT    NOT NULL,
    role          TEXT    NOT NULL CHECK (role IN ('student', 'professor', 'registrar')),
    student_id    INTEGER REFERENCES student(id) ON DELETE CASCADE,
    professor_id  INTEGER REFERENCES professor(id) ON DELETE CASCADE,
    display_name  TEXT    NOT NULL,
    failed_attempts INTEGER NOT NULL DEFAULT 0,  -- 连续登录失败次数
    locked_until  TEXT                           -- 临时锁定截止时间
);

-- 学期（成员E：注册周期管理）
CREATE TABLE IF NOT EXISTS semester (
    code          TEXT    PRIMARY KEY,           -- 如 2026F
    name          TEXT    NOT NULL,
    seq           INTEGER NOT NULL UNIQUE,       -- 学期先后顺序
    status        TEXT    NOT NULL CHECK (status IN ('PLANNED', 'OPEN', 'CLOSED')),
    add_drop_end  TEXT,                          -- 加退选截止时间（开放时设置）
    closed_at     TEXT
);
-- 任一时刻至多一个开放学期
CREATE UNIQUE INDEX IF NOT EXISTS uq_semester_open ON semester(status) WHERE status = 'OPEN';

-- 开课的本地状态（遗留目录库只读，因此教师分配与开课状态保存在本系统）
-- 成员D 写 professor_id；成员E 写 status；成员C 读
CREATE TABLE IF NOT EXISTS offering_state (
    offering_id   INTEGER PRIMARY KEY,
    semester      TEXT    NOT NULL REFERENCES semester(code),
    course_id     TEXT    NOT NULL,              -- 遗留目录课程号快照，供先修课/成绩使用
    professor_id  INTEGER REFERENCES professor(id),
    status        TEXT    NOT NULL DEFAULT 'OPEN'
                  CHECK (status IN ('OPEN', 'COMMITTED', 'CLOSED', 'CANCELLED')),
    cancel_reason TEXT
);
CREATE INDEX IF NOT EXISTS idx_offering_state_sem ON offering_state(semester);
CREATE INDEX IF NOT EXISTS idx_offering_state_prof ON offering_state(professor_id);

-- 学生课表（成员C）。每名学生每学期至多一张课表
CREATE TABLE IF NOT EXISTS schedule (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    student_id  INTEGER NOT NULL REFERENCES student(id) ON DELETE CASCADE,
    semester    TEXT    NOT NULL REFERENCES semester(code),
    status      TEXT    NOT NULL CHECK (status IN ('DRAFT', 'SUBMITTED')),
    version     INTEGER NOT NULL DEFAULT 1,      -- 乐观锁：防止两个窗口同时修改同一课表
    created_at  TEXT    NOT NULL,
    updated_at  TEXT    NOT NULL,
    UNIQUE (student_id, semester)
);

-- 课表条目（成员C 写入；成员E 关闭注册时更新；成员D 生成花名册）
CREATE TABLE IF NOT EXISTS schedule_item (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    schedule_id  INTEGER NOT NULL REFERENCES schedule(id) ON DELETE CASCADE,
    offering_id  INTEGER NOT NULL REFERENCES offering_state(offering_id),
    kind         TEXT    NOT NULL CHECK (kind IN ('PRIMARY', 'ALTERNATE')),
    priority     INTEGER NOT NULL,
    status       TEXT    NOT NULL CHECK (status IN ('SELECTED', 'ENROLLED', 'COMMITTED', 'CANCELLED')),
    note         TEXT,
    UNIQUE (schedule_id, offering_id)
);
CREATE INDEX IF NOT EXISTS idx_item_offering ON schedule_item(offering_id, status);

-- 成绩（成员D）。course_id 为遗留目录中课程号的快照，先修课检查时无需再访问慢速遗留库
CREATE TABLE IF NOT EXISTS grade (
    student_id   INTEGER NOT NULL REFERENCES student(id),
    offering_id  INTEGER NOT NULL REFERENCES offering_state(offering_id),
    semester     TEXT    NOT NULL,
    course_id    TEXT    NOT NULL,
    grade        TEXT    NOT NULL CHECK (grade IN ('A', 'B', 'C', 'D', 'F', 'I')),
    recorded_by  INTEGER NOT NULL REFERENCES professor(id),
    updated_at   TEXT    NOT NULL,
    PRIMARY KEY (student_id, offering_id)
);

-- 计费发件箱（成员E）：与关闭注册同一事务写入，提交后异步发送并重试（事务性发件箱模式）
CREATE TABLE IF NOT EXISTS billing_outbox (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    idempotency_key  TEXT    NOT NULL UNIQUE,     -- 学期:学号，防止重复计费
    student_id       INTEGER NOT NULL,
    semester         TEXT    NOT NULL,
    amount           INTEGER NOT NULL,            -- 学费（元）
    payload          TEXT    NOT NULL,            -- JSON：含最终课表
    status           TEXT    NOT NULL CHECK (status IN ('PENDING', 'SENT')),
    attempts         INTEGER NOT NULL DEFAULT 0,
    last_error       TEXT,
    next_attempt_at  TEXT    NOT NULL,
    created_at       TEXT    NOT NULL,
    sent_at          TEXT
);

-- 系统设置与演示开关（模拟外部系统可用性）
CREATE TABLE IF NOT EXISTS system_setting (
    key    TEXT PRIMARY KEY,
    value  TEXT NOT NULL
);
