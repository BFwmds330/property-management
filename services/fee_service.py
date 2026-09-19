# -*- coding: utf-8 -*-
"""物业费核心模块：收费项目、账单生成、缴费登记、金额调整、作废、欠费与催缴。"""
import calendar
from datetime import date

from database import log_op, query_all, query_one, scalar
from utils import (UserError, clean_str, fmt_money, months_owed,
                   parse_amount, parse_date, parse_int, parse_period,
                   period_options, safe_int, today_str, this_month,
                   CYCLE_NAME, PRICING_NAME)

# ---------------------------------------------------------------- 收费项目

def list_fee_items(db, community_id, include_disabled=True):
    sql = """
        SELECT f.*,
               (SELECT COUNT(*) FROM bill b WHERE b.fee_item_id = f.id) AS bill_count
        FROM fee_item f WHERE f.community_id = ?"""
    if not include_disabled:
        sql += " AND f.enabled = 1"
    sql += " ORDER BY f.enabled DESC, f.id"
    return [dict(r) for r in query_all(db, sql, (community_id,))]


def get_fee_item(db, fid):
    row = query_one(db, "SELECT * FROM fee_item WHERE id=?", (fid,))
    if not row:
        raise UserError("没有找到这个收费项目，可能已被删除，请刷新页面")
    return row


def _item_fields(db, form):
    name = clean_str(form.get("name"), "收费项目名称", 50, required=True)
    pricing_mode = form.get("pricing_mode", "area")
    if pricing_mode not in PRICING_NAME:
        raise UserError("计价方式不正确")
    cycle = form.get("cycle", "month")
    if cycle not in CYCLE_NAME:
        raise UserError("计费周期不正确")
    # 单价统一按“元/月”口径录入：按面积即 元/㎡/月，按户即 元/户/月
    unit_price = parse_amount(form.get("unit_price"), "单价", required=True)
    remark = clean_str(form.get("remark"), "备注", 200)
    return name, pricing_mode, cycle, unit_price, remark


def add_fee_item(db, community_id, form):
    name, pricing_mode, cycle, unit_price, remark = _item_fields(db, form)
    if scalar(db, "SELECT COUNT(*) FROM fee_item WHERE community_id=? AND name=?",
              (community_id, name)) > 0:
        raise UserError("已经有叫“%s”的收费项目了" % name)
    cur = db.execute(
        """INSERT INTO fee_item (community_id, name, pricing_mode, unit_price, cycle, enabled, remark)
           VALUES (?,?,?,?,?,1,?)""",
        (community_id, name, pricing_mode, unit_price, cycle, remark))
    log_op(db, "收费", "新增收费项目", "新增收费项目「%s」（%s）" % (name, PRICING_NAME[pricing_mode]))
    return cur.lastrowid


def update_fee_item(db, fid, form):
    row = get_fee_item(db, fid)
    name, pricing_mode, cycle, unit_price, remark = _item_fields(db, form)
    if scalar(db, "SELECT COUNT(*) FROM fee_item WHERE community_id=? AND name=? AND id!=?",
              (row["community_id"], name, fid)) > 0:
        raise UserError("已经有叫“%s”的收费项目了" % name)
    db.execute(
        "UPDATE fee_item SET name=?, pricing_mode=?, unit_price=?, cycle=?, remark=? WHERE id=?",
        (name, pricing_mode, unit_price, cycle, remark, fid))
    log_op(db, "收费", "修改收费项目", "修改收费项目「%s」" % name)


def set_fee_item_enabled(db, fid, enabled):
    row = get_fee_item(db, fid)
    db.execute("UPDATE fee_item SET enabled=? WHERE id=?", (1 if enabled else 0, fid))
    log_op(db, "收费", "停用/启用收费项目", "收费项目「%s」已%s" % (row["name"], "启用" if enabled else "停用"))


