# -*- coding: utf-8 -*-
"""公告（v2.3.0）：小区级公告，置顶优先，删除留痕。"""
from database import log_op, query_all, query_one
from utils import UserError, clean_str


def list_notices(db, community_id, limit=100):
    rows = query_all(db, """
        SELECT * FROM notice WHERE community_id=?
        ORDER BY pinned DESC, id DESC LIMIT ?""", (community_id, limit))
    return [dict(r) for r in rows]


def recent_notices(db, community_id, limit=3):
    rows = query_all(db, """
        SELECT id, title, pinned, created_at FROM notice WHERE community_id=?
        ORDER BY pinned DESC, id DESC LIMIT ?""", (community_id, limit))
    return [dict(r) for r in rows]


def add_notice(db, community_id, form):
    title = clean_str(form.get("title"), "公告标题", 100, required=True)
    content = clean_str(form.get("content"), "公告内容", 2000)
    pinned = 1 if form.get("pinned") else 0
    db.execute("INSERT INTO notice (community_id, title, content, pinned) VALUES (?,?,?,?)",
               (community_id, title, content, pinned))
    log_op(db, "公告", "发布公告", "发布公告「%s」%s" % (title, "（置顶）" if pinned else ""))


def delete_notice(db, nid):
    row = query_one(db, "SELECT * FROM notice WHERE id=?", (nid,))
    if not row:
        raise UserError("没有找到这条公告，可能已被删除，请刷新页面")
    db.execute("DELETE FROM notice WHERE id=?", (nid,))
    log_op(db, "公告", "删除公告", "删除公告「%s」" % row["title"])
