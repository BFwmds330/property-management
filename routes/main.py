# -*- coding: utf-8 -*-
"""首页：总览仪表盘 + 待办提醒。"""
from flask import Blueprint, redirect, render_template, session, url_for

from database import get_db, query_all
from routes.helpers import cur_community, safe
from services import report_service

main_bp = Blueprint("main", __name__)


@main_bp.route("/")
@safe
def dashboard(db):
    cur = cur_community(db)
    if not cur:
        return redirect(url_for("community.community_add"))
    data = report_service.dashboard(db, cur["id"])
    # 首页最近 5 笔收款流水
    recent = query_all(db, """
        SELECT p.pay_date, p.amount, p.method, f.name AS item_name, b.period,
               h.unit, h.room_no, bd.code AS building_code
        FROM payment p
        JOIN bill b ON b.id = p.bill_id
        JOIN fee_item f ON f.id = b.fee_item_id
        JOIN house h ON h.id = b.house_id
        JOIN building bd ON bd.id = h.building_id
        WHERE h.community_id = ?
        ORDER BY p.id DESC LIMIT 5""", (cur["id"],))
    return render_template("dashboard.html", data=data,
                           recent=[dict(r) for r in recent], active_nav="dashboard")
