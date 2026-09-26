# -*- coding: utf-8 -*-
"""设置页：备份数据、恢复数据、演示数据、操作日志、系统信息。"""
import os

from flask import (Blueprint, flash, redirect, render_template, request,
                   send_file, session, url_for)

from config import DB_PATH
from database import get_db, log_op, query_all
from routes.helpers import safe
from services import system_service
from utils import UserError

system_bp = Blueprint("system", __name__)


@system_bp.route("/settings")
@safe
def settings(db):
    demo_rows = system_service.demo_communities(db)
    logs = query_all(db, "SELECT * FROM operation_log ORDER BY id DESC LIMIT 50")
    return render_template("system/settings.html", backups=system_service.list_backups(),
                           demo_rows=demo_rows, logs=[dict(r) for r in logs],
                           db_path=DB_PATH,
                           pin_enabled=bool(system_service.get_pin_hash(db)),
                           active_nav="settings")


@system_bp.route("/settings/backup")
@safe
def backup(db):
    path, name = system_service.backup_to_file()
    log_op(db, "系统", "手动备份", "已下载数据库备份文件 %s" % name)
    flash("备份文件已生成：%s（浏览器已开始下载，请保存到 U 盘或网盘等安全位置）" % name, "success")
    resp = send_file(path, as_attachment=True, download_name=name,
                     mimetype="application/octet-stream")
    # 先提交日志再返回文件
    db.commit()
    return resp


@system_bp.route("/settings/restore", methods=["POST"])
@safe
def restore(db):
    f = request.files.get("file")
    if not f or not f.filename:
        raise UserError("请先选择要恢复的备份文件（.db）")
    filename = f.filename or ""
    if not filename.lower().endswith((".db", ".sqlite", ".sqlite3")):
        raise UserError("请上传本系统导出的 .db 备份文件（当前文件：%s）" % filename)
    tmp = DB_PATH + ".restore_tmp"
    f.save(tmp)
    restored_from = system_service.restore_from_file(tmp)
    try:
        os.remove(tmp)
    except OSError:
        pass
    flash("数据恢复成功！恢复前的旧数据已自动备份为 %s，可随时再恢复回去" % restored_from, "success")
    session.pop("cid", None)
    return redirect(url_for("system.settings"))


@system_bp.route("/settings/pin/enable", methods=["POST"])
@safe
def pin_enable(db):
    system_service.enable_pin(db, request.form)
    db.commit()
    session["pin_ok"] = True      # 操作者刚设置完密码，本会话不再询问
    flash("启动密码已开启：下次打开系统（或关闭浏览器后再开）需先输入密码", "success")
    return redirect(url_for("system.settings"))


@system_bp.route("/settings/pin/change", methods=["POST"])
@safe
def pin_change(db):
    system_service.change_pin(db, request.form)
    db.commit()
    session["pin_ok"] = True
    flash("启动密码已修改，请记住新密码", "success")
    return redirect(url_for("system.settings"))


@system_bp.route("/settings/pin/disable", methods=["POST"])
@safe
def pin_disable(db):
    system_service.disable_pin(db, request.form)
    db.commit()
    session["pin_ok"] = True
    flash("启动密码已关闭：进入系统不再需要密码", "success")
    return redirect(url_for("system.settings"))


@system_bp.route("/settings/demo/load", methods=["POST"])
@safe
def demo_load(db):
    system_service.load_demo_data()      # 内部用独立连接写入并提交
    from database import open_db, query_one
    db2 = open_db()
    try:
        row = query_one(db2, "SELECT id FROM community WHERE is_demo=1 ORDER BY id LIMIT 1")
    finally:
        db2.close()
    if row:
        session["cid"] = row["id"]
    flash("演示数据已载入！里面有两个演示小区（阳光花园、翡翠湾），随便点随便改，练习完可在本页一键清空", "success")
    return redirect(url_for("main.dashboard"))


@system_bp.route("/settings/demo/clear", methods=["POST"])
@safe
def demo_clear(db):
    names = system_service.clear_demo_data()
    db.commit()
    session.pop("cid", None)
    flash("演示数据已清空（%s），您自己的数据不受影响" % names, "success")
    return redirect(url_for("system.settings"))
