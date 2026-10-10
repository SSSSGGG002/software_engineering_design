"""教授选择授课、花名册与成绩录入页面。建议维护人：成员D。"""
from flask import Blueprint, flash, g, redirect, render_template, request, url_for

from ..errors import DomainError
from . import flash_error, login_required, svc

bp = Blueprint("professor", __name__, url_prefix="/professor")


@bp.route("/")
@login_required("professor")
def home():
    semesters = svc().semesters.list_all()
    current = svc().semesters.current()
    code = request.args.get("semester") or (current.code if current else (semesters[-1].code if semesters else None))
    offerings = svc().teaching.teaching_offerings(g.user.professor_id, code) if code else []
    return render_template("professor/home.html", offerings=offerings, semesters=semesters, selected=code)


@bp.route("/teach", methods=["GET", "POST"])
@login_required("professor")
def teach():
    sem, choices, mine = svc().teaching.teaching_options(g.user.professor_id)
    if request.method == "POST":
        try:
            svc().teaching.select_courses_to_teach(g.user.professor_id, request.form.getlist("offerings"))
        except DomainError as err:
            flash_error(err)
        else:
            flash("授课安排已更新", "success")
            return redirect(url_for(".home"))
    return render_template("professor/teach.html", choices=choices, mine=mine, semester=sem)


@bp.route("/roster/<int:oid>")
@login_required("professor")
def roster(oid):
    entries = svc().teaching.roster(g.user.professor_id, oid)
    return render_template("professor/roster.html", entries=entries, oid=oid)


@bp.route("/grades", methods=["GET", "POST"])
@login_required("professor")
def grades():
    sem, offerings = svc().teaching.gradable_offerings(g.user.professor_id)
    oid = request.args.get("oid", type=int) or offerings[0]["offering_id"]
    entries = svc().teaching.roster(g.user.professor_id, oid)
    if request.method == "POST":
        # 只取成绩字段；额外学生编号仍由服务层进行花名册校验。
        grades = {key[6:]: val for key, val in request.form.items() if key.startswith("grade_")}
        try:
            count = svc().teaching.submit_grades(g.user.professor_id, oid, grades)
        except DomainError as err:
            flash_error(err)
        else:
            flash(f"成绩已保存，本次更新 {count} 条；留空的条目保持原值", "success")
            return redirect(url_for(".grades", oid=oid))
    return render_template("professor/grades.html", entries=entries, offerings=offerings, oid=oid, semester=sem)
