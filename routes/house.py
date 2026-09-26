# -*- coding: utf-8 -*-
"""房屋 / 楼栋 / 户型管理：列表、筛选、增删改、批量生成、房屋详情、产权过户、名册导入。"""
import json

from flask import (Blueprint, abort, flash, redirect, render_template, request,
                   session, url_for)

from routes.helpers import cur_community, need_community, safe
from services import house_service, house_import, prepaid_service, resident_service
from utils import csv_response, safe_int

house_bp = Blueprint("house", __name__)
building_bp = Blueprint("building", __name__)
htype_bp = Blueprint("htype", __name__)


# ---------------------------------------------------------------- 房屋

@house_bp.route("/house")
@safe
def house_list(db):
    cur = cur_community(db)
    if not cur:
        return need_community()
    args = request.args
    rows = house_service.list_houses(
        db, cur["id"],
        building_id=safe_int(args.get("building_id")),
        unit=safe_int(args.get("unit")),
        status=args.get("status", ""),
        keyword=args.get("keyword", ""),
        only_owed=1 if args.get("only_owed") else 0)
    return render_template(
        "house/list.html", rows=rows, buildings=house_service.list_buildings(db, cur["id"]),
        f_building=args.get("building_id", ""), f_unit=args.get("unit", ""),
        f_status=args.get("status", ""), f_keyword=args.get("keyword", ""),
        f_only_owed=args.get("only_owed", ""), active_nav="house")


@house_bp.route("/house/add")
@safe
def house_add(db):
    cur = cur_community(db)
    if not cur:
        return need_community()
    return render_template(
        "house/form.html", house=None, buildings=house_service.list_buildings(db, cur["id"]),
        types=house_service.list_house_types(db, cur["id"]),
        residents=resident_service.global_search(db, cur["id"], "", limit=200),
        active_nav="house")


@house_bp.route("/house/add", methods=["POST"])
@safe
def house_add_post(db):
    cur = cur_community(db)
    if not cur:
        return need_community()
    hid = house_service.add_house(db, cur["id"], request.form)
    db.commit()
    flash("房屋添加成功", "success")
    return redirect(url_for("house.house_detail", hid=hid))


@house_bp.route("/house/<int:hid>/edit")
@safe
def house_edit(db, hid):
    cur = cur_community(db)
    if not cur:
        return need_community()
    house = house_service.get_house_full(db, hid)
    if house["community_id"] != cur["id"]:
        flash("这套房屋不属于当前小区，请先切换小区", "warning")
        return redirect(url_for("house.house_list"))
    return render_template(
        "house/form.html", house=house, buildings=house_service.list_buildings(db, cur["id"]),
        types=house_service.list_house_types(db, cur["id"]),
        residents=[], active_nav="house")


@house_bp.route("/house/<int:hid>/edit", methods=["POST"])
@safe
def house_edit_post(db, hid):
    cur = cur_community(db)
    if not cur:
        return need_community()
    house_service.update_house(db, hid, request.form)
    db.commit()
    flash("房屋信息已保存", "success")
    return redirect(url_for("house.house_detail", hid=hid))


@house_bp.route("/house/<int:hid>/delete", methods=["POST"])
@safe
def house_delete(db, hid):
    house_service.delete_house(db, hid)
    db.commit()
    flash("房屋已删除", "success")
    return redirect(url_for("house.house_list"))


@house_bp.route("/house/<int:hid>")
@safe
def house_detail(db, hid):
    cur = cur_community(db)
    if not cur:
        return need_community()
    house = house_service.get_house_full(db, hid)
    if house["community_id"] != cur["id"]:
        flash("这套房屋不属于当前小区，请先切换小区", "warning")
        return redirect(url_for("house.house_list"))
    residents = resident_service.house_residents(db, hid)
    history = resident_service.house_history(db, hid)
    bills = resident_bills(db, hid)
    prepaid_balance = prepaid_service.get_balance(db, hid)
    prepaid_entries = prepaid_service.list_entries(db, hid)
    return render_template("house/detail.html", house=house, residents=residents,
                           history=history, bills=bills,
                           prepaid_balance=prepaid_balance, prepaid_entries=prepaid_entries,
                           active_nav="house")


