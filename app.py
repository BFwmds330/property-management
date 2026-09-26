# -*- coding: utf-8 -*-
"""物业管家（个人版）——应用入口。

双击 启动.bat 后由本文件启动服务：
- 自动建库建表（data/property.db）；
- 自动找一个空闲端口；
- 启动后自动打开浏览器进入系统首页。
"""
import os
import re
import secrets
import socket
import sqlite3
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


def _bind_host():
    """监听地址：默认只允许本机（127.0.0.1）访问；WUYE_LAN=1 时对局域网开放（供手机查看）。

    对局域网开放意味着同一 WiFi 内的任何人都能打开本系统（身份证号、手机号、账目都在里面），
    所以启动横幅会给出醒目提醒；请只在可信网络下开启，并建议配合启动密码（WUYE_PIN）使用。
    """
    return "0.0.0.0" if os.environ.get("WUYE_LAN") == "1" else "127.0.0.1"


def _resolve_startup_pin():
    """启动密码（可选）：由环境变量 WUYE_PIN 控制，默认不启用。

    - 未设置或为空：无密码，行为与旧版一致；
    - 设为 1 / on / yes / 随机：本次启动随机生成一个 4 位数字（打印在启动横幅里）；
    - 设为 4 位数字（如 8848）：直接用这个固定密码。
    进入系统前需在验证页输入一次，通过后本次会话内不再询问（存 session）。
    """
    raw = (os.environ.get("WUYE_PIN") or "").strip()
    if not raw:
        return None
    if raw in ("1", "on", "yes", "ON", "random", "随机"):
        return "%04d" % secrets.randbelow(10000)
    if re.fullmatch(r"\d{4}", raw):
        return raw
    print("【提示】环境变量 WUYE_PIN 的值不是 4 位数字，启动密码未启用。")
    return None


def _port_in_use(port):
    """端口是否已有程序在监听：直接连一次，连得上就说明被占用。

    这是最可靠的判断方式——不管对方绑定的是 0.0.0.0 还是具体 IP，
    只要端口上有服务，连接就一定成功。旧版用“试绑定 127.0.0.1”判断，
    在 Windows 上会被 0.0.0.0 的监听“骗过”，导致两个版本开在同一个端口。
    """
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.3)
        return s.connect_ex(("127.0.0.1", port)) == 0


def _can_bind(port, host="0.0.0.0"):
    """能否按服务器将要采用的方式真正绑定该端口。"""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        try:
            s.bind((host, port))
            return True
        except OSError:
            return False


def _pick_port(start, host=None):
    """从 start 开始找一个真正可用的端口（最多往后找 20 个，找不到返回 None）。"""
    host = host or _bind_host()
    for port in range(start, start + 21):
        if not _port_in_use(port) and _can_bind(port, host):
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
    # 显式声明 SameSite=Lax：现代浏览器跨站 POST 默认就被浏览器拦下，这里变成明示保证
    app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
    # 启动密码（可选，见 _resolve_startup_pin）：在 create_app 时读取一次环境变量，
    # 之后环境变量再变化也不影响已启动的实例
    app.config["STARTUP_PIN"] = _resolve_startup_pin()

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
    from routes.notice import notice_bp
    from routes.system import system_bp
    for bp in (main_bp, community_bp, house_bp, building_bp, htype_bp,
               resident_bp, fee_bp, report_bp, repair_bp, staff_bp, expense_bp, notice_bp, system_bp):
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

    @app.before_request
    def require_startup_pin():
        """启动密码拦截（v2.4.0）：
        - 设置页启用的持久密码（存 app_meta 哈希）优先，运行中启用/关闭立即生效；
        - 未设置持久密码时，回退到环境变量 WUYE_PIN 的会话密码；
        - 通过验证后 session 记 pin_ok，本会话（关闭浏览器前）不再询问。
        """
        from services import system_service
        stored = system_service.get_pin_hash(database.get_db())
        session_pin = app.config.get("STARTUP_PIN")
        if not stored and not session_pin:
            return None
        if session.get("pin_ok"):
            return None
        if request.path == "/pin" or request.path.startswith("/static/"):
            return None
        nxt = request.full_path if request.query_string else request.path
        return redirect(url_for("pin_page", next=nxt))

    @app.route("/pin", methods=["GET", "POST"])
    def pin_page():
        """启动密码验证页（持久密码或 WUYE_PIN 会话密码启用时可达）。"""
        from services import system_service
        stored = system_service.get_pin_hash(database.get_db())
        session_pin = app.config.get("STARTUP_PIN")
        if not stored and not session_pin:
            return redirect(url_for("main.dashboard"))
        nxt = request.values.get("next") or url_for("main.dashboard")
        # 只允许站内相对路径，防止跳到外部网址
        if not str(nxt).startswith("/"):
            nxt = url_for("main.dashboard")
        error = ""
        if request.method == "POST":
            code = (request.form.get("code") or "").strip()
            if stored:
                ok = system_service.verify_pin_value(code, stored)
            else:
                ok = (code == session_pin)
            if ok:
                session["pin_ok"] = True
                return redirect(nxt)
            error = "密码不正确，请重新输入"
        return render_template("pin.html", error=error, next=nxt)

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
    host = _bind_host()
    port = _pick_port(start, host)
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
    if host == "0.0.0.0":
        print("  手机访问（同一 WiFi）：http://%s:%d" % (_lan_ip(), port))
        print("  ⚠️ 已对局域网开放（WUYE_LAN=1）：同一 WiFi 内的任何人都能打开本系统，")
        print("     请只在可信网络下使用，并建议配合启动密码（WUYE_PIN）。")
    else:
        print("  （当前仅本机可访问。手机要看？关闭本窗口后运行「手机访问.bat」，")
        print("   或设置环境变量 WUYE_LAN=1 再启动。）")
    pin = app.config.get("STARTUP_PIN")
    stored_pin = ""
    try:
        _db = database.open_db()
        try:
            _row = _db.execute("SELECT value FROM app_meta WHERE key='startup_pin_hash'").fetchone()
        finally:
            _db.close()
        stored_pin = (_row[0] or "") if _row else ""
    except sqlite3.Error:
        stored_pin = ""
    if stored_pin:
        print("  🔐 启动密码已开启：打开系统需输入 4 位数字密码")
        print("     （可在系统「设置 → 启动密码」里更改或关闭）")
    elif pin:
        print("  🔐 启动密码已开启：本系统的访问密码是 %s" % pin)
        if (os.environ.get("WUYE_PIN") or "").strip() in ("1", "on", "yes", "ON", "random", "随机"):
            print("     （随机密码，每次启动都会变化；建议改在系统「设置 → 启动密码」里设置固定密码）")
    print("  数据保存在：data 文件夹里，关闭本窗口即退出系统")
    print("=" * 56)

    if os.environ.get("WUYE_NO_BROWSER") != "1":
        threading.Timer(1.2, lambda: webbrowser.open(url)).start()
    if os.environ.get("WUYE_FAST_EXIT") == "1":     # 自动化测试用：启动后自动退出
        threading.Timer(3.0, lambda: os._exit(0)).start()

    try:
        app.run(host=host, port=port, debug=False, use_reloader=False)
    except OSError:
        print("【提示】端口 %d 刚好被其他程序抢占了，请关掉本窗口后重新双击启动。" % port)


if __name__ == "__main__":
    main()