def delete_fee_item(db, fid):
    row = get_fee_item(db, fid)
    used = scalar(db, "SELECT COUNT(*) FROM bill WHERE fee_item_id=?", (fid,))
    if used > 0:
        raise UserError("这个收费项目已经生成了 %d 笔账单，不能删除；可以改为“停用”" % used)
    db.execute("DELETE FROM fee_item WHERE id=?", (fid,))
    log_op(db, "收费", "删除收费项目", "删除收费项目「%s」" % row["name"])


# ---------------------------------------------------------------- 账单生成

def compute_bill_amount(fee_item, house):
    """按计价方式算一笔账单金额（分）。

    按面积：建筑面积(0.01㎡整数) × 单价(分/㎡/月) × 月数 ÷ 100；
    按户：单价(分/户/月) × 月数。
    """
    months = {"month": 1, "quarter": 3, "year": 12}[fee_item["cycle"]]
    if fee_item["pricing_mode"] == "area":
        area_100 = int(house["area_100"] or 0)
        if area_100 <= 0:
            return 0
        return (area_100 * int(fee_item["unit_price"]) * months + 50) // 100
    return int(fee_item["unit_price"]) * months


def generate_preview(db, community_id, fee_item_id, period_value, building_id=0, status_scope="all"):
    """生成前预览：哪些房屋将产生账单、合计金额。"""
    fee_item = get_fee_item(db, fee_item_id)
    if fee_item["community_id"] != community_id:
        raise UserError("该收费项目不属于当前小区")
    period, period_start, months = parse_period(fee_item["cycle"], period_value)
    houses = _scope_houses(db, community_id, fee_item, period, building_id, status_scope)
    lines = []
    total = 0
    for h in houses:
        amt = compute_bill_amount(fee_item, h)
        total += amt
        lines.append({"house": h, "amount_fen": amt})
    return {
        "fee_item": dict(fee_item),
        "period": period, "period_start": period_start, "months": months,
        "lines": lines, "count": len(lines), "total_fen": total,
        "zero_count": sum(1 for l in lines if l["amount_fen"] == 0),
    }


def _scope_houses(db, community_id, fee_item, period, building_id=0, status_scope="all"):
    sql = """SELECT h.*, b.code AS building_code FROM house h JOIN building b ON b.id=h.building_id
             WHERE h.community_id=?"""
    args = [community_id]
    if building_id:
        sql += " AND h.building_id=?"
        args.append(building_id)
    if status_scope == "occupied":
        sql += " AND h.status IN ('self','rent')"
    elif status_scope == "vacant":
        sql += " AND h.status = 'vacant'"
    sql += " ORDER BY b.id, h.unit, h.floor, h.room_no"
    houses = [dict(r) for r in query_all(db, sql, args)]
    # 排除该账期已生成过（未作废）账单的房屋
    exists = {r["house_id"] for r in query_all(db, """
        SELECT house_id FROM bill WHERE fee_item_id=? AND period=? AND status!='void'""",
        (fee_item["id"], period))}
    return [h for h in houses if h["id"] not in exists]


def generate_bills(db, community_id, form, is_demo=0):
    """确认后真正生成账单。返回 (生成笔数, 合计分, 跳过房屋数)。"""
    fee_item_id = parse_int(form.get("fee_item_id"), "收费项目", 1, 10**9)
    building_id = parse_int(form.get("building_id"), "楼栋", 0, 10**9, required=False, default=0)
    status_scope = form.get("status_scope", "all")
    preview = generate_preview(db, community_id, fee_item_id, form.get("period"), building_id, status_scope)
    if preview["count"] == 0:
        raise UserError("没有需要生成的房屋（所选范围内都已生成过这个账期的账单）")
    fee_item = preview["fee_item"]
    for line in preview["lines"]:
        db.execute(
            """INSERT INTO bill (house_id, fee_item_id, period, period_start, months, amount_receivable)
               VALUES (?,?,?,?,?,?)""",
            (line["house"]["id"], fee_item["id"], preview["period"], preview["period_start"],
             preview["months"], line["amount_fen"]))
    log_op(db, "收费", "生成账单",
           "「%s」账期 %s：生成 %d 笔账单，应收合计 %s 元"
           % (fee_item["name"], preview["period"], preview["count"], fmt_money(preview["total_fen"])), is_demo)
    return preview["count"], preview["total_fen"], 0