@house_bp.route("/house/<int:hid>/prepaid/add", methods=["POST"])
@safe
def prepaid_add(db, hid):
    """手工登记预收款（多收挂账 / 提前预存）。"""
    cur = cur_community(db)
    if not cur:
        return need_community()
    house = house_service.get_house_full(db, hid)
    if house["community_id"] != cur["id"]:
        flash("这套房屋不属于当前小区，请先切换小区", "warning")
        return redirect(url_for("house.house_list"))
    amount = prepaid_service.credit_from_form(db, hid, request.form)
    db.commit()
    from utils import fmt_money
    flash("预收款登记成功：入账 %s 元，当前余额 %s 元"
          % (fmt_money(amount), fmt_money(prepaid_service.get_balance(db, hid))), "success")
    return redirect(url_for("house.house_detail", hid=hid))


def resident_bills(db, hid):
    from database import query_all
    rows = query_all(db, """
        SELECT b.*, f.name AS item_name,
               (b.amount_receivable - b.amount_received) AS owed_fen
        FROM bill b JOIN fee_item f ON f.id = b.fee_item_id
        WHERE b.house_id = ? AND b.status != 'void'
        ORDER BY b.period_start DESC, f.id LIMIT 60""", (hid,))
    return [dict(r) for r in rows]


@house_bp.route("/house/batch")
@safe
def house_batch(db):
    cur = cur_community(db)
    if not cur:
        return need_community()
    return render_template("house/batch.html", buildings=house_service.list_buildings(db, cur["id"]),
                           types=house_service.list_house_types(db, cur["id"]), active_nav="house")


@house_bp.route("/house/batch", methods=["POST"])
@safe
def house_batch_post(db):
    cur = cur_community(db)
    if not cur:
        return need_community()
    created, skipped = house_service.batch_generate(db, cur["id"], request.form)
    db.commit()
    flash("批量生成完成：新建 %d 套房屋%s" % (
        created, ("，跳过已存在的 %d 套" % skipped) if skipped else ""), "success")
    return redirect(url_for("house.house_list"))


@house_bp.route("/house/<int:hid>/transfer")
@safe
def house_transfer(db, hid):
    cur = cur_community(db)
    if not cur:
        return need_community()
    house = house_service.get_house_full(db, hid)
    if house["community_id"] != cur["id"]:
        flash("这套房屋不属于当前小区，请先切换小区", "warning")
        return redirect(url_for("house.house_list"))
    return render_template("house/transfer.html", house=house,
                           residents=resident_service.global_search(db, cur["id"], "", limit=200),
                           active_nav="house")


@house_bp.route("/house/<int:hid>/transfer", methods=["POST"])
@safe
def house_transfer_post(db, hid):
    house_service.transfer_owner(db, hid, request.form)
    db.commit()
    flash("产权过户完成，旧业主信息已转入历史记录", "success")
    return redirect(url_for("house.house_detail", hid=hid))


@house_bp.route("/house/<int:hid>/vehicle/add", methods=["POST"])
@safe
def vehicle_add(db, hid):
    cur = cur_community(db)
    if not cur:
        return need_community()
    house = house_service.get_house_full(db, hid)
    if house["community_id"] != cur["id"]:
        from flask import flash
        flash("这套房屋不属于当前小区，请先切换小区", "warning")
        return redirect(url_for("house.house_list"))
    house_service.add_vehicle(db, hid, request.form)
    db.commit()
    from flask import flash
    flash("车辆登记成功", "success")
    return redirect(url_for("house.house_detail", hid=hid))


@house_bp.route("/house/vehicle/<int:vid>/delete", methods=["POST"])
@safe
def vehicle_delete(db, vid):
    house_service.delete_vehicle(db, vid)
    db.commit()
    from flask import flash
    flash("车辆登记已删除", "success")
    return redirect(request.referrer or url_for("house.house_list"))


# ---------------------------------------------------------------- 名册导入

@house_bp.route("/house/import")
@safe
def house_import_form(db):
    cur = cur_community(db)
    if not cur:
        return need_community()
    return render_template("house/import.html", active_nav="house")


@house_bp.route("/house/import/template")
def house_import_template():
    """下载导入模板（内容为虚构示例数据）。"""
    return csv_response("业主名册导入模板.csv",
                        house_import.IMPORT_TEMPLATE_ROWS[0],
                        house_import.IMPORT_TEMPLATE_ROWS[1:])


