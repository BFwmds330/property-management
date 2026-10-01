# -*- coding: utf-8 -*-
"""物业费核心模块：收费项目、账单生成、缴费登记、金额调整、作废、欠费与催缴。"""
import calendar
import re
from datetime import date

from database import log_op, query_all, query_one, scalar
from utils import (UserError, clean_str, fmt_money, house_label, months_owed,
                   parse_amount, parse_date, parse_int, parse_period,
                   period_options, safe_int, today_str, this_month,
                   CYCLE_NAME, PRICING_NAME)

# 列表页每页行数（0 笔账单/流水都按此分页，杜绝旧版"超过 1000 笔静默截断"）
BILL_PAGE_SIZE = 200

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
    # v2.5.0：允许同名项目（如两档"物业费"，单价/范围不同）；
    # 但名称+计价方式+周期+单价完全相同的重复项没有意义，仍然拦截
    dup = query_one(db, """SELECT id FROM fee_item WHERE community_id=? AND name=?
                           AND pricing_mode=? AND cycle=? AND unit_price=?""",
                    (community_id, name, pricing_mode, cycle, unit_price))
    if dup:
        raise UserError("已经有名称、计价方式、周期、单价完全相同的收费项目了（项目 #%d），无需重复添加" % dup["id"])
    cur = db.execute(
        """INSERT INTO fee_item (community_id, name, pricing_mode, unit_price, cycle, enabled, remark)
           VALUES (?,?,?,?,?,1,?)""",
        (community_id, name, pricing_mode, unit_price, cycle, remark))
    log_op(db, "收费", "新增收费项目", "新增收费项目「%s」（%s）" % (name, PRICING_NAME[pricing_mode]))
    return cur.lastrowid


def update_fee_item(db, fid, form):
    row = get_fee_item(db, fid)
    name, pricing_mode, cycle, unit_price, remark = _item_fields(db, form)
    dup = query_one(db, """SELECT id FROM fee_item WHERE community_id=? AND name=?
                           AND pricing_mode=? AND cycle=? AND unit_price=? AND id!=?""",
                    (row["community_id"], name, pricing_mode, cycle, unit_price, fid))
    if dup:
        raise UserError("已经有名称、计价方式、周期、单价完全相同的收费项目了（项目 #%d），无需重复添加" % dup["id"])
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

def compute_bill_amount(fee_item, house, months=None):
    """按计价方式算一笔账单金额（分）。

    按面积：建筑面积(0.01㎡整数) × 单价(分/㎡/月) × 月数 ÷ 100；
    按户：单价(分/户/月) × 月数。months 缺省时按收费项目周期取整段月数。
    """
    if months is None:
        months = {"month": 1, "quarter": 3, "year": 12}[fee_item["cycle"]]
    if fee_item["pricing_mode"] == "area":
        area_100 = int(house["area_100"] or 0)
        if area_100 <= 0:
            return 0
        return (area_100 * int(fee_item["unit_price"]) * months + 50) // 100
    return int(fee_item["unit_price"]) * months


def _coverage_map(db, community_id, item_name):
    """该小区同名项目全部未作废账单的覆盖图：{house_id: {"covered": set(月键), "any": True}}。

    月键 = year*12+month-1；period_start 格式异常的账单计入 any（说明该户开过单）
    但不计入 covered（无法确定覆盖月份）。
    """
    cmap = {}
    for b in query_all(db, """
            SELECT b.house_id, b.period_start, b.months FROM bill b
            JOIN fee_item f2 ON f2.id = b.fee_item_id
            JOIN house h ON h.id = b.house_id
            WHERE h.community_id = ? AND f2.name = ? AND b.status != 'void'""",
            (community_id, item_name)):
        e = cmap.setdefault(b["house_id"], {"covered": set(), "any": True})
        mm = re.match(r"^(\d{4})-(\d{1,2})$", (b["period_start"] or "").strip())
        if mm:
            try:
                n = max(int(b["months"] or 1), 1)
            except (TypeError, ValueError):
                n = 1
            base = int(mm.group(1)) * 12 + int(mm.group(2)) - 1
            for k in range(n):
                e["covered"].add(base + k)
    return cmap


