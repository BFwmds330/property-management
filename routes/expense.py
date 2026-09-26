# -*- coding: utf-8 -*-
"""支出管理：物业支出 / 公共支出登记与汇总。"""
from flask import Blueprint, flash, redirect, render_template, request, url_for

from database import get_db
from routes.helpers import cur_community, need_community, safe
from services import expense_service
from utils import csv_response, parse_int

expense_bp = Blueprint("expense", __name__)


@expense_bp.route("/expense")
@safe
def expense_list(db):
    cur = cur_community(db)
    if not cur:
        return need_community()
    year = parse_int(request.args.get("year"), "年份", 2000, 2100, required=False, default=None) or 2026
    month = parse_int(request.args.get("month"), "月份", 0, 12, required=False, default=None) or 0
    category = request.args.get("category", "")
    rows = expense_service.list_expenses(db, cur["id"], year=year, month=month, category=category)
    total = expense_service.summarize(rows)
    return render_template("expense/list.html", rows=rows, total=total,
                           year=year, month=month, f_category=category,
                           active_nav="expense")


@expense_bp.route("/expense/add", methods=["POST"])
@safe
def expense_add(db):
    cur = cur_community(db)
    if not cur:
        return need_community()
    expense_service.add_expense(db, cur["id"], request.form)
    db.commit()
    flash("支出登记成功", "success")
    return redirect(url_for("expense.expense_list"))


@expense_bp.route("/expense/<int:expense_id>/delete", methods=["POST"])
@safe
def expense_delete(db, expense_id):
    expense_service.delete_expense(db, expense_id)
    db.commit()
    flash("支出记录已删除", "success")
    return redirect(request.referrer or url_for("expense.expense_list"))


@expense_bp.route("/expense/export")
@safe
def expense_csv(db):
    cur = cur_community(db)
    if not cur:
        return need_community()
    year = parse_int(request.args.get("year"), "年份", 2000, 2100, required=False, default=None) or 2026
    month = parse_int(request.args.get("month"), "月份", 0, 12, required=False, default=None) or 0
    category = request.args.get("category", "")
    rows = expense_service.list_expenses(db, cur["id"], year=year, month=month, category=category)
    total = expense_service.summarize(rows)
    headers = ["日期", "分类", "事由", "金额(元)", "备注"]
    out = [[r["exp_date"], r["category"], r["item"], "%.2f" % (r["amount"] / 100), r["remark"]]
           for r in rows]
    out.append(["合计", "", "", "%.2f" % (total["合计"] / 100), ""])
    return csv_response("%d 年支出明细.csv" % year, headers, out)
