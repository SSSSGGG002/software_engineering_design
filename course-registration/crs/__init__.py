"""Wylie College 课程注册系统（Course Registration System）。

应用工厂：组装各成员的服务对象（依赖注入），注册 Web 蓝图。
所有服务挂在 app.extensions["crs"] 上，测试时可替换时钟与外部系统。

负责人：成员A（架构 / 集成）
"""
from __future__ import annotations

import logging
import os
from datetime import timedelta
from types import SimpleNamespace

from flask import Flask

from .clock import SystemClock
from .config import Config
from .db import Database, get_setting

__version__ = "1.0.0"


def build_services(app: Flask, clock) -> SimpleNamespace:
    from .integration.billing import SimulatedBillingSystem
    from .integration.catalog import CatalogService, LegacyCatalogClient
    from .services.auth import AuthService
    from .services.billing import BillingService
    from .services.closing import ClosingService
    from .services.offerings import OfferingQueries
    from .services.people import PeopleService
    from .services.registration import RegistrationService
    from .services.semesters import SemesterService
    from .services.teaching import TeachingService

    cfg = app.config
    db = Database(cfg["DATABASE"])
    db.init_schema()

    # 外部系统可用性开关（演示"遗留库/计费系统不可用"的备选流）
    def available(key):
        def check():
            with db.reader() as conn:
                return get_setting(conn, key, "1") == "1"
        return check

    catalog_client = LegacyCatalogClient(cfg["CATALOG_DATABASE"], cfg["CATALOG_LATENCY_SECONDS"],
                                         available("catalog_available"))
    catalog = CatalogService(catalog_client, cfg["CATALOG_CACHE_TTL_SECONDS"], clock)
    gateway = SimulatedBillingSystem(cfg["BILLING_DATABASE"], available("billing_available"))

    auth = AuthService(db, clock, cfg["PASSWORD_HASH_METHOD"], cfg["LOGIN_MAX_FAILURES"], cfg["LOGIN_LOCK_MINUTES"])
    offerings = OfferingQueries(db, catalog)
    semesters = SemesterService(db, clock, catalog, offerings)
    billing = BillingService(db, clock, gateway, cfg["BILLING_RETRY_BASE_SECONDS"], cfg["BILLING_RETRY_MAX_SECONDS"])
    return SimpleNamespace(
        db=db, clock=clock, catalog=catalog, catalog_client=catalog_client, billing_gateway=gateway,
        auth=auth, offerings=offerings, semesters=semesters, billing=billing,
        people=PeopleService(db, clock, auth),
        registration=RegistrationService(db, clock, semesters, offerings),
        teaching=TeachingService(db, clock, semesters, offerings),
        closing=ClosingService(db, clock, semesters, offerings, billing, cfg["TUITION_PER_CREDIT"]),
    )


def create_app(config: type | dict | None = None, clock=None) -> Flask:
    app = Flask(__name__, instance_relative_config=True)
    app.config.from_object(Config)
    if isinstance(config, dict):
        app.config.update(config)
    elif config is not None:
        app.config.from_object(config)
    os.makedirs(app.instance_path, exist_ok=True)
    for key, name in (("DATABASE", "crs.sqlite3"), ("CATALOG_DATABASE", "legacy_catalog.sqlite3"),
                      ("BILLING_DATABASE", "billing.sqlite3")):
        if not app.config.get(key):
            app.config[key] = os.path.join(app.instance_path, name)
    app.permanent_session_lifetime = timedelta(minutes=app.config["SESSION_MINUTES"])
    app.config.update(SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE="Lax")

    if not app.config["TESTING"]:
        logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    if not os.path.exists(app.config["CATALOG_DATABASE"]):
        from .seed import create_legacy_catalog
        create_legacy_catalog(app.config["CATALOG_DATABASE"])

    services = build_services(app, clock or SystemClock())
    app.extensions["crs"] = services

    from .web import register_web
    register_web(app)

    from .seed import register_cli
    register_cli(app)

    if app.config["BILLING_WORKER_ENABLED"] and os.environ.get("CRS_NO_WORKER") != "1" and \
            os.environ.get("FLASK_RUN_FROM_CLI") != "true":
        from .services.billing import BillingWorker
        worker = BillingWorker(services.billing, app.config["BILLING_WORKER_INTERVAL_SECONDS"])
        worker.start()
        app.extensions["crs_worker"] = worker
    return app
