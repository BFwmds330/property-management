# -*- coding: utf-8 -*-
"""公告：小区级公告发布 / 删除（v2.3.0）。"""
from flask import Blueprint, flash, redirect, render_template, request, url_for

from routes.helpers import cur_community, need_community, safe
from services import notice_service

notice_bp = Blueprint("notice", __name__)


@notice_bp.route("/notice")
@safe
def notice_list(db):
    cur = cur_community(db)
    if not cur:
        return need_community()
    rows = notice_service.list_notices(db, cur["id"])
    return render_template("notice/list.html", rows=rows, active_nav="notice")


@notice_bp.route("/notice/add", methods=["POST"])
@safe
def notice_add(db):
    cur = cur_community(db)
    if not cur:
        return need_community()
    notice_service.add_notice(db, cur["id"], request.form)
    db.commit()
    flash("公告已发布", "success")
    return redirect(url_for("notice.notice_list"))


@notice_bp.route("/notice/<int:nid>/delete", methods=["POST"])
@safe
def notice_delete(db, nid):
    notice_service.delete_notice(db, nid)
    db.commit()
    flash("公告已删除", "success")
    return redirect(url_for("notice.notice_list"))
