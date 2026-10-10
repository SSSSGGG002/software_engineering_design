"""Maintain Student / Professor Information 页面。负责人：成员B

用例子流程映射：
  Add      GET/POST /registrar/{students|professors}/new
  Update   GET /registrar/.../lookup?id=  →  GET/POST /registrar/.../<id>/edit
  Delete   GET /registrar/.../<id>/delete（显示信息并请求确认） → POST 确认；取消则回到列表
  Not Found：查找不到时提示错误，可重新输入编号或取消
"""
from __future__ import annotations

from flask import Blueprint, flash, redirect, render_template, request, url_for

from ..errors import ConflictError, NotFoundError, ValidationError
from . import flash_error, login_required, svc

bp = Blueprint("registrar", __name__, url_prefix="/registrar")

KINDS = {
    "students": dict(label="学生", get="get_student", list="list_students", add="add_student",
                     update="update_student", delete="delete_student", form="registrar/student_form.html"),
    "professors": dict(label="教授", get="get_professor", list="list_professors", add="add_professor",
                       update="update_professor", delete="delete_professor", form="registrar/professor_form.html"),
}


def _kind(kind):
    if kind not in KINDS:
        from flask import abort
        abort(404)
    return KINDS[kind]


def _call(kind, op, *args):
    return getattr(svc().people, _kind(kind)[op])(*args)


@bp.route("/")
@login_required("registrar")
def home():
    s = svc()
    return render_template("registrar/home.html",
                           n_students=len(s.people.list_students()), n_professors=len(s.people.list_professors()),
                           pending_bills=s.billing.pending_count())


@bp.route("/<kind>/")
@login_required("registrar")
def people_list(kind):
    k = _kind(kind)
    q = request.args.get("q", "")
    return render_template("registrar/people_list.html", kind=kind, k=k, people=_call(kind, "list", q), q=q)


@bp.route("/<kind>/lookup")
@login_required("registrar")
def lookup(kind):
    """用例：请注册员输入编号 → 检索并显示。"""
    action = request.args.get("action", "edit")
    try:
        person = _call(kind, "get", request.args.get("id", ""))
    except NotFoundError as err:
        flash_error(err)    # 备选流 Not Found：提示后可重新输入或取消
        return redirect(url_for(".people_list", kind=kind))
    endpoint = ".delete" if action == "delete" else ".edit"
    return redirect(url_for(endpoint, kind=kind, pid=person.id))


@bp.route("/<kind>/new", methods=["GET", "POST"])
@login_required("registrar")
def new(kind):
    k = _kind(kind)
    if request.method == "POST":
        try:
            person, password = _call(kind, "add", request.form.to_dict())
        except (ValidationError, ConflictError) as err:
            flash_error(err)
            return render_template(k["form"], kind=kind, k=k, form=request.form, errors=getattr(err, "field_errors", {}),
                                   mode="new"), 400
        flash(f"已添加{k['label']} {person.name}，系统分配编号 {person.id}，登录名 {person.username}，"
              f"初始密码 {password}", "success")
        return redirect(url_for(".people_list", kind=kind))
    return render_template(k["form"], kind=kind, k=k, form={}, errors={}, mode="new")


@bp.route("/<kind>/<int:pid>/edit", methods=["GET", "POST"])
@login_required("registrar")
def edit(kind, pid):
    k = _kind(kind)
    person = _call(kind, "get", pid)
    if request.method == "POST":
        try:
            person = _call(kind, "update", pid, request.form.to_dict())
        except (ValidationError, ConflictError) as err:
            flash_error(err)
            return render_template(k["form"], kind=kind, k=k, form=request.form, errors=getattr(err, "field_errors", {}),
                                   mode="edit", person=person), 400
        flash(f"{k['label']} {person.id} 的信息已更新", "success")
        return redirect(url_for(".people_list", kind=kind))
    return render_template(k["form"], kind=kind, k=k, form=person.__dict__, errors={}, mode="edit", person=person)


@bp.route("/<kind>/<int:pid>/delete", methods=["GET", "POST"])
@login_required("registrar")
def delete(kind, pid):
    k = _kind(kind)
    person = _call(kind, "get", pid)
    if request.method == "POST":
        if request.form.get("confirm") != "yes":
            flash("已取消删除", "info")      # 备选流 Delete Cancelled
            return redirect(url_for(".people_list", kind=kind))
        try:
            _call(kind, "delete", pid)
        except ConflictError as err:
            flash_error(err)
            return redirect(url_for(".people_list", kind=kind))
        flash(f"已删除{k['label']} {person.id} {person.name}", "success")
        return redirect(url_for(".people_list", kind=kind))
    return render_template("registrar/confirm_delete.html", kind=kind, k=k, person=person)