@house_bp.route("/house/import/preview", methods=["POST"])
@safe
def house_import_preview(db):
    cur = cur_community(db)
    if not cur:
        return need_community()
    f = request.files.get("file")
    if not f or not f.filename:
        raise house_import.UserError("请先选择要导入的 CSV 文件")
    rows = house_import.read_import_csv(f.read())
    records, errors = house_import.parse_rows(rows)
    stats = house_import.analyze_import(db, cur["id"], records)
    return render_template(
        "house/import.html", records=records, errors=errors, stats=stats,
        payload=json.dumps(rows, ensure_ascii=False),
        total_rows=len(records), active_nav="house")


@house_bp.route("/house/import/confirm", methods=["POST"])
@safe
def house_import_confirm(db):
    cur = cur_community(db)
    if not cur:
        return need_community()
    payload = request.form.get("payload", "")
    import json
    try:
        rows = [(int(i), r) for i, r in json.loads(payload)]
    except (ValueError, TypeError):
        from flask import flash
        flash("导入数据已过期，请重新上传文件", "warning")
        return redirect(url_for("house.house_import_form"))
    records, errors = house_import.parse_rows(rows)
    if errors:
        from flask import flash
        flash("还有 %d 行数据有问题，无法导入，请修正后重新上传" % len(errors), "danger")
        return redirect(url_for("house.house_import_form"))
    created = house_import.execute_import(db, cur["id"], records)
    db.commit()
    from flask import flash
    flash("导入完成：新建房屋 %d 套、更新 %d 套、业主 %d 人、家庭成员 %d 人、租户 %d 人、车辆 %d 辆"
          % (created["houses_new"], created["houses_updated"], created["owners"],
             created["members"], created["tenants"], created["vehicles"]), "success")
    return redirect(url_for("house.house_list"))


# ---------------------------------------------------------------- 楼栋

@building_bp.route("/building")
@safe
def building_list(db):
    cur = cur_community(db)
    if not cur:
        return need_community()
    return render_template("house/buildings.html", rows=house_service.list_buildings(db, cur["id"]),
                           active_nav="house")


@building_bp.route("/building/add", methods=["POST"])
@safe
def building_add(db):
    cur = cur_community(db)
    if not cur:
        return need_community()
    house_service.add_building(db, cur["id"], request.form)
    db.commit()
    flash("楼栋已添加", "success")
    return redirect(url_for("building.building_list"))


@building_bp.route("/building/<int:bid>/edit", methods=["POST"])
@safe
def building_edit(db, bid):
    house_service.update_building(db, bid, request.form)
    db.commit()
    flash("楼栋信息已保存", "success")
    return redirect(url_for("building.building_list"))


@building_bp.route("/building/<int:bid>/delete", methods=["POST"])
@safe
def building_delete(db, bid):
    house_service.delete_building(db, bid)
    db.commit()
    flash("楼栋及其房屋已删除", "success")
    return redirect(url_for("building.building_list"))


# ---------------------------------------------------------------- 户型

@htype_bp.route("/house-type")
@safe
def htype_list(db):
    cur = cur_community(db)
    if not cur:
        return need_community()
    rows = house_service.list_house_types(db, cur["id"])
    total_houses = sum(r["used_count"] for r in rows)
    return render_template("house/types.html", rows=rows, total_houses=total_houses,
                           active_nav="house")


@htype_bp.route("/house-type/add", methods=["POST"])
@safe
def htype_add(db):
    cur = cur_community(db)
    if not cur:
        return need_community()
    house_service.add_house_type(db, cur["id"], request.form)
    db.commit()
    flash("户型已添加", "success")
    return redirect(url_for("htype.htype_list"))


@htype_bp.route("/house-type/<int:tid>/edit", methods=["POST"])
@safe
def htype_edit(db, tid):
    house_service.update_house_type(db, tid, request.form)
    db.commit()
    flash("户型信息已保存", "success")
    return redirect(url_for("htype.htype_list"))


@htype_bp.route("/house-type/<int:tid>/delete", methods=["POST"])
@safe
def htype_delete(db, tid):
    house_service.delete_house_type(db, tid)
    db.commit()
    flash("户型已删除", "success")
    return redirect(url_for("htype.htype_list"))
