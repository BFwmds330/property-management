# -*- coding: utf-8 -*-
"""统计报表：总览仪表盘、应收实收报表、欠费 Top10、各小区收缴率对比。"""
from datetime import date, timedelta

from database import query_all, query_one, scalar
from utils import fmt_money, house_label, this_month

LEASE_SOON_DAYS = 30
BIRTHDAY_SOON_DAYS = 30


def dashboard(db, community_id):
    """首页总览 + 待办提醒。"""
    month = this_month()
    total_houses = scalar(db, "SELECT COUNT(*) FROM house WHERE community_id=?", (community_id,))
    occupied = scalar(db, "SELECT COUNT(*) FROM house WHERE community_id=? AND status IN ('self','rent')",
                      (community_id,))
    # 本月账单（按账期首月 == 当前月）
    row = query_one(db, """
        SELECT COALESCE(SUM(b.amount_receivable),0) AS recv,
               COALESCE(SUM(b.amount_received),0) AS got,
               COUNT(*) AS cnt
        FROM bill b JOIN house h ON h.id = b.house_id
        WHERE h.community_id = ? AND b.period_start = ? AND b.status != 'void'""",
        (community_id, month))
    month_recv, month_got, month_cnt = row["recv"], row["got"], row["cnt"]
    # 欠费（全部账期）
    owed = query_one(db, """
        SELECT COALESCE(SUM(b.amount_receivable - b.amount_received),0) AS total,
               COUNT(DISTINCT b.house_id) AS houses
        FROM bill b JOIN house h ON h.id = b.house_id
        WHERE h.community_id = ? AND b.status IN ('unpaid','partial')""", (community_id,))
    # 待办：本月未缴清账单数
    todo_bills = scalar(db, """
        SELECT COUNT(*) FROM bill b JOIN house h ON h.id=b.house_id
        WHERE h.community_id=? AND b.period_start=? AND b.status IN ('unpaid','partial')""",
        (community_id, month))
    # 待办：30 天内到期的租约
    today = date.today()
    end = today + timedelta(days=LEASE_SOON_DAYS)
    leases = [dict(r) for r in query_all(db, """
        SELECT rh.*, r.name, r.phone, h.unit, h.room_no, b.code AS building_code
        FROM resident_house rh
        JOIN resident r ON r.id = rh.resident_id
        JOIN house h ON h.id = rh.house_id
        JOIN building b ON b.id = h.building_id
        WHERE h.community_id = ? AND rh.role='tenant' AND rh.is_current=1
          AND rh.lease_end != '' AND rh.lease_end >= ? AND rh.lease_end <= ?
        ORDER BY rh.lease_end""", (community_id, today.isoformat(), end.isoformat()))]
    # 待办：30 天内的住户生日
    birthdays = _upcoming_birthdays(db, community_id, today, BIRTHDAY_SOON_DAYS)
    # 待办：未处理报修
    repairs = scalar(db, "SELECT COUNT(*) FROM repair WHERE community_id=? AND status IN ('pending','processing')",
                     (community_id,))
    # 人员模块：在职人数、本月工资/社保是否已录入、30 天内合同到期
    staff_active = scalar(db, "SELECT COUNT(*) FROM staff WHERE status IN ('active','probation')")
    salary_month_done = scalar(db, "SELECT COUNT(*) FROM staff_salary WHERE year=? AND month=?",
                               (int(month[:4]), int(month[5:7])))
    social_month_done = scalar(db, "SELECT COUNT(*) FROM staff_social WHERE year=? AND month=?",
                               (int(month[:4]), int(month[5:7])))
    contracts = [dict(r) for r in query_all(db, """
        SELECT name, department, contract_end FROM staff
        WHERE status IN ('active','probation') AND contract_end != ''
          AND contract_end >= ? AND contract_end <= ?
        ORDER BY contract_end""", (today.isoformat(), end.isoformat()))]
    return {
        "total_houses": total_houses, "occupied": occupied,
        "occupancy": round(occupied * 100 / total_houses) if total_houses else 0,
        "month_recv_fen": month_recv, "month_got_fen": month_got, "month_bill_count": month_cnt,
        "month_rate": round(month_got * 100 / month_recv) if month_recv else None,
        "owed_fen": owed["total"], "owed_houses": owed["houses"],
        "todo_bills": todo_bills, "leases": leases, "birthdays": birthdays, "repairs": repairs,
        "month": month,
        "staff_active": staff_active, "salary_month_done": salary_month_done,
        "social_month_done": social_month_done, "contracts": contracts,
    }


