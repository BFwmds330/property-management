# -*- coding: utf-8 -*-
"""小区管理：列表 / 新增 / 修改 / 删除 / 切换。"""
from flask import redirect, flash, render_template, request, session, url_for, Blueprint

from routes.helpers import cur_community, safe
from services import community_service

community_bp = Blueprint("community", __name__)


@community_bp.route("/community")
@community_bp.route("/community/list")
@safe
def community_list(db):
    keyword = request.args.get("keyword", "")
    rows = community_service.list_communities(db, keyword)
    return render_template("community/list.html", rows=rows, keyword=keyword,
                           active_nav="community")


@community_bp.route("/community/add")
def community_add():
    return render_template("community/form.html", community=None, active_nav="community")


@community_bp.route("/community/add", methods=["POST"])
@safe
def community_add_post(db):
    is_demo = 1 if (request.form.get("demo") == "1") else 0
    new_id = community_service.add_community(db, request.form, is_demo)
    db.commit()
    session["cid"] = new_id      # 新建后自动切换到该小区
    return redirect(url_for("main.dashboard"))


@community_bp.route("/community/edit/<int:cid>")
@safe
def community_edit(db, cid):
    row = community_service.get_community(db, cid)
    return render_template("community/form.html", community=row, active_nav="community")


@community_bp.route("/community/edit/<int:cid>", methods=["POST"])
@safe
def community_edit_post(db, cid):
    community_service.update_community(db, cid, request.form)
    db.commit()
    return redirect(url_for("community.community_list"))


@community_bp.route("/community/delete/<int:cid>", methods=["POST"])
@safe
def community_delete(db, cid):
    community_service.delete_community(db, cid)
    db.commit()
    session.pop("cid", None)
    return redirect(url_for("community.community_list"))


@community_bp.route("/community/switch/<int:cid>", methods=["POST"])
def community_switch(cid):
    session["cid"] = cid
    nxt = request.form.get("next") or url_for("main.dashboard")
    if not str(nxt).startswith("/"):
        nxt = url_for("main.dashboard")
    return redirect(nxt)