def generate_preview(db, community_id, fee_item_id, period_value, building_id=0, status_scope="all",
                     house_ids=None):
    """生成前预览：哪些房屋将产生账单、合计金额。

    building_id 传原始值（""/"selected"/楼栋id）："selected" 表示指定户模式，按 house_ids 勾选。
    v2.6.1 账期覆盖核对：逐户核对该户同名项目已有账单覆盖的月份——
    已全部覆盖的户自动跳过；部分覆盖的户只生成缺口月份（账期自动改为缺口段，可产生多笔）。
    """
    fee_item = get_fee_item(db, fee_item_id)
    if fee_item["community_id"] != community_id:
        raise UserError("该收费项目不属于当前小区")
    period, period_start, months = parse_period(fee_item["cycle"], period_value)
    houses = _scope_houses(db, community_id, fee_item, period, building_id, status_scope,
                           house_ids=house_ids)
    s_key = int(period_start[:4]) * 12 + int(period_start[5:7]) - 1
    e_key = s_key + months - 1
    cmap = _coverage_map(db, community_id, fee_item["name"])
    tier_map = {}
    assigned = {h.get("fee_item_id") for h in houses if h.get("fee_item_id")}
    assigned.discard(fee_item["id"])
    assigned.discard(None)
    for aid in assigned:
        it = query_one(db, "SELECT * FROM fee_item WHERE id=? AND community_id=?", (aid, community_id))
        if it:
            tier_map[aid] = dict(it)
    c_row = query_one(db, "SELECT billing_start FROM community WHERE id=?", (community_id,))
    bs = ((c_row["billing_start"] if c_row else "") or "").strip()
    mm_bs = re.match(r"^(\d{4})-(\d{1,2})$", bs)
    b_key = (int(mm_bs.group(1)) * 12 + int(mm_bs.group(2)) - 1) if mm_bs else None
    lines = []
    total = 0
    covered_skipped = 0
    adjusted = 0
    backfilled = 0
    for h in houses:
        info = cmap.get(h["id"])
        covered = info["covered"] if info else set()
        # v2.8.1：起点规则——
        #   从未开单的房屋：从小区"计费开始月份"（未设则为所选账期首月）起；
        #   有缴费记录的房屋：从最后一笔覆盖月份的次月起（中间断档不往回补，已缴到账期之后的跳过）。
        if covered:
            span_s = max(max(covered) + 1, b_key if b_key is not None else 0)
        elif b_key is not None:
            span_s = b_key
        else:
            span_s = s_key
        if span_s > e_key:
            covered_skipped += 1        # 已缴/预缴到账期之后，无需生成
            continue
        backfill = span_s != s_key
        r_months = e_key - span_s + 1
        y1, m1 = divmod(span_s, 12)
        y2, m2 = divmod(e_key, 12)
        full = (span_s == s_key)
        line_period = period if full else "%d.%d-%d.%d" % (y1, m1 + 1, y2, m2 + 1)
        line_ps = "%04d-%02d" % (y1, m1 + 1)
        # v2.7.1：本户适用档位——house.fee_item_id 指向同名项目时按本户档位计价
        eff_item, eff_tier = fee_item, False
        if h.get("fee_item_id") and h["fee_item_id"] != fee_item["id"]:
            it = tier_map.get(h["fee_item_id"])
            if it and it["name"] == fee_item["name"] and it["community_id"] == community_id:
                eff_item, eff_tier = it, True
        amt = compute_bill_amount(eff_item, h, r_months)
        total += amt
        lines.append({"house": h, "amount_fen": amt, "period": line_period,
                      "period_start": line_ps, "months": r_months,
                      "adjusted": not full, "backfill": backfill,
                      "first_for_house": True, "item": eff_item,
                      "tier": bool(eff_tier)})
        if not full:
            adjusted += 1
            if backfill:
                backfilled += 1
    # v2.6.0：给每户带上名下车辆（预览页可为停车费类账单选择关联车牌）
    if lines:
        hid_list = []
        for l in lines:
            if l["house"]["id"] not in hid_list:
                hid_list.append(l["house"]["id"])
        marks = ",".join("?" for _ in hid_list)
        veh = {}
        for v in query_all(db, "SELECT id, house_id, plate FROM vehicle WHERE house_id IN (%s) ORDER BY id" % marks,
                           hid_list):
            veh.setdefault(v["house_id"], []).append({"id": v["id"], "plate": v["plate"]})
        for l in lines:
            l["house"]["vehicles"] = veh.get(l["house"]["id"], [])
    return {
        "fee_item": dict(fee_item),
        "period": period, "period_start": period_start, "months": months,
        "lines": lines, "count": len(lines), "total_fen": total,
        "zero_count": sum(1 for l in lines if l["amount_fen"] == 0),
        "covered_skipped": covered_skipped, "adjusted": adjusted,
        "backfilled": backfilled,
    }