def _upcoming_birthdays(db, community_id, today, days):
    end = today + timedelta(days=days)
    rows = query_all(db, """
        SELECT r.name, r.birth_date, r.phone, rh.role, h.unit, h.room_no, b.code AS building_code
        FROM resident_house rh
        JOIN resident r ON r.id = rh.resident_id
        JOIN house h ON h.id = rh.house_id
        JOIN building b ON b.id = h.building_id
        WHERE h.community_id = ? AND rh.is_current=1 AND r.birth_date != ''
              AND r.birth_date LIKE '____-__-__'""", (community_id,))
    out = []
    for r in rows:
        try:
            b = date.fromisoformat(r["birth_date"])
        except ValueError:
            continue
        # 取今年的生日；若今年已过，看明年
        for y in (today.year, today.year + 1):
            try:
                nb = b.replace(year=y)
            except ValueError:      # 2月29日
                nb = date(y, 3, 1)
            if today <= nb <= end:
                out.append({
                    "name": r["name"], "date": nb.isoformat(), "phone": r["phone"], "role": r["role"],
                    "label": house_label(r["building_code"], r["unit"], r["room_no"]),
                })
                break
    out.sort(key=lambda x: x["date"])
    return out[:10]


def _range(month="", year=""):
    """把 月/年 参数换算成账期首月范围 [start, end]。"""
    if year:
        return "%s-01" % year, "%s-12" % year, "%s 年度" % year
    m = month or this_month()
    return m, m, "%s 月" % m


def report(db, community_id=0, month="", year=""):
    """报表：应收/实收/收缴率 + 按项目明细 + 欠费Top10 + 各小区对比。

    community_id=0 时统计全部小区（用于“各小区对比”视角）。
    """
    start, end, label = _range(month, year)
    cond_cid = "AND h.community_id = ?" if community_id else ""
    args = ([community_id] if community_id else []) + [start, end]

    summary = query_one(db, """
        SELECT COALESCE(SUM(b.amount_receivable),0) AS recv,
               COALESCE(SUM(b.amount_received),0) AS got,
               COUNT(*) AS cnt
        FROM bill b JOIN house h ON h.id = b.house_id
        WHERE 1=1 %s AND b.period_start >= ? AND b.period_start <= ? AND b.status != 'void'""" % cond_cid,
        args)
    by_item = [dict(r) for r in query_all(db, """
        SELECT f.name AS item_name,
               COALESCE(SUM(b.amount_receivable),0) AS recv,
               COALESCE(SUM(b.amount_received),0) AS got,
               COUNT(*) AS cnt
        FROM bill b JOIN house h ON h.id = b.house_id JOIN fee_item f ON f.id = b.fee_item_id
        WHERE 1=1 %s AND b.period_start >= ? AND b.period_start <= ? AND b.status != 'void'
        GROUP BY f.id ORDER BY f.id""" % cond_cid, args)]
    top_owed = [dict(r) for r in query_all(db, """
        SELECT h.id AS house_id, h.unit, h.room_no, bd.code AS building_code,
               MAX(r.name) AS owner_name,
               SUM(b.amount_receivable - b.amount_received) AS owed_fen,
               COUNT(*) AS cnt
        FROM bill b JOIN house h ON h.id = b.house_id JOIN building bd ON bd.id = h.building_id
        LEFT JOIN resident r ON r.id = h.owner_resident_id
        WHERE 1=1 %s AND b.period_start >= ? AND b.period_start <= ?
              AND b.status IN ('unpaid','partial')
        GROUP BY h.id HAVING owed_fen > 0
        ORDER BY owed_fen DESC LIMIT 10""" % cond_cid, args)]
    communities = [dict(r) for r in query_all(db, """
        SELECT c.id, c.name,
               COALESCE(SUM(b.amount_receivable),0) AS recv,
               COALESCE(SUM(b.amount_received),0) AS got
        FROM community c
        LEFT JOIN house h ON h.community_id = c.id
        LEFT JOIN bill b ON b.house_id = h.id AND b.period_start >= ? AND b.period_start <= ?
              AND b.status != 'void'
        GROUP BY c.id ORDER BY c.id""", (start, end))]
    for r in top_owed:
        r["house_label"] = house_label(r["building_code"], r["unit"], r["room_no"])
    return {
        "label": label, "start": start, "end": end,
        "recv_fen": summary["recv"], "got_fen": summary["got"], "cnt": summary["cnt"],
        "rate": round(summary["got"] * 100 / summary["recv"]) if summary["recv"] else None,
        "by_item": by_item, "top_owed": top_owed, "communities": communities,
    }


def community_switch_list(db):
    """顶部小区切换器用的列表。"""
    return [dict(r) for r in query_all(db, "SELECT id, name, is_demo FROM community ORDER BY is_demo, id")]
