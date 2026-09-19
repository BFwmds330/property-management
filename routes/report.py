# -*- coding: utf-8 -*-
"""报表统计：应收/实收/收缴率、欠费 Top10、各小区对比，支持导出 CSV。"""
from flask import redirect, flash, render_template, request, url_for, Blueprint

from routes.helpers import cur_community, need_community, safe
from services import report_service
from utils import csv_response

report_bp = Blueprint("report", __name__)


@report_bp.route("/report")
@safe
def report_index(db):
    cur = cur_community(db)
    if not cur:
        return need_community()
    mode = request.args.get("mode", "month")
    month = request.args.get("month", "")
    year = request.args.get("year", "")
    scope = request.args.get("scope", "current")     # current=当前小区 / all=全部小区
    if mode not in ("month", "year"):
        mode = "month"
    data = report_service.report(db, cur["id"] if scope == "current" else 0,
                                 month=month, year=year if mode == "year" else "")
    data["mode"] = mode
    data["scope"] = scope
    data["month"] = month
    data["year"] = year
    data["current_community"] = cur["name"]
    return render_template("report/index.html", data=data, active_nav="report")


def _report_for_export(db):
    month = request.args.get("month", "")
    year = request.args.get("year", "")
    scope = request.args.get("scope", "current")
    cur = cur_community(db)
    return report_service.report(db, cur["id"] if scope == "current" else 0,
                                 month=month, year=year), cur


@report_bp.route("/report/export/summary")
@safe
def export_summary(db):
    data, cur = _report_for_export(db)
    headers = ["收费项目", "应收(元)", "实收(元)", "未收(元)", "收缴率(%)", "账单笔数"]
    out = []
    for r in data["by_item"]:
        rate = round(r["got"] * 100 / r["recv"]) if r["recv"] else ""
        out.append([r["item_name"], "%.2f" % (r["recv"] / 100), "%.2f" % (r["got"] / 100),
                    "%.2f" % ((r["recv"] - r["got"]) / 100), rate, r["cnt"]])
    total_rate = round(data["got_fen"] * 100 / data["recv_fen"]) if data["recv_fen"] else ""
    out.append(["合计", "%.2f" % (data["recv_fen"] / 100), "%.2f" % (data["got_fen"] / 100),
                "%.2f" % ((data["recv_fen"] - data["got_fen"]) / 100), total_rate, data["cnt"]])
    return csv_response("收费报表_%s.csv" % data["label"].replace(" ", ""), headers, out)


@report_bp.route("/report/export/top")
@safe
def export_top(db):
    data, cur = _report_for_export(db)
    headers = ["房号", "业主", "欠费金额(元)", "欠费笔数"]
    out = [[r["house_label"], r["owner_name"] or "", "%.2f" % (r["owed_fen"] / 100), r["cnt"]]
           for r in data["top_owed"]]
    return csv_response("欠费Top10_%s.csv" % data["label"].replace(" ", ""), headers, out)


@report_bp.route("/report/export/communities")
@safe
def export_communities(db):
    data, cur = _report_for_export(db)
    headers = ["小区", "应收(元)", "实收(元)", "收缴率(%)"]
    out = []
    for r in data["communities"]:
        rate = round(r["got"] * 100 / r["recv"]) if r["recv"] else ""
        out.append([r["name"], "%.2f" % (r["recv"] / 100), "%.2f" % (r["got"] / 100), rate])
    return csv_response("各小区收缴率对比_%s.csv" % data["label"].replace(" ", ""), headers, out)