def _scope_houses(db, community_id, fee_item, period, building_id=0, status_scope="all",
                  house_ids=None):
    sql = """SELECT h.*, b.code AS building_code, r.name AS owner_name
             FROM house h
             JOIN building b ON b.id=h.building_id
             LEFT JOIN resident r ON r.id = h.owner_resident_id
             WHERE h.community_id=?"""
    args = [community_id]
    bid = safe_int(building_id)          # ""/"0"/"selected" 都归一为 0（不过滤楼栋）
    if bid:
        sql += " AND h.building_id=?"
        args.append(bid)
    if status_scope == "occupied":
        sql += " AND h.status IN ('self','rent')"
    elif status_scope == "vacant":
        sql += " AND h.status = 'vacant'"
    if building_id == "selected":
        # 指定户模式：只生成勾选的户；一户没勾就是 0 户（绝不回退成"全部房屋"）
        ids = [safe_int(x) for x in (house_ids or []) if safe_int(x)]
        if not ids:
            return []
        if len(ids) > 5000:
            raise UserError("一次最多勾选 5000 户")
        marks = ",".join("?" for _ in ids)
        sql += " AND h.id IN (%s)" % marks
        args += ids
    sql += " ORDER BY b.id, h.unit, h.floor, h.room_no"
    # v2.8.0：不再按"同账期同名"排除房屋——账期只是截止月，核对区间从计费开始月起，
    # 防重复完全由 _coverage_map 的按月覆盖判定承担（同账期字符串的旧排除会挡住缺口补齐）
    return [dict(r) for r in query_all(db, sql, args)]


def generate_bills(db, community_id, form, is_demo=0):
    """确认后真正生成账单。返回 (生成笔数, 合计分, 跳过房屋数)。"""
    fee_item_id = parse_int(form.get("fee_item_id"), "收费项目", 1, 10**9)
    house_ids = form.getlist("house_ids")
    preview = generate_preview(db, community_id, fee_item_id, form.get("period"),
                               form.get("building_id", 0), form.get("status_scope", "all"),
                               house_ids=house_ids)
    if preview["count"] == 0:
        raise UserError("没有需要生成的账单（所选范围内该账期已被已有账单覆盖；指定户模式下请至少勾选一户）")
    fee_item = preview["fee_item"]
    linked = 0
    for line in preview["lines"]:
        # v2.6.0：可按户选择关联车辆（停车费类账单），校验车辆确实属于该房屋
        vehicle_id = None
        if line.get("first_for_house"):
            raw_vid = safe_int(form.get("vehicle_for_%d" % line["house"]["id"]))
            if raw_vid:
                vrow = query_one(db, "SELECT house_id, plate FROM vehicle WHERE id=?", (raw_vid,))
                if not vrow or vrow["house_id"] != line["house"]["id"]:
                    raise UserError("勾选的关联车辆与房屋不匹配（车牌 %s），请刷新页面后重试"
                                    % (vrow["plate"] if vrow else "#%d" % raw_vid))
                vehicle_id = raw_vid
                linked += 1
        cur = db.execute(
            """INSERT INTO bill (house_id, fee_item_id, period, period_start, months, amount_receivable, vehicle_id)
               VALUES (?,?,?,?,?,?,?)""",
            (line["house"]["id"], line["item"]["id"], line["period"], line["period_start"],
             line["months"], line["amount_fen"], vehicle_id))
        # 生成即按公式定状态：单价为 0（免收）时直接是已缴清，不产生"欠费 0 元"的杂音
        _refresh_bill_status(db, cur.lastrowid)
    log_op(db, "收费", "生成账单",
           "「%s」账期 %s：生成 %d 笔账单，应收合计 %s 元%s%s%s"
           % (fee_item["name"], preview["period"], preview["count"], fmt_money(preview["total_fen"]),
              "，自动补齐缺口 %d 笔" % preview["adjusted"] if preview["adjusted"] else "",
              "，自动起补 %d 笔（自上次缴至月次月或计费开始月）" % preview["backfilled"] if preview["backfilled"] else "",
              "；另有 %d 户该期间已被已有账单覆盖跳过" % preview["covered_skipped"]
              if preview["covered_skipped"] else ""), is_demo)
    return preview["count"], preview["total_fen"], preview["covered_skipped"]