# ---------------------------------------------------------------- 账单查询

def list_bills(db, community_id, period="", fee_item_id=0, status="", building_id=0, keyword=""):
    sql = """
        SELECT b.*, f.name AS item_name, f.cycle AS item_cycle,
               h.unit, h.room_no, h.community_id, bd.code AS building_code,
               r.name AS owner_name,
               (b.amount_receivable - b.amount_received) AS owed_fen
        FROM bill b
        JOIN fee_item f ON f.id = b.fee_item_id
        JOIN house h ON h.id = b.house_id
        JOIN building bd ON bd.id = h.building_id
        LEFT JOIN resident r ON r.id = h.owner_resident_id
        WHERE h.community_id = ?"""
    args = [community_id]
    if period:
        sql += " AND b.period = ?"
        args.append(period.strip())
    if fee_item_id:
        sql += " AND b.fee_item_id = ?"
        args.append(fee_item_id)
    if status:
        sql += " AND b.status = ?"
        args.append(status)
    if building_id:
        sql += " AND h.building_id = ?"
        args.append(building_id)
    kw = keyword.strip()
    if kw:
        sql += """ AND (r.name LIKE ? OR (bd.code || '-' || h.unit || '-' || h.room_no) LIKE ?
                      OR b.period LIKE ? OR f.name LIKE ?)"""
        like = f"%{kw}%"
        args += [like, like, like, like]
    sql += " ORDER BY b.period_start DESC, bd.id, h.unit, h.floor, h.room_no, f.id LIMIT 1000"
    rows = [dict(r) for r in query_all(db, sql, args)]
    for r in rows:
        r["house_label"] = "%s栋%d单元%d室" % (str(r["building_code"]).replace("#", ""), r["unit"], r["room_no"])
    return rows


def distinct_periods(db, community_id):
    return [r["period"] for r in query_all(db, """
        SELECT DISTINCT b.period FROM bill b JOIN house h ON h.id=b.house_id
        WHERE h.community_id=? ORDER BY b.period_start DESC""", (community_id,))]


def get_bill_full(db, bill_id):
    row = query_one(db, """
        SELECT b.*, f.name AS item_name, f.cycle AS item_cycle, f.pricing_mode,
               v.plate AS vehicle_plate,
               h.unit, h.room_no, h.area_100, h.status AS house_status, h.community_id,
               bd.code AS building_code, r.name AS owner_name, r.phone AS owner_phone
        FROM bill b
        JOIN fee_item f ON f.id = b.fee_item_id
        JOIN house h ON h.id = b.house_id
        JOIN building bd ON bd.id = h.building_id
        LEFT JOIN resident r ON r.id = h.owner_resident_id
        LEFT JOIN vehicle v ON v.id = b.vehicle_id
        WHERE b.id = ?""", (bill_id,))
    if not row:
        raise UserError("没有找到这笔账单，可能已被删除，请刷新页面")
    d = dict(row)
    d["house_label"] = "%s栋%d单元%d室" % (str(d["building_code"]).replace("#", ""), d["unit"], d["room_no"])
    d["payments"] = [dict(p) for p in query_all(
        db, "SELECT * FROM payment WHERE bill_id=? ORDER BY pay_date, id", (bill_id,))]
    d["adjusts"] = [dict(a) for a in query_all(
        db, "SELECT * FROM bill_adjust WHERE bill_id=? ORDER BY id DESC", (bill_id,))]
    return d


# ---------------------------------------------------------------- 缴费

def _refresh_bill_status(db, bill_id):
    b = query_one(db, "SELECT amount_receivable, amount_received, status FROM bill WHERE id=?", (bill_id,))
    if not b or b["status"] == "void":
        return
    if b["amount_received"] >= b["amount_receivable"] and b["amount_receivable"] > 0:
        status = "paid"
    elif b["amount_received"] > 0:
        status = "partial"
    else:
        status = "unpaid"
    db.execute("UPDATE bill SET status=? WHERE id=?", (status, bill_id))


