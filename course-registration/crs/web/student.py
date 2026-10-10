"""学生选课与成绩单页面。建议维护人：成员C；成绩规则依赖成员D。"""
from datetime import datetime

from flask import Blueprint, current_app, flash, g, jsonify, redirect, render_template, request, session, url_for

from ..errors import DomainError, NotFoundError
from . import flash_error, login_required, svc

bp = Blueprint("student", __name__, url_prefix="/student")


@bp.route("/")
@login_required("student")
def home():
    s = svc()
    semesters = s.semesters.list_all()
    code = request.args.get("semester")
    current = s.semesters.current()
    code = code or (current.code if current else (semesters[-1].code if semesters else None))
    schedule = None
    if code:
        try:
            schedule = s.registration.get_schedule(g.user.student_id, code)
        except DomainError as err:
            flash_error(err)
    return render_template("student/home.html", schedule=schedule, semesters=semesters, selected=code)


@bp.route("/schedule", methods=["GET", "POST"])
@login_required("student")
def schedule():
    s = svc()
    choices = s.registration.available_offerings()
    existing = s.registration.get_schedule(g.user.student_id)
    primary = [i.offering.offering_id for i in existing.primaries] if existing else []
    alternate = [i.offering.offering_id for i in existing.alternates] if existing else []
    version = existing.version if existing else 0
    status = 200
    if request.method == "POST":
        primary = request.form.getlist("primary")
        alternate = request.form.getlist("alternate")
        version = request.form.get("version", "0")
        try:
            submitted = request.form.get("action") == "submit"
            s.registration.save_schedule(g.user.student_id, primary, alternate, version, submitted)
        except DomainError as err:
            flash_error(err)
            status = 409
        else:
            flash("课表已提交注册" if submitted else "课表已保存；新选课程尚未占用名额", "success")
            return redirect(url_for(".home"))
    # 保留提交失败时的输入；版本号保持原值，冲突时必须刷新。
    return render_template("student/schedule.html", choices=choices, primary=list(map(str, primary)),
                           alternate=list(map(str, alternate)), version=version), status


@bp.route("/schedule/delete", methods=["GET", "POST"])
@login_required("student")
def delete():
    existing = svc().registration.get_schedule(g.user.student_id)
    if existing is None:
        raise NotFoundError("未找到您本学期的课表")
    if request.method == "POST":
        if request.form.get("confirm") == "yes":
            svc().registration.delete_schedule(g.user.student_id, request.form.get("version"))
            flash("课表已删除，已占用名额同时释放", "success")
        else:
            flash("已取消删除", "info")
        return redirect(url_for(".home"))
    return render_template("student/delete.html", schedule=existing)


@bp.route("/api/seats")
@login_required("student")
def seats():
    return jsonify(svc().registration.seat_status())


@bp.route("/report", methods=["GET", "POST"])
@login_required("student")
def report():
    # 服务端身份取自会话；请求中的 student_id 参数不会用于查询其他学生。
    confirmed = session.get("report_auth_at")
    recent = confirmed and (svc().clock.now() - datetime.fromisoformat(confirmed)).total_seconds() < \
        current_app.config["RECENT_AUTH_MINUTES"] * 60
    if request.method == "POST":
        try:
            svc().auth.authenticate(g.user.username, request.form.get("password", ""))
        except DomainError as err:
            flash_error(err)
        else:
            session["report_auth_at"] = svc().clock.now().isoformat()
            return redirect(url_for(".report", semester=request.args.get("semester")))
    if not recent:
        return render_template("student/confirm_password.html")
    semesters = svc().semesters.closed_semesters()
    card = None
    try:
        card = svc().teaching.report_card(g.user.student_id, request.args.get("semester"))
    except NotFoundError as err:
        flash(err.message, "info")
    response = current_app.make_response(render_template("student/report.html", card=card, semesters=semesters))
    response.headers["Cache-Control"] = "no-store"
    return response
