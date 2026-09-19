# -*- coding: utf-8 -*-
"""数据库层：连接管理、建表、常用查询小助手。

要点：
- 金额一律存“分”（整数），面积存“0.01平方米”的整数，避免浮点误差；
- 房号组合唯一：楼栋+单元+楼层+房号（部分唯一索引，作废账单不占用账期）；
- 外键开启级联删除：删小区会连带删掉它下面的楼栋/房屋/账单等；
- 每次请求用一个独立连接，用完自动关闭。
"""
import sqlite3

from flask import g

from config import DB_PATH

# 建表语句。全部使用 IF NOT EXISTS，重复执行安全（也用于恢复旧备份后的补列）
SCHEMA = """
CREATE TABLE IF NOT EXISTS community (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    name             TEXT NOT NULL UNIQUE,
    address          TEXT DEFAULT '',
    building_count   INTEGER DEFAULT 0,
    parking_info     TEXT DEFAULT '',
    delivery_date    TEXT DEFAULT '',
    takeover_date    TEXT DEFAULT '',
    default_unit_price INTEGER DEFAULT 0,
    remark           TEXT DEFAULT '',
    is_demo          INTEGER DEFAULT 0,
    created_at       TEXT DEFAULT (datetime('now','localtime'))
);

CREATE TABLE IF NOT EXISTS building (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    community_id  INTEGER NOT NULL REFERENCES community(id) ON DELETE CASCADE,
    code          TEXT NOT NULL,
    units         INTEGER NOT NULL DEFAULT 1,
    floors        INTEGER NOT NULL DEFAULT 1,
    has_elevator  INTEGER DEFAULT 0,
    remark        TEXT DEFAULT '',
    UNIQUE(community_id, code)
);

CREATE TABLE IF NOT EXISTS house_type (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    community_id  INTEGER NOT NULL REFERENCES community(id) ON DELETE CASCADE,
    name          TEXT NOT NULL,
    rooms         INTEGER DEFAULT 0,
    halls         INTEGER DEFAULT 0,
    baths         INTEGER DEFAULT 0,
    area_100      INTEGER DEFAULT 0,
    orientation   TEXT DEFAULT '',
    has_balcony   INTEGER DEFAULT 0,
    remark        TEXT DEFAULT '',
    UNIQUE(community_id, name)
);

CREATE TABLE IF NOT EXISTS resident (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    name            TEXT NOT NULL,
    gender          TEXT DEFAULT '',
    birth_date      TEXT DEFAULT '',
    id_number       TEXT DEFAULT '',
    phone           TEXT DEFAULT '',
    work_unit       TEXT DEFAULT '',
    emergency_name  TEXT DEFAULT '',
    emergency_phone TEXT DEFAULT '',
    remark          TEXT DEFAULT '',
    created_at      TEXT DEFAULT (datetime('now','localtime'))
);

CREATE TABLE IF NOT EXISTS house (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    community_id      INTEGER NOT NULL REFERENCES community(id) ON DELETE CASCADE,
    building_id       INTEGER NOT NULL REFERENCES building(id) ON DELETE CASCADE,
    unit              INTEGER NOT NULL DEFAULT 1,
    floor             INTEGER NOT NULL DEFAULT 1,
    room_no           INTEGER NOT NULL DEFAULT 0,
    area_100          INTEGER DEFAULT 0,
    inner_area_100    INTEGER DEFAULT 0,
    house_type_id     INTEGER REFERENCES house_type(id) ON DELETE SET NULL,
    status            TEXT DEFAULT 'vacant',
    owner_resident_id INTEGER REFERENCES resident(id) ON DELETE SET NULL,
    occupied_date     TEXT DEFAULT '',
    remark            TEXT DEFAULT '',
    created_at        TEXT DEFAULT (datetime('now','localtime')),
    UNIQUE(building_id, unit, floor, room_no)
);

-- 住户与房屋的关系：业主 / 家庭成员 / 租户；is_current=0 表示历史记录
CREATE TABLE IF NOT EXISTS resident_house (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    resident_id  INTEGER NOT NULL REFERENCES resident(id) ON DELETE CASCADE,
    house_id     INTEGER NOT NULL REFERENCES house(id) ON DELETE CASCADE,
    role         TEXT NOT NULL DEFAULT 'member',
    relation     TEXT DEFAULT '',
    is_current   INTEGER DEFAULT 1,
    is_living    INTEGER DEFAULT 1,
    start_date   TEXT DEFAULT '',
    end_date     TEXT DEFAULT '',
    lease_start  TEXT DEFAULT '',
    lease_end    TEXT DEFAULT '',
    monthly_rent INTEGER DEFAULT 0,
    remark       TEXT DEFAULT '',
    created_at   TEXT DEFAULT (datetime('now','localtime'))
);

CREATE TABLE IF NOT EXISTS fee_item (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    community_id  INTEGER NOT NULL REFERENCES community(id) ON DELETE CASCADE,
    name          TEXT NOT NULL,
    pricing_mode  TEXT DEFAULT 'area',
    unit_price    INTEGER DEFAULT 0,
    cycle         TEXT DEFAULT 'month',
    enabled       INTEGER DEFAULT 1,
    remark        TEXT DEFAULT '',
    UNIQUE(community_id, name)
);

CREATE TABLE IF NOT EXISTS vehicle (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    house_id   INTEGER NOT NULL REFERENCES house(id) ON DELETE CASCADE,
    plate      TEXT NOT NULL,
    remark     TEXT DEFAULT '',
    created_at TEXT DEFAULT (datetime('now','localtime'))
);

CREATE INDEX IF NOT EXISTS idx_vehicle_house ON vehicle(house_id);

CREATE TABLE IF NOT EXISTS bill (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    house_id          INTEGER NOT NULL REFERENCES house(id) ON DELETE CASCADE,
    fee_item_id       INTEGER NOT NULL REFERENCES fee_item(id),
    vehicle_id        INTEGER REFERENCES vehicle(id) ON DELETE SET NULL,
    period            TEXT NOT NULL,
    period_start      TEXT NOT NULL,
    months            INTEGER NOT NULL DEFAULT 1,
    amount_receivable INTEGER DEFAULT 0,
    amount_received   INTEGER DEFAULT 0,
    status            TEXT DEFAULT 'unpaid',
    remark            TEXT DEFAULT '',
    created_at        TEXT DEFAULT (datetime('now','localtime'))
);

-- 已作废的账单不占用“房+项目+账期”唯一名额，允许重新生成
CREATE UNIQUE INDEX IF NOT EXISTS uq_bill_period
    ON bill(house_id, fee_item_id, period) WHERE status != 'void';

CREATE TABLE IF NOT EXISTS bill_adjust (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    bill_id       INTEGER NOT NULL REFERENCES bill(id) ON DELETE CASCADE,
    before_amount INTEGER NOT NULL,
    after_amount  INTEGER NOT NULL,
    reason        TEXT NOT NULL,
    created_at    TEXT DEFAULT (datetime('now','localtime'))
);

CREATE TABLE IF NOT EXISTS payment (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    bill_id    INTEGER NOT NULL REFERENCES bill(id) ON DELETE CASCADE,
    amount     INTEGER NOT NULL,
    pay_date   TEXT NOT NULL,
    method     TEXT DEFAULT '现金',
    receipt_no TEXT DEFAULT '',
    remark     TEXT DEFAULT '',
    created_at TEXT DEFAULT (datetime('now','localtime'))
);

CREATE TABLE IF NOT EXISTS repair (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    community_id  INTEGER NOT NULL REFERENCES community(id) ON DELETE CASCADE,
    house_id      INTEGER REFERENCES house(id) ON DELETE SET NULL,
    content       TEXT NOT NULL,
    status        TEXT DEFAULT 'pending',
    handler_note  TEXT DEFAULT '',
    created_at    TEXT DEFAULT (datetime('now','localtime')),
    updated_at    TEXT DEFAULT (datetime('now','localtime'))
);

CREATE TABLE IF NOT EXISTS operation_log (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    module     TEXT DEFAULT '',
    action     TEXT DEFAULT '',
    detail     TEXT DEFAULT '',
    is_demo    INTEGER DEFAULT 0,
    created_at TEXT DEFAULT (datetime('now','localtime'))
);

CREATE TABLE IF NOT EXISTS app_meta (
    key   TEXT PRIMARY KEY,
    value TEXT DEFAULT ''
);

CREATE INDEX IF NOT EXISTS idx_house_community ON house(community_id);
CREATE INDEX IF NOT EXISTS idx_bill_house ON bill(house_id);
CREATE INDEX IF NOT EXISTS idx_rh_house ON resident_house(house_id);
CREATE INDEX IF NOT EXISTS idx_rh_resident ON resident_house(resident_id);
"""