def add_payment(db, bill_id, form, allow_over=False):
    """在单笔账单上登记收款。"""
    bill = get_bill_full(db, bill_id)
    if bill["status"] == "void":
        raise UserError("这笔账单已作废，不能收款")
    remaining = bill["amount_receivable"] - bill["amount_received"]
    if remaining <= 0:
        raise UserError("这笔账单已经缴清，不需要再收款")
    amount = parse_amount(form.get("amount"), "实收金额", required=True, allow_zero=False)
    if amount > remaining and not allow_over:
        raise UserError("实收金额（%s 元）不能大于剩余未缴金额（%s 元）；如需调整应收金额请用“调整金额”"
                        % (fmt_money(amount), fmt_money(remaining)))
    pay_date = parse_date(form.get("pay_date"), "缴费日期", required=True)
    method = (form.get("method") or "现金").strip()
    if method not in ("现金", "转账", "扫码"):
        raise UserError("支付方式不正确")
    receipt_no = clean_str(form.get("receipt_no"), "收据号", 50)
    remark = clean_str(form.get("remark"), "备注", 200)
    cur = db.execute(
        "INSERT INTO payment (bill_id, amount, pay_date, method, receipt_no, remark) VALUES (?,?,?,?,?,?)",
        (bill_id, amount, pay_date, method, receipt_no, remark))
    db.execute("UPDATE bill SET amount_received = amount_received + ? WHERE id=?", (amount, bill_id))
    _refresh_bill_status(db, bill_id)
    log_op(db, "收费", "登记缴费", "%s %s 账期 %s 收款 %s 元（%s）"
           % (bill["house_label"], bill["item_name"], bill["period"], fmt_money(amount), method))
    return cur.lastrowid


def delete_payment(db, payment_id):
    p = query_one(db, "SELECT * FROM payment WHERE id=?", (payment_id,))
    if not p:
        raise UserError("没有找到这条缴费记录，可能已被删除，请刷新页面")
    db.execute("DELETE FROM payment WHERE id=?", (payment_id,))
    db.execute("UPDATE bill SET amount_received = MAX(0, amount_received - ?) WHERE id=?",
               (p["amount"], p["bill_id"]))
    _refresh_bill_status(db, p["bill_id"])
    log_op(db, "收费", "删除缴费记录", "删除一笔 %s 元的缴费记录（账单 #%d）" % (fmt_money(p["amount"]), p["bill_id"]))


def adjust_bill(db, bill_id, form):
    """调整单笔账单应收金额（必须填原因，留痕）。"""
    bill = get_bill_full(db, bill_id)
    if bill["status"] == "void":
        raise UserError("已作废的账单不能调整金额")
    new_amount = parse_amount(form.get("new_amount"), "调整后应收金额", required=True)
    reason = clean_str(form.get("reason"), "调整原因", 200, required=True)
    if bill["amount_received"] > new_amount:
        raise UserError("调整后的应收金额（%s 元）不能小于已收金额（%s 元）"
                        % (fmt_money(new_amount), fmt_money(bill["amount_received"])))
    db.execute("INSERT INTO bill_adjust (bill_id, before_amount, after_amount, reason) VALUES (?,?,?,?)",
               (bill_id, bill["amount_receivable"], new_amount, reason))
    db.execute("UPDATE bill SET amount_receivable=? WHERE id=?", (new_amount, bill_id))
    _refresh_bill_status(db, bill_id)
    log_op(db, "收费", "调整账单金额",
           "%s %s 账期 %s 应收由 %s 元调整为 %s 元，原因：%s"
           % (bill["house_label"], bill["item_name"], bill["period"],
              fmt_money(bill["amount_receivable"]), fmt_money(new_amount), reason))


