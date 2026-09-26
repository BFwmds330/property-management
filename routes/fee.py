# -*- coding: utf-8 -*-
"""收费核心：收费项目、生成账单（预览→确认）、账单列表/详情、收款、调整、作废、
一键缴清、欠费与催缴短信、缴费流水查询与导出。"""
from flask import redirect, flash, render_template, request, url_for, Blueprint, jsonify

from routes.helpers import build_pagination, cur_community, need_community, safe
from services import fee_service, house_service
from utils import BILL_STATUS, UserError, csv_response, house_label, safe_int, today_str

fee_bp = Blueprint("fee", __name__)


# ---------------------------------------------------------------- 收费项目

@fee_bp.route("/fee/items")
@safe
def fee_items(db):
    cur = cur_community(db)
    if not cur:
        return need_community()
    return render_template("fee/items.html", rows=fee_service.list_fee_items(db, cur["id"]),
                           active_nav="fee")


@fee_bp.route("/fee/items/add", methods=["POST"])
@safe
def fee_items_add(db):
    cur = cur_community(db)
    if not cur:
        return need_community()
    fee_service.add_fee_item(db, cur["id"], request.form)
    db.commit()
    flash("收费项目已添加", "success")
    return redirect(url_for("fee.fee_items"))


@fee_bp.route("/fee/items/<int:fid>/edit", methods=["POST"])
@safe
def fee_items_edit(db, fid):
    fee_service.update_fee_item(db, fid, request.form)
    db.commit()
    flash("收费项目已保存", "success")
    return redirect(url_for("fee.fee_items"))


@fee_bp.route("/fee/items/<int:fid>/toggle", methods=["POST"])
@safe
def fee_items_toggle(db, fid):
    fee_service.set_fee_item_enabled(db, fid, request.form.get("enabled") == "1")
    db.commit()
    return redirect(url_for("fee.fee_items"))


@fee_bp.route("/fee/items/<int:fid>/delete", methods=["POST"])
@safe
def fee_items_delete(db, fid):
    fee_service.delete_fee_item(db, fid)
    db.commit()
    flash("收费项目已删除", "success")
    return redirect(url_for("fee.fee_items"))


# ---------------------------------------------------------------- 生成账单

@fee_bp.route("/fee/generate")
@safe
def generate_form(db):
    cur = cur_community(db)
    if not cur:
        return need_community()
    items = fee_service.list_fee_items(db, cur["id"], include_disabled=False)
    return render_template("fee/generate.html", items=items,
                           buildings=house_service.list_buildings(db, cur["id"]),
                           active_nav="fee")


@fee_bp.route("/fee/generate", methods=["POST"])
@safe
def generate_post(db):
    cur = cur_community(db)
    if not cur:
        return need_community()
    args = request.form
    preview = fee_service.generate_preview(
        db, cur["id"], safe_int(args.get("fee_item_id")), args.get("period"),
        safe_int(args.get("building_id")), args.get("status_scope", "all"))
    items = fee_service.list_fee_items(db, cur["id"], include_disabled=False)
    return render_template("fee/generate.html", items=items,
                           buildings=house_service.list_buildings(db, cur["id"]),
                           preview=preview, form=args, active_nav="fee")


@fee_bp.route("/fee/generate/confirm", methods=["POST"])
@safe
def generate_confirm(db):
    cur = cur_community(db)
    if not cur:
        return need_community()
    count, total_fen, _ = fee_service.generate_bills(db, cur["id"], request.form)
    db.commit()
    from utils import fmt_money
    flash("账单生成完成：共 %d 笔，应收合计 %s 元" % (count, fmt_money(total_fen)), "success")
    return redirect(url_for("fee.bill_list", period=request.form.get("period", "")))


# ---------------------------------------------------------------- 账单