def get_db():
    """获取当前请求的数据库连接（Flask 自动管理生命周期）。"""
    if "db" not in g:
        g.db = sqlite3.connect(DB_PATH)
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA foreign_keys = ON")
        g.db.execute("PRAGMA busy_timeout = 5000")
    return g.db


def close_db(exc=None):
    db = g.pop("db", None)
    if db is not None:
        try:
            db.commit()
        except sqlite3.Error:
            pass
        db.close()


def open_db(path=None):
    """独立打开一个连接（备份/恢复/演示数据等场景用，不挂在请求上）。"""
    db = sqlite3.connect(path or DB_PATH)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys = ON")
    return db


def _migrate(db):
    """轻量升级：给老数据库补上新增的列（新装环境无感）。"""
    cols = {r[1] for r in db.execute("PRAGMA table_info(house)")}
    if "parking_no" not in cols:
        db.execute("ALTER TABLE house ADD COLUMN parking_no TEXT DEFAULT ''")
    bcols = {r[1] for r in db.execute("PRAGMA table_info(bill)")}
    if "vehicle_id" not in bcols:
        db.execute("ALTER TABLE bill ADD COLUMN vehicle_id INTEGER REFERENCES vehicle(id) ON DELETE SET NULL")


def ensure_schema(db):
    """建库建表；已存在则跳过。恢复旧备份后也会调用它补齐缺失的表。"""
    db.executescript(SCHEMA)
    _migrate(db)
    db.commit()


def init_db():
    """首次运行：确保目录和表都存在。"""
    from config import ensure_dirs
    ensure_dirs()
    db = open_db()
    try:
        ensure_schema(db)
    finally:
        db.close()


def log_op(db, module, action, detail="", is_demo=0):
    """写一条关键操作日志。"""
    db.execute(
        "INSERT INTO operation_log (module, action, detail, is_demo) VALUES (?,?,?,?)",
        (module, action, detail, is_demo))


def query_all(db, sql, args=()):
    return db.execute(sql, args).fetchall()


def query_one(db, sql, args=()):
    return db.execute(sql, args).fetchone()


def scalar(db, sql, args=(), default=0):
    row = db.execute(sql, args).fetchone()
    if row is None or row[0] is None:
        return default
    return row[0]
