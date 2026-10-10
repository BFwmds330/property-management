# -*- coding: utf-8 -*-
"""局域网同步 API（v2.9.1）：供物业管家安卓版与 PC 之间双向同步数据库。

安全模型（与系统「个人自用」定位一致）：
- PC 设置了启动密码时，请求必须携带 X-Wuye-Pin 头（与设置页同一套 PBKDF2 校验）；
  未设密码时按局域网可信假设放行（与「手机访问.bat」同级，app.py 的浏览器会话拦截
  对 /sync/* 豁免，由本文件的头部校验接管）；
- 推送 = 上传库覆盖本机库：复用 restore_from_file（校验 + 恢复前自动备份 + 表结构补齐）；
- 拉取 = sqlite backup API 一致性快照，数据库正在写入时也能安全导出；
- 所有端点返回 JSON（错误用 401/400 状态码，不走页面的 flash+redirect）；
- 每次同步动作都写操作日志，可审计。
"""
import io
import os
from datetime import datetime

from flask import Blueprint, jsonify, request, send_file

from config import DB_PATH, VERSION
from database import get_db, log_op, query_one
from services import system_service
from utils import UserError

sync_bp = Blueprint("sync", __name__)


def _pin_ok():
    """已设启动密码时校验 X-Wuye-Pin 头；未设密码直接放行。"""
    stored = system_service.get_pin_hash(get_db())
    if not stored:
        return True
    pin = request.headers.get("X-Wuye-Pin") or ""
    return bool(pin) and system_service.verify_pin_value(pin, stored)


def _counts():
    """给安卓版同步页看的双方数据摘要。"""
    db = get_db()

    def one(sql):
        return query_one(db, sql)[0]

    return {
        "role": "pc",
        "version": VERSION,
        "db_size": os.path.getsize(DB_PATH) if os.path.exists(DB_PATH) else 0,
        "communities": one("SELECT COUNT(*) FROM community"),
        "houses": one("SELECT COUNT(*) FROM house"),
        "residents": one("SELECT COUNT(*) FROM resident"),
        "bills": one("SELECT COUNT(*) FROM bill"),
        "bills_unpaid": one("SELECT COUNT(*) FROM bill WHERE status='unpaid'"),
        "payments": one("SELECT COUNT(*) FROM payment"),
    }


@sync_bp.route("/sync/info")
def sync_info():
    if not _pin_ok():
        return jsonify(ok=False, error="需要启动密码（请在安卓版同步设置中填写）"), 401
    info = _counts()
    info["ok"] = True
    return jsonify(info)


@sync_bp.route("/sync/pull")
def sync_pull():
    """安卓版拉取 PC 数据的一致性快照。

    v2.9.2：改为读入内存后用 BytesIO 响应——此前 send_file 直接引用临时文件，
    Windows 下响应结束后句柄可能未释放，导致 .sync_pull_tmp 残留在 data/ 目录。
    """
    if not _pin_ok():
        return jsonify(ok=False, error="需要启动密码（请在安卓版同步设置中填写）"), 401
    tmp = DB_PATH + ".sync_pull_tmp"
    try:
        if os.path.exists(tmp):
            os.remove(tmp)          # 清理历史版本可能残留的临时文件
    except OSError:
        pass
    system_service.snapshot_db(tmp)
    try:
        with open(tmp, "rb") as f:
            payload = f.read()
    finally:
        try:
            os.remove(tmp)
        except OSError:
            pass
    name = "wuye_pc_%s.db" % datetime.now().strftime("%Y%m%d_%H%M%S")
    db = get_db()
    log_op(db, "系统", "同步拉取", "安卓版拉取了 PC 数据快照（%s）" % name)
    db.commit()
    return send_file(io.BytesIO(payload), as_attachment=True, download_name=name,
                     mimetype="application/octet-stream")


@sync_bp.route("/sync/push", methods=["POST"])
def sync_push():
    """安卓版推送其数据库覆盖 PC（先校验，推送前旧数据自动备份）。"""
    if not _pin_ok():
        return jsonify(ok=False, error="需要启动密码（请在安卓版同步设置中填写）"), 401
    body = request.get_data(cache=False)
    if not body:
        return jsonify(ok=False, error="推送内容为空"), 400
    tmp = DB_PATH + ".sync_push_tmp"
    try:
        with open(tmp, "wb") as f:
            f.write(body)
        backup_name = system_service.restore_from_file(tmp)
    except UserError as e:
        return jsonify(ok=False, error=str(e)), 400
    finally:
        try:
            os.remove(tmp)
        except OSError:
            pass
    db = get_db()
    log_op(db, "系统", "同步推送", "已接收安卓版推送的数据（推送前旧数据已备份为 %s）" % backup_name)
    db.commit()
    result = _counts()
    result.update(ok=True, backup=backup_name)
    return jsonify(result)