@fee_bp.route("/fee/bills")
@safe
def bill_list(db):
    cur = cur_community(db)
    if not cur:
        return need_community()
    args = request.args
    filters = dict(
        period=args.get("period", ""),
        fee_item_id=safe_int(args.get("fee_item_id")),
        status=args.get("status", ""),
        building_id=safe_int(args.get("building_id")),
        keyword=args.get("keyword", ""))
    total = fee_service.count_bills(db, cur["id"], **filters)
    page, pages, page_list, page_url = build_pagination(args, safe_int(args.get("page"), 1),
                                                        total, fee_service.BILL_PAGE_SIZE)
    rows = fee_service.list_bills(db, cur["id"], page=page, **filters)
    return render_template(
        "fee/bills.html", rows=rows, periods=fee_service.distinct_periods(db, cur["id"]),
        items=fee_service.list_fee_items(db, cur["id"]),
        buildings=house_service.list_buildings(db, cur["id"]),
        total=total, page=page, pages=pages, page_list=page_list, page_url=page_url,
        f_period=args.get("period", ""), f_item=args.get("fee_item_id", ""),
        f_status=args.get("status", ""), f_building=args.get("building_id", ""),
        f_keyword=args.get("keyword", ""), active_nav="fee")


@fee_bp.route("/fee/bill/<int:bill_id>")
@safe
def bill_detail(db, bill_id):
    cur = cur_community(db)
    if not cur:
        return need_community()
    bill = fee_service.get_bill_full(db, bill_id)
    if bill["community_id"] != cur["id"]:
        flash("这笔账单不属于当前小区，请先切换小区", "warning")
        return redirect(url_for("fee.bill_list"))
    return render_template("fee/bill_detail.html", bill=bill,
                           pay_date_default=today_str(), active_nav="fee")


@fee_bp.route("/fee/bill/<int:bill_id>/pay", methods=["POST"])
@safe
def bill_pay(db, bill_id):
    fee_service.add_payment(db, bill_id, request.form)
    db.commit()
    flash("收款登记成功", "success")
    return redirect(url_for("fee.bill_detail", bill_id=bill_id))


@fee_bp.route("/fee/bill/<int:bill_id>/adjust", methods=["POST"])
@safe
def bill_adjust(db, bill_id):
    fee_service.adjust_bill(db, bill_id, request.form)
    db.commit()
    flash("应收金额已调整（已留痕，可在账单详情查看调整历史）", "success")
    return redirect(url_for("fee.bill_detail", bill_id=bill_id))


@fee_bp.route("/fee/bill/<int:bill_id>/edit-period", methods=["POST"])
@safe
def bill_edit_period(db, bill_id):
    cur = cur_community(db)
    if not cur:
        return need_community()
    bill = fee_service.get_bill_full(db, bill_id)
    if bill["community_id"] != cur["id"]:
        flash("这笔账单不属于当前小区，请先切换小区", "warning")
        return redirect(url_for("fee.bill_list"))
    fee_service.edit_bill_period(db, bill_id, request.form)
    db.commit()
    flash("账期已修改：%s → %s（可在操作日志追溯）" % (
        bill["period"], request.form.get("new_period", "")), "success")
    return redirect(url_for("fee.bill_detail", bill_id=bill_id))


@fee_bp.route("/fee/bill/<int:bill_id>/void", methods=["POST"])
@safe
def bill_void(db, bill_id):
    fee_service.void_bill(db, bill_id)
    db.commit()
    flash("账单已作废", "success")
    return redirect(url_for("fee.bill_list"))


@fee_bp.route("/fee/payment/<int:payment_id>/delete", methods=["POST"])
@safe
def payment_delete(db, payment_id):
    bill_id = request.form.get("bill_id")
    fee_service.delete_payment(db, payment_id)
    db.commit()
    flash("缴费记录已删除，账单状态已同步更新", "success")
    if bill_id:
        return redirect(url_for("fee.bill_detail", bill_id=bill_id))
    return redirect(url_for("fee.payments"))


