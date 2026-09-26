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
from config import (BASE_DIR, DATA_DIR, DEFAULT_PORT, EDITION, MAX_CONTENT_LENGTH,
                    VERSION, ensure_dirs)
from utils import (BILL_STATUS, BILL_STATUS_COLOR, CYCLE_NAME, HOUSE_STATUS,
                   HOUSE_STATUS_COLOR, PAY_METHODS, PRICING_NAME, RELATIONS,
                   REPAIR_STATUS, REPAIR_STATUS_COLOR, ROLE, STAFF_STATUS,
                   STAFF_STATUS_COLOR, age_from_birth, fmt_area, fmt_money,
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


def _port_in_use(port):
    """端口是否已有程序在监听：直接连一次，连得上就说明被占用。

    这是最可靠的判断方式——不管对方绑定的是 0.0.0.0 还是具体 IP，
    只要端口上有服务，连接就一定成功。旧版用“试绑定 127.0.0.1”判断，
    在 Windows 上会被 0.0.0.0 的监听“骗过”，导致两个版本开在同一个端口。
    """
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.3)
        return s.connect_ex(("127.0.0.1", port)) == 0


def _can_bind(port):
    """能否按服务器将要采用的方式（0.0.0.0）真正绑定该端口。"""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        try:
            s.bind(("0.0.0.0", port))
            return True
        except OSError:
            return False


def _pick_port(start):
    """从 start 开始找一个真正可用的端口（最多往后找 20 个，找不到返回 None）。"""
    for port in range(start, start + 21):
        if not _port_in_use(port) and _can_bind(port):
            return port
    return None


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
    # 名册导入的预览数据会以表单字段回传（600+ 户可达数 MB），放宽默认 500KB 限制
    app.config["MAX_FORM_MEMORY_SIZE"] = 32 * 1024 * 1024
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
    from routes.staff import staff_bp
    from routes.expense import expense_bp
    from routes.system import system_bp
    for bp in (main_bp, community_bp, house_bp, building_bp, htype_bp,
               resident_bp, fee_bp, report_bp, repair_bp, staff_bp, expense_bp, system_bp):
        app.register_blueprint(bp)

    # ------------------------------------------------ 模板辅助函数 / 过滤器
    app.jinja_env.filters["money"] = fmt_money
    app.jinja_env.filters["area"] = fmt_area
    app.jinja_env.filters["age"] = age_from_birth

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
            edition=EDITION, version=VERSION,
            HOUSE_STATUS=HOUSE_STATUS, HOUSE_STATUS_COLOR=HOUSE_STATUS_COLOR,
            BILL_STATUS=BILL_STATUS, BILL_STATUS_COLOR=BILL_STATUS_COLOR,
            ROLE=ROLE, RELATIONS=RELATIONS, PAY_METHODS=PAY_METHODS,
            CYCLE_NAME=CYCLE_NAME, PRICING_NAME=PRICING_NAME,
            REPAIR_STATUS=REPAIR_STATUS, REPAIR_STATUS_COLOR=REPAIR_STATUS_COLOR,
            STAFF_STATUS=STAFF_STATUS, STAFF_STATUS_COLOR=STAFF_STATUS_COLOR,
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
    start = int(os.environ.get("WUYE_PORT", DEFAULT_PORT))
    port = _pick_port(start)
    if port is None:
        print("=" * 56)
        print("  【提示】%d ~ %d 端口都被占用了。" % (start, start + 20))
        print("  请把已经打开的物业管家窗口关掉几个，再重新双击启动。")
        print("=" * 56)
        return
    url = "http://127.0.0.1:%d" % port

    edition_tag = "（%s）" % EDITION if EDITION else ""
    print("=" * 56)
    print("  物业管家（个人版）v%s%s 已启动" % (VERSION, edition_tag))
    print("  本机访问：%s" % url)
    print("  手机访问（需同一 WiFi）：http://%s:%d" % (_lan_ip(), port))
    print("  数据保存在：data 文件夹里，关闭本窗口即退出系统")
    print("=" * 56)

    if os.environ.get("WUYE_NO_BROWSER") != "1":
        threading.Timer(1.2, lambda: webbrowser.open(url)).start()
    if os.environ.get("WUYE_FAST_EXIT") == "1":     # 自动化测试用：启动后自动退出
        threading.Timer(3.0, lambda: os._exit(0)).start()

    try:
        app.run(host="0.0.0.0", port=port, debug=False, use_reloader=False)
    except OSError:
        print("【提示】端口 %d 刚好被其他程序抢占了，请关掉本窗口后重新双击启动。" % port)


if __name__ == "__main__":
    main()
