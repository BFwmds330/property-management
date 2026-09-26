# -*- coding: utf-8 -*-
"""支出管理：物业支出 / 公共支出（公共电费、绿化、保安保洁等）的登记与汇总。

金额以「分」整数存储；支出按小区归属；删除留痕；支持按年月/分类筛选与合计。
"""
from database import log_op, query_all, query_one, scalar
from utils import STAFF_DEPARTMENTS, UserError, clean_str, parse_amount, parse_date, parse_int

CATEGORIES = ["物业支出", "公共支出"]


def add_expense(db, community_id, form):
    exp_date = parse_date(form.get("exp_date"), "支出日期", required=True)
    category = (form.get("category") or "").strip()
    if category not in CATEGORIES:
        raise UserError("支出分类只能是 物业支出 / 公共支出")
    item = clean_str(form.get("item"), "支出事由", 100, required=True)
    amount = parse_amount(form.get("amount"), "支出金额", required=True, allow_zero=False)
    remark = clean_str(form.get("remark"), "备注", 200)
    cur = db.execute(
        "INSERT INTO expense (community_id, exp_date, category, item, amount, remark) VALUES (?,?,?,?,?,?)",
        (community_id, exp_date, category, item, amount, remark))
    log_op(db, "支出", "登记支出", "%s %s「%s」%.2f 元" % (exp_date, category, item, amount / 100))
    return cur.lastrowid


def delete_expense(db, expense_id):
    row = query_one(db, "SELECT * FROM expense WHERE id=?", (expense_id,))
    if not row:
        raise UserError("没有找到这条支出记录，可能已被删除，请刷新页面")
    db.execute("DELETE FROM expense WHERE id=?", (expense_id,))
    log_op(db, "支出", "删除支出", "删除 %s %s「%s」%.2f 元" % (
        row["exp_date"], row["category"], row["item"], row["amount"] / 100))


def list_expenses(db, community_id, year=0, month=0, category=""):
    sql = """SELECT * FROM expense WHERE community_id = ?"""
    args = [community_id]
    if year:
        sql += " AND exp_date LIKE ?"
        args.append("%d-%02d%%" % (year, month) if month else "%d%%" % year)
    if category:
        sql += " AND category = ?"
        args.append(category)
    sql += " ORDER BY exp_date DESC, id DESC"
    return [dict(r) for r in query_all(db, sql, args)]


def summarize(rows):
    total = {"物业支出": 0, "公共支出": 0, "合计": 0}
    for r in rows:
        total[r["category"]] = total.get(r["category"], 0) + r["amount"]
        total["合计"] += r["amount"]
    return total