def void_bill(db, bill_id):
    bill = get_bill_full(db, bill_id)
    if bill["status"] == "void":
        raise UserError("这笔账单已经是作废状态")
    if bill["amount_received"] > 0:
        raise UserError("这笔账单已收款 %s 元，请先在账单详情里删除对应的缴费记录，再作废"
                        % fmt_money(bill["amount_received"]))
    db.execute("UPDATE bill SET status='void' WHERE id=?", (bill_id,))
    log_op(db, "收费", "作废账单", "%s %s 账期 %s 已作废（应收 %s 元）"
           % (bill["house_label"], bill["item_name"], bill["period"], fmt_money(bill["amount_receivable"])))


def pay_all_for_house(db, house_id, form):
    """一次缴清同一房屋的多张账单：按账期从旧到新依次冲抵。"""
    from services.house_service import get_house_full, house_label
    house = get_house_full(db, house_id)
    bill_ids = form.getlist("bill_ids")
    if not bill_ids:
        raise UserError("请至少勾选一笔要缴的账单")
    total = parse_amount(form.get("total_amount"), "本次实收总额", required=True, allow_zero=False)
    pay_date = parse_date(form.get("pay_date"), "缴费日期", required=True)
    method = (form.get("method") or "现金").strip()
    if method not in ("现金", "转账", "扫码"):
        raise UserError("支付方式不正确")
    receipt_no = clean_str(form.get("receipt_no"), "收据号", 50)
    remark = clean_str(form.get("remark"), "备注", 200)
    marks = ",".join("?" for _ in bill_ids)
    bills = query_all(db, """
        SELECT * FROM bill WHERE id IN (%s) AND status IN ('unpaid','partial')
        ORDER BY period_start, id""" % marks, bill_ids)
    valid_ids = {b["id"] for b in query_all(
        db, "SELECT id FROM bill WHERE house_id=?", (house_id,))}
    for bid in bill_ids:
        if safe_int(bid, -1) not in valid_ids:
            raise UserError("所选账单里有不属于这套房的记录，请刷新页面后重试")
    if not bills:
        raise UserError("所选账单都已缴清或作废，无需缴费")
    sum_remaining = sum(b["amount_receivable"] - b["amount_received"] for b in bills)
    if total > sum_remaining:
        raise UserError("本次实收总额（%s 元）超过所选账单未缴合计（%s 元），请核对金额"
                        % (fmt_money(total), fmt_money(sum_remaining)))
    left = total
    paid_count = 0
    for b in bills:
        if left <= 0:
            break
        remaining = b["amount_receivable"] - b["amount_received"]
        if remaining <= 0:
            continue
        pay = min(remaining, left)
        db.execute(
            "INSERT INTO payment (bill_id, amount, pay_date, method, receipt_no, remark) VALUES (?,?,?,?,?,?)",
            (b["id"], pay, pay_date, method, receipt_no, remark))
        db.execute("UPDATE bill SET amount_received = amount_received + ? WHERE id=?", (pay, b["id"]))
        _refresh_bill_status(db, b["id"])
        left -= pay
        paid_count += 1
    log_op(db, "收费", "合并缴清", "%s 合并收款 %s 元，冲抵 %d 笔账单（%s）"
           % (house_label({"code": house["building_code"]}, house["unit"], house["room_no"]),
              fmt_money(total), paid_count, method))
    return total, paid_count


# ---------------------------------------------------------------- 欠费 / 催缴

