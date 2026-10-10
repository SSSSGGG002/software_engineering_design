"""Web 层公共设施：蓝图注册、登录与角色检查、CSRF、统一异常处理。

Web 层只做三件事：解析请求 → 调用服务 → 渲染结果。业务规则全部在 services/ 中，
因此每个用例都可以脱离浏览器做单元测试。

负责人：成员A    各角色页面：auth(A) registrar(B) student(C) professor(D) admin(E)
"""
from __future__ import annotations

import logging
import secrets
from datetime import datetime
from functools import wraps

from flask import abort, current_app, flash, g, jsonify, redirect, render_template, request, session, url_for

from ..domain import (DAY_NAMES, DEPARTMENTS, ITEM_KIND, ITEM_STATUS, OFFERING_STATUS, PROFESSOR_STATUS,
                      STUDENT_STATUS)
from ..errors import (AuthenticationError, CatalogUnavailableError, ConflictError, DomainError, NotFoundError,
                      PermissionDeniedError, ValidationError)

log = logging.getLogger(__name__)

ROLE_HOME = {"student": "student.home", "professor": "professor.home", "registrar": "registrar.home"}
ROLE_LABEL = {"student": "学生", "professor": "教授", "registrar": "注册员"}


def svc():
    """当前应用的服务容器。"""
    return current_app.extensions["crs"]


# ------------------------------------------------------------------ 会话与权限
def login_required(role: str | None = None):
    def decorator(view):
        @wraps(view)
        def wrapped(*args, **kwargs):
            if g.user is None:
                flash("请先登录", "warning")
                return redirect(url_for("auth.login", next=request.full_path))
            if role and g.user.role != role:
                # 补充规约：学生不能修改他人课表、只有教授能录入成绩、只有注册员能维护人员信息
                abort(403)
            return view(*args, **kwargs)
        return wrapped
    return decorator


def csrf_token() -> str:
    if "_csrf" not in session:
        session["_csrf"] = secrets.token_urlsafe(24)
    return session["_csrf"]


def _check_csrf():
    if request.method == "POST" and not current_app.config.get("WTF_CSRF_DISABLED"):
        token = request.form.get("_csrf") or request.headers.get("X-CSRF-Token")
        if not token or token != session.get("_csrf"):
            flash("页面已过期，请重新操作", "warning")
            return redirect(request.referrer or url_for("auth.login"))
    return None


def _load_user():
    g.user = None
    account_id = session.get("account_id")
    if account_id is None:
        return
    try:
        g.user = svc().auth.get_account(account_id)
    except NotFoundError:
        # 账号已被删除（例如注册员删除了该学生），会话立即失效
        session.clear()


# ------------------------------------------------------------------ 异常处理
def flash_error(err: DomainError) -> None:
    flash(err.message, "danger")
    for d in err.details:
        flash(d, "danger-detail")


def _handle_domain_error(err: DomainError):
    """未在视图中就地处理的业务异常：提示并返回上一页（状态不变）。"""
    if request.path.startswith("/api/") or request.accept_mimetypes.best == "application/json":
        status = {NotFoundError: 404, PermissionDeniedError: 403, ValidationError: 400, ConflictError: 409,
                  AuthenticationError: 401, CatalogUnavailableError: 503}.get(type(err), 400)
        return jsonify(error=err.message, details=err.details), status
    if request.method == "GET":
        # 故障页直接呈现，避免角色首页依赖目录失败时循环重定向。
        status = 503 if isinstance(err, CatalogUnavailableError) else 400
        if isinstance(err, PermissionDeniedError):
            status = 403
        elif isinstance(err, NotFoundError):
            status = 404
        return render_template("error.html", code=status, message=err.message), status
    flash_error(err)
    target = url_for(ROLE_HOME[g.user.role]) if g.get("user") else url_for("auth.login")
    return redirect(target)


def register_web(app):
    from . import admin, auth, professor, registrar, student

    for bp in (auth.bp, registrar.bp, student.bp, professor.bp, admin.bp):
        app.register_blueprint(bp)

    @app.before_request
    def before():
        session.permanent = True
        _load_user()
        return _check_csrf()

    app.register_error_handler(DomainError, _handle_domain_error)

    @app.errorhandler(403)
    def forbidden(_e):
        return render_template("error.html", code=403, message="您没有权限访问该页面"), 403

    @app.errorhandler(404)
    def not_found(_e):
        return render_template("error.html", code=404, message="页面不存在"), 404

    @app.errorhandler(405)
    def method_not_allowed(_e):
        return render_template("error.html", code=405, message="请求方式不正确"), 405

    @app.errorhandler(500)
    def server_error(e):
        log.exception("未处理的异常: %s", e)
        return render_template("error.html", code=500, message="系统内部错误，请稍后重试；问题已记录"), 500

    @app.context_processor
    def inject():
        current = None
        phase_label = None
        try:
            from ..services.semesters import PHASE_LABELS
            current = svc().semesters.current()
            phase_label = PHASE_LABELS[svc().semesters.phase(current)] if current else None
        except Exception:      # 页头信息失败不应影响页面本身
            log.exception("读取当前学期失败")
        return dict(csrf_token=csrf_token, ROLE_LABEL=ROLE_LABEL, STUDENT_STATUS=STUDENT_STATUS,
                    PROFESSOR_STATUS=PROFESSOR_STATUS, DEPARTMENTS=DEPARTMENTS, ITEM_KIND=ITEM_KIND,
                    ITEM_STATUS=ITEM_STATUS, OFFERING_STATUS=OFFERING_STATUS, DAY_NAMES=DAY_NAMES,
                    current_semester=current, phase_label=phase_label, now=svc().clock.now())

    @app.route("/")
    def index():
        if g.user:
            return redirect(url_for(ROLE_HOME[g.user.role]))
        return redirect(url_for("auth.login"))

    @app.route("/healthz")
    def healthz():
        with svc().db.reader() as conn:
            conn.execute("SELECT 1").fetchone()
        return jsonify(status="ok", time=datetime.now().isoformat(timespec="seconds"))
