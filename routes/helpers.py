# -*- coding: utf-8 -*-
"""路由层的公共小工具：安全包装、当前小区、分页。"""
import math
import traceback
from functools import wraps
from urllib.parse import urlparse, urlencode

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


def build_pagination(request_args, page, total, page_size):
    """列表页分页参数：页码修正、总页数、页码按钮列表、翻页链接。

    返回 (page, pages, page_list, page_url)。page_list 里的 0 表示"…"省略号；
    page_url 是一个 (页码 -> 链接) 的函数，自动保留当前所有筛选参数。
    """
    pages = max(1, math.ceil(total / page_size)) if total else 1
    page = min(max(1, page), pages)
    qs = urlencode({k: v for k, v in request_args.items()
                    if k != "page" and str(v).strip() != ""})

    def page_url(p):
        return ("?" + qs + "&" if qs else "?") + "page=%d" % p

    if pages <= 7:
        page_list = list(range(1, pages + 1))
    else:
        page_list = [1]
        lo, hi = max(2, page - 2), min(pages - 1, page + 2)
        if lo > 2:
            page_list.append(0)
        page_list += list(range(lo, hi + 1))
        if hi < pages - 1:
            page_list.append(0)
        page_list.append(pages)
    return page, pages, page_list, page_url