# ---------------------------------------------------------------- 账单查询

def _bill_where(community_id, period="", fee_item_id=0, status="", building_id=0, keyword=""):
    """账单列表的公共 WHERE 片段（count / 分页查询共用，保证口径一致）。"""
    sql = """
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
    return sql, args


def count_bills(db, community_id, period="", fee_item_id=0, status="", building_id=0, keyword=""):
    """符合条件的账单总笔数（分页用，不受每页行数限制）。"""
    where, args = _bill_where(community_id, period, fee_item_id, status, building_id, keyword)
    return scalar(db, "SELECT COUNT(*)" + where, args)


def list_bills(db, community_id, period="", fee_item_id=0, status="", building_id=0, keyword="",
               page=0, page_size=BILL_PAGE_SIZE):
    """账单列表。page>0 时按页返回（page_size 行/页）；page=0 返回全部（CSV 导出用）。"""
    where, args = _bill_where(community_id, period, fee_item_id, status, building_id, keyword)
    sql = """
        SELECT b.*, f.name AS item_name, f.cycle AS item_cycle,
               h.unit, h.room_no, h.community_id, bd.code AS building_code,
               r.name AS owner_name,
               (b.amount_receivable - b.amount_received) AS owed_fen
        """ + where
    if page:
        sql += " ORDER BY b.period_start DESC, bd.id, h.unit, h.floor, h.room_no, f.id LIMIT ? OFFSET ?"
        args = args + [page_size, (page - 1) * page_size]
    else:
        sql += " ORDER BY b.period_start DESC, bd.id, h.unit, h.floor, h.room_no, f.id"
    rows = [dict(r) for r in query_all(db, sql, args)]
    for r in rows:
        r["house_label"] = house_label(r["building_code"], r["unit"], r["room_no"])
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
    d["house_label"] = house_label(d["building_code"], d["unit"], d["room_no"])
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
    if b["amount_receivable"] <= 0:
        # 应缴金额为 0：属于免收/减免到 0，账单视为已结清，不计入欠费名单
        status = "paid"
    elif b["amount_received"] >= b["amount_receivable"]:
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
    # 预收抵扣的流水删掉后，把等额金额退回该房屋的预收余额（保持台账平衡）
    if p["method"] == "预收抵扣":
        from services import prepaid_service
        b = query_one(db, "SELECT house_id FROM bill WHERE id=?", (p["bill_id"],))
        if b:
            prepaid_service.add_ledger(
                db, b["house_id"], p["amount"], method="预收退回",
                remark="删除预收抵扣流水（账单 #%d），金额退回预收余额" % p["bill_id"],
                log_detail="删除预收抵扣流水 %s 元，金额已退回房屋 #%d 的预收余额"
                           % (fmt_money(p["amount"]), b["house_id"]))
    log_op(db, "收费", "删除缴费记录", "删除一笔 %s 元的缴费记录（账单 #%d）" % (fmt_money(p["amount"]), p["bill_id"]))


def edit_bill_period(db, bill_id, form):
    """编辑账期（历史导入的账单账期口径自由，如 2029.1-2029.9 / 2025.1-2025.12）。

    支持格式：YYYY.M-YYYY.M、YYYY.M.D-YYYY.M.D、YYYY-MM、YYYY.M、YYYY（年）。
    账单金额、状态、缴费流水均不变；同房同项目同账期唯一，撞期拒绝并提示。
    """
    bill = get_bill_full(db, bill_id)
    if bill["status"] == "void":
        raise UserError("已作废的账单不能修改账期")
    raw = (form.get("new_period") or "").strip()
    if not raw:
        raise UserError("请填写新账期")
    old_label = bill["period"]
    m = re.match(r"^(\d{4})[.．](\d{1,2})\s*[-—~～至]\s*(\d{4})[.．](\d{1,2})$", raw)
    if m:
        y1, m1, y2, m2 = int(m.group(1)), int(m.group(2)), int(m.group(3)), int(m.group(4))
        if not (1 <= m1 <= 12 and 1 <= m2 <= 12 and (y2, m2) >= (y1, m1)):
            raise UserError("账期格式或顺序不正确：%r（应为 起始年.月-截止年.月，例如 2029.1-2029.9）" % raw)
        period = "%d.%d-%d.%d" % (y1, m1, y2, m2)
        period_start = "%04d-%02d" % (y1, m1)
        months = (y2 - y1) * 12 + (m2 - m1) + 1
    elif re.match(r"^\d{4}[.．-]\d{1,2}$", raw):
        y1, m1 = int(raw[:4]), int(re.search(r"[.．-](\d{1,2})$", raw).group(1))
        if not 1 <= m1 <= 12:
            raise UserError("月份不正确：%r" % raw)
        period = "%04d-%02d" % (y1, m1)
        period_start, months = period, 1
    elif re.fullmatch(r"\d{4}", raw):
        period, period_start, months = raw, raw + "-01", 12
    else:
        raise UserError("账期格式不正确：%r。可用格式：2029.1-2029.9（年.月-年.月）、2026-09（单月）、2026（整年）" % raw)
    dup = query_one(db, """
        SELECT b.id FROM bill b JOIN fee_item f2 ON f2.id = b.fee_item_id
        WHERE b.house_id=? AND b.period=? AND b.id!=? AND f2.name =
              (SELECT name FROM fee_item WHERE id=?)""",
        (bill["house_id"], period, bill_id, bill["fee_item_id"]))
    if dup:
        raise UserError("这套房已有账期 %s 的同名项目账单（账单 #%d）：同房同账期同名欠费不能重复" % (period, dup["id"]))
    db.execute("UPDATE bill SET period=?, period_start=?, months=? WHERE id=?",
               (period, period_start, months, bill_id))
    log_op(db, "收费", "修改账期", "账单 #%d（%s %s）账期由 %s 改为 %s" % (
        bill_id, bill["item_name"], bill["house_label"], old_label, period))


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
    """一次缴清同一房屋的多张账单：按账期从旧到新依次冲抵（v2.3.0 起支持预收）。

    - 实收总额超过所选账单未缴合计的部分**自动转预收**（不再报错），多收的钱挂到该房屋余额；
    - 勾选"用预收余额抵扣"时，现金冲抵后仍未缴清的账单按同样顺序用预收余额补足：
      账单侧记 method='预收抵扣' 的缴费流水，台账记等额负数。
    返回 (现金实收分, 冲抵账单笔数, 转预收分, 预收抵扣分)。
    """
    from services.house_service import get_house_full
    from services import prepaid_service
    house = get_house_full(db, house_id)
    bill_ids = form.getlist("bill_ids")
    if not bill_ids:
        raise UserError("请至少勾选一笔要缴的账单")
    use_prepaid = form.get("use_prepaid") == "1"
    total = parse_amount(form.get("total_amount"), "本次实收总额", required=True,
                         allow_zero=use_prepaid)
    if total == 0 and not use_prepaid:
        raise UserError("实收总额不能为 0；如果这次全部用预收余额抵扣，请勾选“用预收余额抵扣”")
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
    if not bills and not use_prepaid:
        raise UserError("所选账单都已缴清或作废，无需缴费")
    sum_remaining = sum(b["amount_receivable"] - b["amount_received"] for b in bills)

    # 第一轮：现金按账期从旧到新冲抵，最多冲到全部缴清
    applied = min(total, sum_remaining)
    left = applied
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

    # 第二轮：多收的部分自动转预收
    excess = total - applied
    house_label_txt = house_label(house["building_code"], house["unit"], house["room_no"])
    if excess > 0:
        prepaid_service.add_ledger(
            db, house_id, excess, method=method, op_date=pay_date,
            remark="一键缴清多收转预收（现金 %s 元 > 账单未缴 %s 元）"
                   % (fmt_money(total), fmt_money(sum_remaining)),
            log_detail="%s 一键缴清多收 %s 元自动转预收" % (house_label_txt, fmt_money(excess)))

    # 第三轮：勾选了预收抵扣时，把剩余未缴账单用余额补足
    offset_total = 0
    if use_prepaid:
        balance = prepaid_service.get_balance(db, house_id)
        still = query_all(db, """
            SELECT * FROM bill WHERE id IN (%s) AND status IN ('unpaid','partial')
            ORDER BY period_start, id""" % marks, bill_ids)
        for b in still:
            if balance <= 0:
                break
            remaining = b["amount_receivable"] - b["amount_received"]
            if remaining <= 0:
                continue
            pay = min(remaining, balance)
            db.execute(
                "INSERT INTO payment (bill_id, amount, pay_date, method, receipt_no, remark) VALUES (?,?,?,?,?,?)",
                (b["id"], pay, pay_date, "预收抵扣", receipt_no, "使用预收余额抵扣" + ("：" + remark if remark else "")))
            db.execute("UPDATE bill SET amount_received = amount_received + ? WHERE id=?", (pay, b["id"]))
            _refresh_bill_status(db, b["id"])
            balance -= pay
            offset_total += pay
            paid_count += 1
        if offset_total > 0:
            prepaid_service.add_ledger(
                db, house_id, -offset_total, method="预收抵扣", op_date=pay_date,
                remark="预收余额抵扣账单 %s 元" % fmt_money(offset_total),
                log_detail="%s 用预收余额抵扣账单 %s 元" % (house_label_txt, fmt_money(offset_total)))

    log_op(db, "收费", "合并缴清", "%s 合并收款 %s 元，冲抵 %d 笔账单（%s）"
           % (house_label_txt, fmt_money(total), paid_count, method))
    return total, paid_count, excess, offset_total


# ---------------------------------------------------------------- 欠费 / 催缴

def count_overdue(db, community_id, building_id=0, keyword=""):
    where, args = _overdue_where(community_id, building_id, keyword)
    return scalar(db, "SELECT COUNT(*)" + where, args)


def _overdue_where(community_id, building_id=0, keyword=""):
    sql = """
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
    kw = (keyword or "").strip()
    if kw:
        sql += " AND (r.name LIKE ? OR (bd.code || '-' || h.unit || '-' || h.room_no) LIKE ?)"
        like = f"%{kw}%"
        args += [like, like]
    return sql, args