def overdue_rows(db, community_id, building_id=0, keyword=""):
    """欠费清单：每行 = 一笔未缴清的账单。"""
    sql = """
        SELECT b.id AS bill_id, b.period, b.period_start, f.name AS item_name,
               h.id AS house_id, h.unit, h.room_no, bd.code AS building_code,
               r.name AS owner_name, r.phone AS owner_phone,
               b.amount_receivable, b.amount_received,
               (b.amount_receivable - b.amount_received) AS owed_fen
        FROM bill b
        JOIN fee_item f ON f.id = b.fee_item_id
        JOIN house h ON h.id = b.house_id
        JOIN building bd ON bd.id = h.building_id
        LEFT JOIN resident r ON r.id = h.owner_resident_id
        WHERE h.community_id = ? AND b.status IN ('unpaid','partial')"""
    args = [community_id]
    if building_id:
        sql += " AND h.building_id = ?"
        args.append(building_id)
    kw = keyword.strip()
    if kw:
        sql += " AND (r.name LIKE ? OR (bd.code || '-' || h.unit || '-' || h.room_no) LIKE ?)"
        like = f"%{kw}%"
        args += [like, like]
    sql += " ORDER BY bd.id, h.unit, h.floor, h.room_no, b.period_start"
    rows = [dict(r) for r in query_all(db, sql, args)]
    for r in rows:
        r["house_label"] = "%s栋%d单元%d室" % (str(r["building_code"]).replace("#", ""), r["unit"], r["room_no"])
        r["months"] = months_owed(r["period_start"]) if len(r["period_start"]) == 7 else None
    return rows


def house_overdue_summary(db, house_id):
    """某套房屋的全部欠费（用于催缴短信 / 一键缴清页）。"""
    rows = query_all(db, """
        SELECT b.id, b.period, b.amount_receivable, b.amount_received,
               (b.amount_receivable - b.amount_received) AS owed_fen, f.name AS item_name
        FROM bill b JOIN fee_item f ON f.id = b.fee_item_id
        WHERE b.house_id = ? AND b.status IN ('unpaid','partial')
        ORDER BY b.period_start, b.id""", (house_id,))
    return [dict(r) for r in rows]


def reminder_text(db, house_id):
    """生成一段可直接复制发送的催缴提醒短信。"""
    from services.house_service import get_house_full, house_label
    house = get_house_full(db, house_id)
    label = house_label({"code": house["building_code"]}, house["unit"], house["room_no"])
    bills = house_overdue_summary(db, house_id)
    total = sum(b["owed_fen"] for b in bills)
    owner = house["owner_name"] or "业主"
    if not bills:
        return None, label, owner
    lines = "、".join("%s %s 欠 %s 元" % (b["item_name"], b["period"], fmt_money(b["owed_fen"]))
                      for b in bills)
    text = ("【物业缴费提醒】尊敬的%s业主：您好！您在%s的物业费尚未缴清（%s），"
            "合计 %s 元。请您于近期到物业服务中心缴纳，也可联系物业查询明细。感谢您的理解与支持！"
            % (owner, label, lines, fmt_money(total)))
    return text, label, owner


# ---------------------------------------------------------------- 缴费流水

def payment_records(db, community_id, keyword="", date_from="", date_to="", fee_item_id=0, method=""):
    sql = """
        SELECT p.*, b.period, f.name AS item_name,
               h.id AS house_id, h.unit, h.room_no, bd.code AS building_code,
               r.name AS owner_name
        FROM payment p
        JOIN bill b ON b.id = p.bill_id
        JOIN fee_item f ON f.id = b.fee_item_id
        JOIN house h ON h.id = b.house_id
        JOIN building bd ON bd.id = h.building_id
        LEFT JOIN resident r ON r.id = h.owner_resident_id
        WHERE h.community_id = ?"""
    args = [community_id]
    kw = keyword.strip()
    if kw:
        sql += " AND (r.name LIKE ? OR (bd.code || '-' || h.unit || '-' || h.room_no) LIKE ? OR p.receipt_no LIKE ?)"
        like = f"%{kw}%"
        args += [like, like, like]
    if date_from:
        sql += " AND p.pay_date >= ?"
        args.append(date_from)
    if date_to:
        sql += " AND p.pay_date <= ?"
        args.append(date_to)
    if fee_item_id:
        sql += " AND f.id = ?"
        args.append(fee_item_id)
    if method:
        sql += " AND p.method = ?"
        args.append(method)
    sql += " ORDER BY p.pay_date DESC, p.id DESC LIMIT 2000"
    rows = [dict(r) for r in query_all(db, sql, args)]
    for r in rows:
        r["house_label"] = "%s栋%d单元%d室" % (str(r["building_code"]).replace("#", ""), r["unit"], r["room_no"])
    return rows
