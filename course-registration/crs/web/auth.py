"""Login 用例的页面。负责人：成员A"""
from __future__ import annotations

from urllib.parse import urlparse

from flask import Blueprint, flash, g, redirect, render_template, request, session, url_for

from ..errors import AuthenticationError, ValidationError
from . import ROLE_HOME, flash_error, login_required, svc

bp = Blueprint("auth", __name__)


def _safe_next(target: str | None) -> str | None:
    # 只允许站内相对路径，防止开放重定向
    if target and target.startswith("/") and not target.startswith("//") and not urlparse(target).netloc:
        return target
    return None


@bp.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form.get("username", "")
        try:
            account = svc().auth.authenticate(username, request.form.get("password", ""))
        except AuthenticationError as err:
            # 备选流"用户名/密码无效"：显示错误，用户可重新输入或取消
            flash_error(err)
            return render_template("auth/login.html", username=username), 401
        session.clear()
        session["account_id"] = account.id
        session["auth_at"] = svc().clock.now().isoformat()
        flash(f"欢迎，{account.display_name}", "success")
        return redirect(_safe_next(request.args.get("next")) or url_for(ROLE_HOME[account.role]))
    if g.user:
        return redirect(url_for(ROLE_HOME[g.user.role]))
    return render_template("auth/login.html", username="")


@bp.route("/logout", methods=["POST"])
def logout():
    session.clear()
    flash("已安全退出", "info")
    return redirect(url_for("auth.login"))


@bp.route("/password", methods=["GET", "POST"])
@login_required()
def change_password():
    if request.method == "POST":
        try:
            svc().auth.change_password(g.user.id, request.form.get("old", ""), request.form.get("new", ""),
                                       request.form.get("confirm", ""))
        except ValidationError as err:
            flash_error(err)
            return render_template("auth/password.html"), 400
        flash("密码已修改", "success")
        return redirect(url_for(ROLE_HOME[g.user.role]))
    return render_template("auth/password.html")
