# -*- coding: utf-8 -*-
"""住户：全局搜索、登记到房屋、编辑、搬出、通讯录导出。"""
from flask import redirect, flash, render_template, request, url_for, Blueprint

from routes.helpers import cur_community, need_community, safe
from services import house_service, resident_service
from utils import csv_response, safe_int

resident_bp = Blueprint("resident", __name__)


@resident_bp.route("/resident")
@safe
def resident_search(db):
    cur = cur_community(db)
    if not cur:
        return need_community()
    keyword = request.args.get("keyword", "")
    rows = resident_service.global_search(db, cur["id"], keyword) if keyword.strip() else []
    return render_template("resident/search.html", rows=rows, keyword=keyword,
                           active_nav="resident")


@resident_bp.route("/resident/add")
@safe
def resident_add(db):
    cur = cur_community(db)
    if not cur:
        return need_community()
    house_id = safe_int(request.args.get("house_id"))
    role = request.args.get("role", "member")
    if role not in ("owner", "member", "tenant"):
        role = "member"
    house = house_service.get_house_full(db, house_id)
    if house["community_id"] != cur["id"]:
        flash("这套房屋不属于当前小区，请先切换小区", "warning")
        return redirect(url_for("house.house_list"))
    return render_template("resident/form.html", house=house, role=role, link=None,
                           person=None, active_nav="house")


@resident_bp.route("/resident/add", methods=["POST"])
@safe
def resident_add_post(db):
    house_id = safe_int(request.form.get("house_id"))
    resident_service.add_resident_to_house(db, house_id, request.form)
    db.commit()
    flash("住户登记成功", "success")
    return redirect(url_for("house.house_detail", hid=house_id))


@resident_bp.route("/resident/<int:link_id>/edit")
@safe
def resident_edit(db, link_id):
    cur = cur_community(db)
    if not cur:
        return need_community()
    link = resident_service.get_link(db, link_id)
    person = resident_service.get_resident(db, link["resident_id"])
    house = house_service.get_house_full(db, link["house_id"])
    return render_template("resident/form.html", house=house, role=link["role"],
                           link=link, person=person, active_nav="house")


@resident_bp.route("/resident/<int:link_id>/edit", methods=["POST"])
@safe
def resident_edit_post(db, link_id):
    link = resident_service.get_link(db, link_id)
    resident_service.update_resident(db, link["resident_id"], link_id, request.form)
    db.commit()
    flash("住户信息已保存", "success")
    return redirect(url_for("house.house_detail", hid=link["house_id"]))


@resident_bp.route("/resident/<int:link_id>/move-out", methods=["POST"])
@safe
def resident_move_out(db, link_id):
    link = resident_service.get_link(db, link_id)
    resident_service.move_out(db, link_id)
    db.commit()
    flash("已登记搬出，该住户转为历史住户", "success")
    return redirect(url_for("house.house_detail", hid=link["house_id"]))


@resident_bp.route("/resident/export")
@safe
def resident_export(db):
    cur = cur_community(db)
    if not cur:
        return need_community()
    headers, rows, filename = resident_service.export_contacts(db, cur["id"])
    return csv_response(filename, headers, rows)
