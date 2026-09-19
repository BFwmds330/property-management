# -*- coding: utf-8 -*-
"""系统服务：备份、恢复、自动备份、演示数据的载入与清空、报修登记。"""
import os
import shutil
import sqlite3
from datetime import date, datetime, timedelta

from config import AUTO_BACKUP_KEEP, BACKUP_DIR, DB_PATH
from database import ensure_schema, log_op, open_db, query_all, query_one, scalar
from utils import UserError, clean_str, parse_int

REQUIRED_TABLES = {"community", "building", "house_type", "house", "resident",
                   "resident_house", "fee_item", "bill", "payment", "operation_log"}

# ---------------------------------------------------------------- 自动备份

def auto_backup_if_needed():
    """每天首次启动时自动备份一次，最多保留 AUTO_BACKUP_KEEP 份。"""
    try:
        if not os.path.isfile(DB_PATH) or os.path.getsize(DB_PATH) == 0:
            return None
        os.makedirs(BACKUP_DIR, exist_ok=True)
        name = "自动备份_%s.db" % date.today().strftime("%Y%m%d")
        target = os.path.join(BACKUP_DIR, name)
        if not os.path.exists(target):
            shutil.copy2(DB_PATH, target)
            _prune_backups()
        return target
    except OSError:
        return None


def _prune_backups():
    files = [os.path.join(BACKUP_DIR, f) for f in os.listdir(BACKUP_DIR)
             if f.endswith(".db")]
    files.sort(key=os.path.getmtime, reverse=True)
    for f in files[AUTO_BACKUP_KEEP:]:
        try:
            os.remove(f)
        except OSError:
            pass


def list_backups():
    files = []
    if os.path.isdir(BACKUP_DIR):
        for f in os.listdir(BACKUP_DIR):
            if f.endswith(".db"):
                p = os.path.join(BACKUP_DIR, f)
                files.append({"name": f, "size": os.path.getsize(p),
                              "mtime": datetime.fromtimestamp(os.path.getmtime(p)).strftime("%Y-%m-%d %H:%M")})
    files.sort(key=lambda x: x["mtime"], reverse=True)
    return files


# ---------------------------------------------------------------- 手动备份 / 恢复

def backup_to_file():
    """把当前数据库复制一份到 data/backups（恢复前自动备份也用它）。"""
    os.makedirs(BACKUP_DIR, exist_ok=True)
    name = "手动备份_%s.db" % datetime.now().strftime("%Y%m%d_%H%M%S")
    target = os.path.join(BACKUP_DIR, name)
    shutil.copy2(DB_PATH, target)
    _prune_backups()
    return target, name


def validate_backup_file(path):
    """校验上传的备份文件确实是本系统的数据库。返回错误信息或 None。"""
    try:
        with open(path, "rb") as f:
            header = f.read(16)
        if not header.startswith(b"SQLite format 3"):
            return "这不是一个数据库文件：请上传本系统“备份数据”下载得到的 .db 文件"
        db = sqlite3.connect(path)
        try:
            tables = {r[0] for r in db.execute(
                "SELECT name FROM sqlite_master WHERE type='table'")}
            missing = REQUIRED_TABLES - tables
            if missing:
                return "这个数据库文件不是本系统的数据文件（缺少数据表：%s）" % "、".join(sorted(missing))
        finally:
            db.close()
    except sqlite3.Error:
        return "文件损坏或不是有效的数据库文件，无法用于恢复"
    return None


def restore_from_file(upload_path):
    """用上传的备份文件覆盖当前数据。恢复前自动备份当前数据。

    返回 (恢复前备份文件名) 或抛 UserError。
    """
    err = validate_backup_file(upload_path)
    if err:
        raise UserError(err)
    _, name = backup_to_file()
    # 覆盖主数据库文件；清理可能残留的 -wal/-shm
    for suffix in ("-wal", "-shm"):
        side = DB_PATH + suffix
        if os.path.exists(side):
            try:
                os.remove(side)
            except OSError:
                pass
    shutil.copy2(upload_path, DB_PATH)
    db = open_db()
    try:
        ensure_schema(db)   # 兼容旧备份：补齐新增的表/索引
        log_op(db, "系统", "恢复数据", "从备份文件恢复数据（恢复前的数据已备份为 %s）" % name)
        db.commit()
    finally:
        db.close()
    return name


# ---------------------------------------------------------------- 演示数据

def demo_communities(db):
    return [dict(r) for r in query_all(db, "SELECT * FROM community WHERE is_demo=1 ORDER BY id")]


