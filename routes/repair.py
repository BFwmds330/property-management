# -*- coding: utf-8 -*-
"""报修登记：列表、登记、状态流转、备注、删除。"""
from flask import redirect, flash, render_template, request, url_for, Blueprint

from routes.helpers import cur_community, need_community, safe
from services import house_service, system_service

repair_bp = Blueprint("repair", __name__)


@repair_bp.route("/repair")
@safe
def repair_list(db):
    cur = cur_community(db)
    if not cur:
        return need_community()
    status = request.args.get("status", "")
    rows = system_service.list_repairs(db, cur["id"], status)
    return render_template("repair/list.html", rows=rows, f_status=status,
                           houses=house_service.list_houses(db, cur["id"]),
                           active_nav="repair")


@repair_bp.route("/repair/add", methods=["POST"])
@safe
def repair_add(db):
    cur = cur_community(db)
    if not cur:
        return need_community()
    system_service.add_repair(db, cur["id"], request.form)
    db.commit()
    flash("报修已登记", "success")
    return redirect(url_for("repair.repair_list"))


@repair_bp.route("/repair/<int:rid>/status", methods=["POST"])
@safe
def repair_status(db, rid):
    system_service.update_repair_status(db, rid, request.form.get("action", ""))
    db.commit()
    return redirect(url_for("repair.repair_list"))


@repair_bp.route("/repair/<int:rid>/note", methods=["POST"])
@safe
def repair_note(db, rid):
    system_service.update_repair_note(db, rid, request.form)
    db.commit()
    flash("处理备注已保存", "success")
    return redirect(url_for("repair.repair_list"))


@repair_bp.route("/repair/<int:rid>/delete", methods=["POST"])
@safe
def repair_delete(db, rid):
    system_service.delete_repair(db, rid)
    db.commit()
    flash("报修记录已删除", "success")
    return redirect(url_for("repair.repair_list"))