@fee_bp.route("/fee/bills/export")
@safe
def bills_export(db):
    """账单列表导出 CSV：与账单页相同的筛选条件，导出全部符合条件的行。"""
    cur = cur_community(db)
    if not cur:
        return need_community()
    args = request.args
    rows = fee_service.list_bills(
        db, cur["id"], period=args.get("period", ""),
        fee_item_id=safe_int(args.get("fee_item_id")),
        status=args.get("status", ""),
        building_id=safe_int(args.get("building_id")),
        keyword=args.get("keyword", ""))
    headers = ["房号", "业主", "收费项目", "账期", "应收(元)", "已收(元)", "未收(元)", "状态"]
    out = [[r["house_label"], r["owner_name"] or "", r["item_name"], r["period"],
            "%.2f" % (r["amount_receivable"] / 100), "%.2f" % (r["amount_received"] / 100),
            "%.2f" % (r["owed_fen"] / 100), BILL_STATUS.get(r["status"], r["status"])] for r in rows]
    return csv_response("账单列表.csv", headers, out)


# ---------------------------------------------------------------- 一键缴清

@fee_bp.route("/fee/house/<int:hid>/payall")
@safe
def payall_form(db, hid):
    cur = cur_community(db)
    if not cur:
        return need_community()
    house = house_service.get_house_full(db, hid)
    if house["community_id"] != cur["id"]:
        flash("这套房屋不属于当前小区，请先切换小区", "warning")
        return redirect(url_for("house.house_list"))
    bills = fee_service.house_overdue_summary(db, hid)
    from services import prepaid_service
    prepaid_balance = prepaid_service.get_balance(db, hid)
    return render_template("fee/payall.html", house=house, bills=bills,
                           prepaid_balance=prepaid_balance,
                           pay_date_default=today_str(), active_nav="fee")


@fee_bp.route("/fee/house/<int:hid>/payall", methods=["POST"])
@safe
def payall_post(db, hid):
    total, paid_count, excess, offset = fee_service.pay_all_for_house(db, hid, request.form)
    db.commit()
    from utils import fmt_money
    msg = "合并收款成功：实收 %s 元，冲抵 %d 笔账单" % (fmt_money(total), paid_count)
    if excess > 0:
        msg += "；多收的 %s 元已自动转入该房屋预收余额" % fmt_money(excess)
    if offset > 0:
        msg += "；已用预收余额抵扣 %s 元" % fmt_money(offset)
    flash(msg, "success")
    return redirect(url_for("house.house_detail", hid=hid))


# ---------------------------------------------------------------- 欠费与催缴

@fee_bp.route("/fee/overdue")
@safe
def overdue(db):
    cur = cur_community(db)
    if not cur:
        return need_community()
    args = request.args
    rows = fee_service.overdue_rows(
        db, cur["id"], building_id=safe_int(args.get("building_id")),
        keyword=args.get("keyword", ""))
    total = sum(r["owed_fen"] for r in rows)
    return render_template("fee/overdue.html", rows=rows, total=total,
                           buildings=house_service.list_buildings(db, cur["id"]),
                           f_building=args.get("building_id", ""),
                           f_keyword=args.get("keyword", ""), active_nav="fee")


@fee_bp.route("/fee/overdue/export")
@safe
def overdue_export(db):
    cur = cur_community(db)
    if not cur:
        return need_community()
    rows = fee_service.overdue_rows(db, cur["id"])
    headers = ["房号", "业主", "手机号", "收费项目", "账期", "应收(元)", "已收(元)", "欠费(元)", "欠费月数"]
    out = [[r["house_label"], r["owner_name"] or "", r["owner_phone"] or "", r["item_name"],
            r["period"], "%.2f" % (r["amount_receivable"] / 100),
            "%.2f" % (r["amount_received"] / 100), "%.2f" % (r["owed_fen"] / 100),
            r["months"] or ""] for r in rows]
    cur_row = cur["name"] if cur else "小区"
    return csv_response("%s-催缴名单.csv" % cur_row, headers, out)


@fee_bp.route("/fee/overdue/sms/<int:hid>")
def overdue_sms(hid):
    db = None
    try:
        from database import get_db
        db = get_db()
        text, label, owner = fee_service.reminder_text(db, hid)
        if text is None:
            return jsonify({"ok": True, "text": "", "label": label,
                            "msg": "这户当前没有欠费账单"})
        return jsonify({"ok": True, "text": text, "label": label})
    except Exception:
        return jsonify({"ok": False, "msg": "生成短信失败，请刷新页面后重试"}), 200


