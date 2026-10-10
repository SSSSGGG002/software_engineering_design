"""注册周期、计费与外部依赖演示。建议维护人：成员E。"""
from flask import Blueprint, flash, redirect, render_template, request, url_for

from ..db import get_setting, set_setting
from . import login_required, svc

bp = Blueprint("admin", __name__, url_prefix="/admin")


@bp.route("/")
@login_required("registrar")
def dashboard():
    s = svc()
    with s.db.reader() as conn:
        switches = {k: get_setting(conn, k, "1") for k in ("catalog_available", "billing_available")}
    return render_template("registrar/admin.html", semesters=s.semesters.list_all(),
                           bills=s.billing.list_outbox(), switches=switches, report=None)


@bp.route("/open", methods=["POST"])
@login_required("registrar")
def open_semester():
    svc().semesters.open_semester(request.form.get("semester", ""), request.form.get("deadline", ""))
    flash("学期已开放", "success")
    return redirect(url_for(".dashboard"))


@bp.route("/end", methods=["POST"])
@login_required("registrar")
def end():
    if request.form.get("confirm") == "yes":
        svc().semesters.end_add_drop(request.form.get("semester", ""))
        flash("加退选期已结束，可以执行关闭注册", "success")
    return redirect(url_for(".dashboard"))


@bp.route("/close", methods=["POST"])
@login_required("registrar")
def close():
    if request.form.get("confirm") != "yes":
        return redirect(url_for(".dashboard"))
    report = svc().closing.close_registration()
    return render_template("registrar/close_report.html", report=report)


@bp.route("/retry", methods=["POST"])
@login_required("registrar")
def retry():
    sent, pending = svc().billing.dispatch_due(force=True)
    flash(f"已发送 {sent} 张账单，待发送 {pending} 张", "info")
    return redirect(url_for(".dashboard"))


@bp.route("/switch", methods=["POST"])
@login_required("registrar")
def switch():
    key = request.form.get("key")
    if key in ("catalog_available", "billing_available"):
        with svc().db.transaction() as conn:
            set_setting(conn, key, "1" if request.form.get("value") == "1" else "0")
        if key == "catalog_available":
            svc().catalog.invalidate()
        flash("演示开关已更新（目录开关同时清空缓存）", "info")
    return redirect(url_for(".dashboard"))
