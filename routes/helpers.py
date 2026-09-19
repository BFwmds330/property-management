# -*- coding: utf-8 -*-
"""路由层的公共小工具：安全包装、当前小区。"""
import traceback
from functools import wraps
from urllib.parse import urlparse

from flask import flash, g, redirect, request, session, url_for

from database import get_db, query_one
from utils import UserError


def cur_community(db):
    """当前小区：优先取会话里记住的；没有就取第一个小区。"""
    cid = session.get("cid")
    if cid:
        row = query_one(db, "SELECT * FROM community WHERE id=?", (cid,))
        if row:
            return row
    row = query_one(db, "SELECT * FROM community ORDER BY is_demo, id LIMIT 1")
    if row:
        session["cid"] = row["id"]
    return row


def need_community():
    """没有小区时统一引导用户先去创建小区。"""
    flash("请先创建您的小区，再进行其他操作", "warning")
    return redirect(url_for("community.community_add"))


def _back():
    """校验失败后返回来源页，并保留用户刚填的内容。"""
    back = request.form.get("_back") or request.headers.get("Referer") or url_for("main.dashboard")
    if back.startswith("http"):
        p = urlparse(back)
        back = p.path + (("?" + p.query) if p.query else "")
    if not back.startswith("/"):
        back = url_for("main.dashboard")
    session["_last_form"] = {k: val for k, val in request.form.to_dict().items()}
    session["_last_form_path"] = urlparse(back).path
    return redirect(back)


def safe(fn):
    """统一兜底：业务错误显示中文提示；意外错误也不让程序崩溃。"""
    @wraps(fn)
    def wrapper(*args, **kwargs):
        db = get_db()
        try:
            return fn(db, *args, **kwargs)
        except UserError as e:
            db.rollback()
            flash(str(e), "danger")
            return _back()
        except Exception:
            db.rollback()
            traceback.print_exc()
            flash("系统出了点小问题，刚才的操作没有保存成功，请重试；"
                  "如果反复出现，请联系开发者（设置页有日志说明）", "danger")
            return _back()
    return wrapper