def overdue_rows(db, community_id, building_id=0, keyword="", page=0, page_size=200):
    """欠费清单：每行 = 一笔未缴清的账单。page>0 时按页返回；page=0 返回全部（CSV 导出用）。"""
    where, args = _overdue_where(community_id, building_id, keyword)
    sql = """
        SELECT b.id AS bill_id, b.period, b.period_start, f.name AS item_name,
               h.id AS house_id, h.unit, h.room_no, bd.code AS building_code,
               r.name AS owner_name, r.phone AS owner_phone,
               b.amount_receivable, b.amount_received,
               (b.amount_receivable - b.amount_received) AS owed_fen
        """ + where
    sql += " ORDER BY bd.id, h.unit, h.floor, h.room_no, b.period_start"
    if page:
        sql += " LIMIT ? OFFSET ?"
        args = list(args) + [page_size, (page - 1) * page_size]
    rows = [dict(r) for r in query_all(db, sql, args)]
    for r in rows:
        r["house_label"] = house_label(r["building_code"], r["unit"], r["room_no"])
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
    from services.house_service import get_house_full
    house = get_house_full(db, house_id)
    label = house_label(house["building_code"], house["unit"], house["room_no"])
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

def _payment_where(community_id, keyword="", date_from="", date_to="", fee_item_id=0, method=""):
    """缴费流水的公共 WHERE 片段（列表 / 计数 / 合计共用，保证口径一致）。"""
    sql = """
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
    return sql, args


def payment_stats(db, community_id, keyword="", date_from="", date_to="", fee_item_id=0, method=""):
    """流水全量统计：笔数与合计金额（不随分页截断，页面"合计"以这里为准）。"""
    where, args = _payment_where(community_id, keyword, date_from, date_to, fee_item_id, method)
    row = query_one(db, "SELECT COUNT(*) AS cnt, COALESCE(SUM(p.amount),0) AS sum_fen" + where, args)
    return {"count": row["cnt"], "sum_fen": row["sum_fen"]}


def payment_records(db, community_id, keyword="", date_from="", date_to="", fee_item_id=0, method="",
                    page=0, page_size=BILL_PAGE_SIZE):
    """缴费流水列表。page>0 时按页返回；page=0 返回全部（CSV 导出用）。"""
    where, args = _payment_where(community_id, keyword, date_from, date_to, fee_item_id, method)
    sql = """
        SELECT p.*, b.period, f.name AS item_name,
               h.id AS house_id, h.unit, h.room_no, bd.code AS building_code,
               r.name AS owner_name
        """ + where
    if page:
        sql += " ORDER BY p.pay_date DESC, p.id DESC LIMIT ? OFFSET ?"
        args = args + [page_size, (page - 1) * page_size]
    else:
        sql += " ORDER BY p.pay_date DESC, p.id DESC"
    rows = [dict(r) for r in query_all(db, sql, args)]
    for r in rows:
        r["house_label"] = house_label(r["building_code"], r["unit"], r["room_no"])
    return rows
