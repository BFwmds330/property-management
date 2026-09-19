# -*- coding: utf-8 -*-
"""物业管家（个人版）——应用入口。

双击 启动.bat 后由本文件启动服务：
- 自动建库建表（data/property.db）；
- 自动找一个空闲端口；
- 启动后自动打开浏览器进入系统首页。
"""
import os
import secrets
import socket
import threading
import webbrowser

from flask import Flask, g, redirect, render_template, request, session, url_for

import database
from config import BASE_DIR, DATA_DIR, DEFAULT_PORT, MAX_CONTENT_LENGTH, ensure_dirs
from utils import (BILL_STATUS, BILL_STATUS_COLOR, CYCLE_NAME, HOUSE_STATUS,
                   HOUSE_STATUS_COLOR, PAY_METHODS, PRICING_NAME, RELATIONS,
                   REPAIR_STATUS, REPAIR_STATUS_COLOR, ROLE, fmt_area, fmt_money,
                   today_str)


def _load_secret_key():
    """会话密钥：第一次运行时生成并保存，之后一直用同一个。"""
    os.makedirs(DATA_DIR, exist_ok=True)
    key_file = os.path.join(DATA_DIR, "secret.key")
    try:
        with open(key_file, "r", encoding="utf-8") as f:
            key = f.read().strip()
        if key:
            return key
    except OSError:
        pass
    key = secrets.token_hex(32)
    with open(key_file, "w", encoding="utf-8") as f:
        f.write(key)
    return key


def _free_port(start):
    """从 start 开始找一个空闲端口。"""
    port = start
    for _ in range(20):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind(("127.0.0.1", port))
                return port
            except OSError:
                port += 1
    return start


def _lan_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except OSError:
        return "127.0.0.1"


def create_app():
    ensure_dirs()
    database.init_db()

    app = Flask(__name__)
    app.config["SECRET_KEY"] = _load_secret_key()
    app.config["MAX_CONTENT_LENGTH"] = MAX_CONTENT_LENGTH
    app.config["SESSION_COOKIE_HTTPONLY"] = True

    # 每次请求结束后自动提交并关闭数据库连接
    app.teardown_appcontext(database.close_db)

    from routes.main import main_bp
    from routes.community import community_bp
    from routes.house import house_bp, building_bp, htype_bp
    from routes.resident import resident_bp
    from routes.fee import fee_bp
    from routes.report import report_bp
    from routes.repair import repair_bp
    from routes.system import system_bp
    for bp in (main_bp, community_bp, house_bp, building_bp, htype_bp,
               resident_bp, fee_bp, report_bp, repair_bp, system_bp):
        app.register_blueprint(bp)

    # ------------------------------------------------ 模板辅助函数 / 过滤器
    app.jinja_env.filters["money"] = fmt_money
    app.jinja_env.filters["area"] = fmt_area

    @app.context_processor
    def inject_globals():
        def v(field, default=""):
            """表单校验失败后，回填用户上一次输入的值。"""
            last = getattr(g, "last_form", None) or {}
            val = last.get(field, "")
            return val if val != "" else default

        def sel(field, value, default=""):
            return "selected" if str(v(field, default)) == str(value) else ""

        def chk(field, default=False):
            val = getattr(g, "last_form", None) or {}
            if field in val:
                return "checked" if val[field] else ""
            return "checked" if default else ""

        return dict(
            v=v, sel=sel, chk=chk,
            HOUSE_STATUS=HOUSE_STATUS, HOUSE_STATUS_COLOR=HOUSE_STATUS_COLOR,
            BILL_STATUS=BILL_STATUS, BILL_STATUS_COLOR=BILL_STATUS_COLOR,
            ROLE=ROLE, RELATIONS=RELATIONS, PAY_METHODS=PAY_METHODS,
            CYCLE_NAME=CYCLE_NAME, PRICING_NAME=PRICING_NAME,
            REPAIR_STATUS=REPAIR_STATUS, REPAIR_STATUS_COLOR=REPAIR_STATUS_COLOR,
            money=fmt_money, area_fmt=fmt_area, today=today_str(),
        )

    @app.before_request
    def prepare_request():
        # 上一次表单校验失败的回填数据（只在与失败页面相同的路径生效一次）
        if "_last_form" in session:
            if session.get("_last_form_path") == request.path:
                g.last_form = session.pop("_last_form")
                session.pop("_last_form_path", None)
            else:
                g.last_form = {}
        else:
            g.last_form = {}

    @app.context_processor
    def inject_community():
        """所有页面都能拿到“当前小区”和小区列表（顶部切换器用）。"""
        from routes.helpers import cur_community
        db = database.get_db()
        cur = cur_community(db)
        communities = [dict(c) for c in database.query_all(
            db, "SELECT id, name, is_demo FROM community ORDER BY is_demo, id")]
        return dict(cur=cur, communities=communities)

    @app.after_request
    def no_cache(resp):
        # 本机自用：禁止缓存，避免改完数据浏览器还显示旧页面
        resp.headers["Cache-Control"] = "no-store"
        return resp

    # ------------------------------------------------ 友好的错误页面
    @app.errorhandler(404)
    def page_not_found(e):
        return render_template("error.html", code=404,
                               msg="您访问的页面不存在，可能已被移动或删除。"), 404

    @app.errorhandler(413)
    def too_large(e):
        return render_template("error.html", code=413,
                               msg="上传的文件太大了，请确认上传的是本系统导出的 .db 备份文件。"), 413

    @app.errorhandler(500)
    def server_error(e):
        return render_template("error.html", code=500,
                               msg="系统出了点小问题，已自动记录。您可以返回首页重试；"
                                   "如果反复出现，请通过“设置 → 备份数据”先保存数据。"), 500

    return app


def main():
    # 启动时自动做一次当日备份（数据安全的双保险）
    try:
        from services.system_service import auto_backup_if_needed
        auto_backup_if_needed()
    except Exception:
        pass

    app = create_app()
    port = _free_port(int(os.environ.get("WUYE_PORT", DEFAULT_PORT)))
    url = "http://127.0.0.1:%d" % port

    print("=" * 56)
    print("  物业管家（个人版）已启动")
    print("  本机访问：%s" % url)
    print("  手机访问（需同一 WiFi）：http://%s:%d" % (_lan_ip(), port))
    print("  数据保存在：data 文件夹里，关闭本窗口即退出系统")
    print("=" * 56)

    if os.environ.get("WUYE_NO_BROWSER") != "1":
        threading.Timer(1.2, lambda: webbrowser.open(url)).start()
    if os.environ.get("WUYE_FAST_EXIT") == "1":     # 自动化测试用：启动后自动退出
        threading.Timer(3.0, lambda: os._exit(0)).start()

    app.run(host="0.0.0.0", port=port, debug=False, use_reloader=False)


if __name__ == "__main__":
    main()