def load_demo_data():
    """载入一套完整的演示数据（2 个小区、楼栋房屋、20+ 住户、账单与缴费）。"""
    from services.demo_data import build_demo
    db = open_db()
    try:
        existing = scalar(db, "SELECT COUNT(*) FROM community WHERE is_demo=1")
        if existing:
            raise UserError("演示数据已经载入过了，请先“清空演示数据”再重新载入")
        build_demo(db)
        log_op(db, "系统", "载入演示数据", "载入演示数据（2 个小区，仅供练习，可一键清空）", is_demo=1)
        db.commit()
    finally:
        db.close()


def clear_demo_data():
    """清空所有带演示标记的小区及其全部数据。"""
    db = open_db()
    try:
        rows = query_all(db, "SELECT id, name FROM community WHERE is_demo=1")
        if not rows:
            raise UserError("当前没有演示数据")
        names = "、".join(r["name"] for r in rows)
        for r in rows:
            db.execute("DELETE FROM community WHERE id=?", (r["id"],))
        # 清理不再挂在任何房屋上的演示住户
        db.execute("""DELETE FROM resident WHERE id NOT IN
                      (SELECT DISTINCT resident_id FROM resident_house)""")
        db.execute("DELETE FROM operation_log WHERE is_demo=1")
        log_op(db, "系统", "清空演示数据", "已清空演示数据（%s）" % names)
        db.commit()
    finally:
        db.close()
    return names


# ---------------------------------------------------------------- 报修

REPAIR_FLOW = {"pending": "processing", "processing": "done"}


def list_repairs(db, community_id, status=""):
    sql = """
        SELECT rp.*, h.unit, h.room_no, b.code AS building_code
        FROM repair rp
        LEFT JOIN house h ON h.id = rp.house_id
        LEFT JOIN building b ON b.id = h.building_id
        WHERE rp.community_id = ?"""
    args = [community_id]
    if status:
        sql += " AND rp.status = ?"
        args.append(status)
    sql += " ORDER BY CASE rp.status WHEN 'pending' THEN 0 WHEN 'processing' THEN 1 ELSE 2 END, rp.id DESC"
    rows = [dict(r) for r in query_all(db, sql, args)]
    for r in rows:
        if r["house_id"]:
            r["house_label"] = "%s栋%d单元%d室" % (
                str(r["building_code"]).replace("#", ""), r["unit"], r["room_no"])
        else:
            r["house_label"] = "（未指定房屋）"
    return rows


def add_repair(db, community_id, form):
    content = clean_str(form.get("content"), "报修内容", 500, required=True)
    house_id = parse_int(form.get("house_id"), "房屋", 1, 10**9, required=False, default=None) or None
    if house_id:
        h = query_one(db, "SELECT community_id FROM house WHERE id=?", (house_id,))
        if not h or h["community_id"] != community_id:
            raise UserError("所选房屋不属于当前小区")
    db.execute(
        "INSERT INTO repair (community_id, house_id, content) VALUES (?,?,?)",
        (community_id, house_id, content))
    log_op(db, "报修", "新增报修", "新增报修登记：%s" % content[:50])


def update_repair_status(db, rid, action):
    r = query_one(db, "SELECT * FROM repair WHERE id=?", (rid,))
    if not r:
        raise UserError("没有找到这条报修记录，可能已被删除，请刷新页面")
    if action == "start":
        if r["status"] != "pending":
            raise UserError("只有“待处理”的报修才能开始处理")
        new_status = "processing"
    elif action == "finish":
        if r["status"] != "processing":
            raise UserError("只有“处理中”的报修才能标记完成")
        new_status = "done"
    else:
        raise UserError("操作不正确")
    db.execute("UPDATE repair SET status=?, updated_at=datetime('now','localtime') WHERE id=?",
               (new_status, rid))
    log_op(db, "报修", "更新报修状态", "报修 #%d 状态更新为 %s" % (rid, new_status))


def update_repair_note(db, rid, form):
    r = query_one(db, "SELECT id FROM repair WHERE id=?", (rid,))
    if not r:
        raise UserError("没有找到这条报修记录，可能已被删除，请刷新页面")
    note = clean_str(form.get("handler_note"), "处理备注", 500)
    db.execute("UPDATE repair SET handler_note=?, updated_at=datetime('now','localtime') WHERE id=?",
               (note, rid))


def delete_repair(db, rid):
    r = query_one(db, "SELECT * FROM repair WHERE id=?", (rid,))
    if not r:
        raise UserError("没有找到这条报修记录，可能已被删除，请刷新页面")
    db.execute("DELETE FROM repair WHERE id=?", (rid,))
    log_op(db, "报修", "删除报修记录", "删除报修记录 #%d" % rid)
