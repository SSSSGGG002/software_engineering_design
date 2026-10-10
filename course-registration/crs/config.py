"""配置。所有可调参数集中于此，便于配置管理（见《软件配置管理计划》）。

负责人：成员A
"""
import os


class Config:
    SECRET_KEY = os.environ.get("CRS_SECRET_KEY", "dev-only-please-change")
    # 数据文件位置；为 None 时放在 Flask instance 目录下
    DATABASE = None
    CATALOG_DATABASE = None          # 模拟的遗留课程目录库（Ingres/VAX）
    BILLING_DATABASE = None          # 模拟的外部计费系统

    # ---- 遗留课程目录访问（成员A） ----
    CATALOG_LATENCY_SECONDS = 0.8    # 模拟遗留库的慢速访问
    CATALOG_CACHE_TTL_SECONDS = 300  # 中间层缓存有效期

    # ---- 计费（成员E） ----
    TUITION_PER_CREDIT = 600         # 每学分学费（元）
    BILLING_RETRY_BASE_SECONDS = 10  # 重试退避基数：10s、20s、40s……
    BILLING_RETRY_MAX_SECONDS = 300  # 退避上限
    BILLING_WORKER_ENABLED = True    # 后台重发线程
    BILLING_WORKER_INTERVAL_SECONDS = 5

    # ---- 登录与会话（成员A） ----
    PASSWORD_HASH_METHOD = "pbkdf2:sha256:200000"
    LOGIN_MAX_FAILURES = 5
    LOGIN_LOCK_MINUTES = 5
    SESSION_MINUTES = 30
    RECENT_AUTH_MINUTES = 5          # 查看成绩单前需在 5 分钟内确认过密码

    TESTING = False


class TestConfig(Config):
    TESTING = True
    SECRET_KEY = "test"
    CATALOG_LATENCY_SECONDS = 0
    BILLING_WORKER_ENABLED = False
    PASSWORD_HASH_METHOD = "pbkdf2:sha256:1000"   # 测试中加快哈希速度