# ---------------------------------------------------------------- 缴费流水

@fee_bp.route("/fee/payments")
@safe
def payments(db):
    cur = cur_community(db)
    if not cur:
        return need_community()
    args = request.args
    filters = dict(
        keyword=args.get("keyword", ""),
        date_from=args.get("date_from", ""), date_to=args.get("date_to", ""),
        fee_item_id=safe_int(args.get("fee_item_id")), method=args.get("method", ""))
    # 合计按筛选条件全量统计（不随分页截断）
    stats = fee_service.payment_stats(db, cur["id"], **filters)
    page, pages, page_list, page_url = build_pagination(args, safe_int(args.get("page"), 1),
                                                        stats["count"], fee_service.BILL_PAGE_SIZE)
    rows = fee_service.payment_records(db, cur["id"], page=page, **filters)
    return render_template(
        "fee/payments.html", rows=rows, total=stats["sum_fen"], total_count=stats["count"],
        items=fee_service.list_fee_items(db, cur["id"]),
        page=page, pages=pages, page_list=page_list, page_url=page_url,
        f_keyword=args.get("keyword", ""), f_from=args.get("date_from", ""),
        f_to=args.get("date_to", ""), f_item=args.get("fee_item_id", ""),
        f_method=args.get("method", ""), active_nav="fee")


@fee_bp.route("/fee/payments/export")
@safe
def payments_export(db):
    cur = cur_community(db)
    if not cur:
        return need_community()
    args = request.args
    rows = fee_service.payment_records(
        db, cur["id"], keyword=args.get("keyword", ""),
        date_from=args.get("date_from", ""), date_to=args.get("date_to", ""),
        fee_item_id=safe_int(args.get("fee_item_id")), method=args.get("method", ""))
    headers = ["缴费日期", "房号", "业主", "收费项目", "账期", "金额(元)", "支付方式", "收据号", "备注"]
    out = [[r["pay_date"], r["house_label"], r["owner_name"] or "", r["item_name"], r["period"],
            "%.2f" % (r["amount"] / 100), r["method"], r["receipt_no"], r["remark"]] for r in rows]
    return csv_response("缴费流水.csv", headers, out)


# ---------------------------------------------------------------- 收据打印

@fee_bp.route("/fee/receipt")
@safe
def receipt(db):
    """收款收据打印页：?ids=1,2,3 多选流水，一联一张收据，浏览器打印即可（可另存 PDF）。"""
    from database import query_all
    from utils import money_capital
    ids = []
    for part in (request.args.get("ids") or "").split(","):
        pid = safe_int(part)
        if pid > 0:
            ids.append(pid)
    if not ids:
        raise UserError("请先选择要打印的缴费记录")
    ids = ids[:50]     # 一次最多 50 联，防止误传超大列表
    marks = ",".join("?" for _ in ids)
    rows = query_all(db, """
        SELECT p.*, b.period, b.period_start, f.name AS item_name,
               h.unit, h.room_no, bd.code AS building_code, r.name AS owner_name,
               c.name AS community_name
        FROM payment p
        JOIN bill b ON b.id = p.bill_id
        JOIN fee_item f ON f.id = b.fee_item_id
        JOIN house h ON h.id = b.house_id
        JOIN building bd ON bd.id = h.building_id
        LEFT JOIN resident r ON r.id = h.owner_resident_id
        JOIN community c ON c.id = h.community_id
        WHERE p.id IN (%s)
        ORDER BY p.id""" % marks, ids)
    out = []
    for r in rows:
        d = dict(r)
        d["house_label"] = house_label(d["building_code"], d["unit"], d["room_no"])
        d["capital"] = money_capital(d["amount"])
        out.append(d)
    return render_template("fee/receipt.html", rows=out, active_nav="fee")
