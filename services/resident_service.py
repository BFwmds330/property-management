# -*- coding: utf-8 -*-
"""住户管理：业主 / 家庭成员 / 租户，以房屋为锚点登记，含历史轨迹与通讯录导出。"""
from database import log_op, query_all, query_one, scalar
from utils import (UserError, ROLE, clean_str, fmt_money, parse_date,
                   parse_idcard, parse_int, parse_phone)


def get_resident(db, rid):
    row = query_one(db, "SELECT * FROM resident WHERE id=?", (rid,))
    if not row:
        raise UserError("没有找到这位住户，可能已被删除，请刷新页面")
    return row


def get_link(db, link_id):
    row = query_one(db, "SELECT * FROM resident_house WHERE id=?", (link_id,))
    if not row:
        raise UserError("没有找到这条住户登记记录，可能已被删除，请刷新页面")
    return row


def _person_fields(db, form, prefix=""):
    g = lambda k: form.get(prefix + k)
    name = clean_str(g("name"), "姓名", 50, required=True)
    gender = (g("gender") or "").strip()
    if gender not in ("", "男", "女"):
        raise UserError("性别只能是 男 / 女")
    birth = parse_date(g("birth_date"), "出生日期")
    id_number = parse_idcard(g("id_number"), "证件号")
    phone = parse_phone(g("phone"), "手机号")
    work_unit = clean_str(g("work_unit"), "工作单位", 100)
    e_name = clean_str(g("emergency_name"), "紧急联系人姓名", 50)
    e_phone = parse_phone(g("emergency_phone"), "紧急联系人电话")
    remark = clean_str(g("remark"), "备注", 300)
    return dict(name=name, gender=gender, birth_date=birth, id_number=id_number, phone=phone,
                work_unit=work_unit, emergency_name=e_name, emergency_phone=e_phone, remark=remark)


def _check_current_owner_unique(db, house_id, exclude_link=None):
    cnt = scalar(db, "SELECT COUNT(*) FROM resident_house WHERE house_id=? AND role='owner' AND is_current=1",
                 (house_id,))
    if cnt > 0:
        raise UserError("这套房已经有登记业主了；如需更换业主请使用“产权过户”功能")


def add_resident_to_house(db, house_id, form):
    """在某套房屋下登记业主/家庭成员/租户。"""
    from services.house_service import get_house_full, house_label
    house = get_house_full(db, house_id)
    role = form.get("role")
    if role not in ROLE:
        raise UserError("住户身份不正确")
    if role == "owner":
        _check_current_owner_unique(db, house_id)
    person = _person_fields(db, form)
    cur = db.execute(
        """INSERT INTO resident (name, gender, birth_date, id_number, phone, work_unit,
               emergency_name, emergency_phone, remark) VALUES (?,?,?,?,?,?,?,?,?)""",
        (person["name"], person["gender"], person["birth_date"], person["id_number"],
         person["phone"], person["work_unit"], person["emergency_name"],
         person["emergency_phone"], person["remark"]))
    rid = cur.lastrowid
    relation = clean_str(form.get("relation"), "与业主的关系", 20)
    is_living = 1 if form.get("is_living") else 0
    start_date = parse_date(form.get("start_date"), "入住/登记日期")
    lease_start = lease_end = ""
    monthly_rent = 0
    if role == "tenant":
        lease_start = parse_date(form.get("lease_start"), "租期开始日期")
        lease_end = parse_date(form.get("lease_end"), "租期结束日期")
        if lease_start and lease_end and lease_end < lease_start:
            raise UserError("租期结束日期不能早于开始日期")
        monthly_rent = parse_amount_rent(form.get("monthly_rent"))
    db.execute(
        """INSERT INTO resident_house (resident_id, house_id, role, relation, is_current,
               is_living, start_date, lease_start, lease_end, monthly_rent)
           VALUES (?,?,?,?,1,?,?,?,?,?)""",
        (rid, house_id, role, relation, is_living, start_date, lease_start, lease_end, monthly_rent))
    if role == "owner" and not house["owner_resident_id"]:
        db.execute("UPDATE house SET owner_resident_id=? WHERE id=?", (rid, house_id))
    log_op(db, "住户", "登记住户", "%s 登记%s「%s」" % (
        house_label({"code": house["building_code"]}, house["unit"], house["room_no"]),
        ROLE[role], person["name"]))
    return rid


def parse_amount_rent(value):
    from utils import parse_amount
    return parse_amount(value, "月租金", required=False)


def update_resident(db, rid, link_id, form):
    """编辑住户个人信息 + 登记信息。"""
    from services.house_service import get_house_full, house_label
    person = _person_fields(db, form)
    db.execute(
        """UPDATE resident SET name=?, gender=?, birth_date=?, id_number=?, phone=?,
               work_unit=?, emergency_name=?, emergency_phone=?, remark=? WHERE id=?""",
        (person["name"], person["gender"], person["birth_date"], person["id_number"],
         person["phone"], person["work_unit"], person["emergency_name"],
         person["emergency_phone"], person["remark"], rid))
    link = get_link(db, link_id)
    relation = clean_str(form.get("relation"), "与业主的关系", 20)
    is_living = 1 if form.get("is_living") else 0
    start_date = parse_date(form.get("start_date"), "入住/登记日期")
    lease_start = lease_end = ""
    monthly_rent = 0
    if link["role"] == "tenant":
        lease_start = parse_date(form.get("lease_start"), "租期开始日期")
        lease_end = parse_date(form.get("lease_end"), "租期结束日期")
        if lease_start and lease_end and lease_end < lease_start:
            raise UserError("租期结束日期不能早于开始日期")
        monthly_rent = parse_amount_rent(form.get("monthly_rent"))
    db.execute(
        """UPDATE resident_house SET relation=?, is_living=?, start_date=?,
               lease_start=?, lease_end=?, monthly_rent=? WHERE id=?""",
        (relation, is_living, start_date, lease_start, lease_end, monthly_rent, link_id))
    house = get_house_full(db, link["house_id"])
    log_op(db, "住户", "修改住户", "%s 修改%s「%s」信息" % (
        house_label({"code": house["building_code"]}, house["unit"], house["room_no"]),
        ROLE[link["role"]], person["name"]))


def move_out(db, link_id):
    """住户搬出/退租：登记记录转历史，人保留（也许还住在别处）。"""
    from services.house_service import get_house_full, house_label
    link = get_link(db, link_id)
    if link["role"] == "owner":
        raise UserError("业主不能直接移出，请使用房屋页面的“产权过户”功能更换业主")
    house = get_house_full(db, link["house_id"])
    person = get_resident(db, link["resident_id"])
    from utils import today_str
    db.execute("UPDATE resident_house SET is_current=0, end_date=? WHERE id=?", (today_str(), link_id))
    log_op(db, "住户", "住户搬出", "%s %s「%s」搬出，转为历史住户" % (
        house_label({"code": house["building_code"]}, house["unit"], house["room_no"]),
        ROLE[link["role"]], person["name"]))


def house_residents(db, house_id, only_current=True):
    """一套房的住户（默认只看当前），含角色与租期信息。"""
    sql = """
        SELECT rh.*, r.name, r.gender, r.phone, r.id_number, r.birth_date,
               r.work_unit, r.emergency_name, r.emergency_phone, r.remark AS person_remark
        FROM resident_house rh JOIN resident r ON r.id = rh.resident_id
        WHERE rh.house_id = ?"""
    if only_current:
        sql += " AND rh.is_current = 1"
    sql += " ORDER BY CASE rh.role WHEN 'owner' THEN 0 WHEN 'member' THEN 1 ELSE 2 END, rh.id"
    return [dict(r) for r in query_all(db, sql, (house_id,))]


def house_history(db, house_id):
    """一套房的历史住户时间线（含已搬出的）。"""
    rows = query_all(db, """
        SELECT rh.*, r.name, r.phone
        FROM resident_house rh JOIN resident r ON r.id = rh.resident_id
        WHERE rh.house_id = ?
        ORDER BY rh.is_current DESC,
                 COALESCE(NULLIF(rh.start_date,''), rh.created_at) DESC, rh.id DESC""", (house_id,))
    return [dict(r) for r in rows]


def global_search(db, community_id, keyword, limit=80):
    """按姓名 / 手机号 / 证件号 / 房号全局搜索住户。"""
    kw = f"%{keyword.strip()}%"
    rows = query_all(db, """
        SELECT rh.id AS link_id, rh.role, rh.is_current, rh.relation,
               rh.lease_start, rh.lease_end, rh.monthly_rent,
               r.id AS resident_id, r.name, r.phone, r.id_number,
               h.id AS house_id, h.unit, h.room_no, b.code AS building_code
        FROM resident_house rh
        JOIN resident r ON r.id = rh.resident_id
        JOIN house h ON h.id = rh.house_id
        JOIN building b ON b.id = h.building_id
        WHERE h.community_id = ?
          AND (r.name LIKE ? OR r.phone LIKE ? OR r.id_number LIKE ?
               OR (b.code || '-' || h.unit || '-' || h.room_no) LIKE ?)
        ORDER BY rh.is_current DESC, r.name
        LIMIT ?""", (community_id, kw, kw, kw, kw, limit))
    return [dict(r) for r in rows]


def export_contacts(db, community_id):
    """导出当前小区在住住户通讯录 CSV。"""
    community = query_one(db, "SELECT name FROM community WHERE id=?", (community_id,))
    rows = query_all(db, """
        SELECT rh.role, rh.relation, rh.is_living, rh.lease_start, rh.lease_end, rh.monthly_rent,
               r.name, r.gender, r.phone, r.id_number, r.work_unit,
               r.emergency_name, r.emergency_phone,
               h.unit, h.room_no, b.code AS building_code
        FROM resident_house rh
        JOIN resident r ON r.id = rh.resident_id
        JOIN house h ON h.id = rh.house_id
        JOIN building b ON b.id = h.building_id
        WHERE h.community_id = ? AND rh.is_current = 1
        ORDER BY b.id, h.unit, h.floor, h.room_no, rh.role""", (community_id,))
    headers = ["房号", "身份", "姓名", "性别", "手机号", "证件号", "与业主关系", "是否常住",
               "工作单位", "紧急联系人", "紧急联系人电话", "租期开始", "租期结束", "月租金(元)"]
    out = []
    for r in rows:
        label = "%s栋%d单元%d室" % (str(r["building_code"]).replace("#", ""), r["unit"], r["room_no"])
        out.append([
            label, ROLE.get(r["role"], r["role"]), r["name"], r["gender"], r["phone"],
            r["id_number"], r["relation"], "是" if r["is_living"] else "否", r["work_unit"],
            r["emergency_name"], r["emergency_phone"], r["lease_start"], r["lease_end"],
            fmt_money(r["monthly_rent"]),
        ])
    name = "%s-住户通讯录.csv" % (community["name"] if community else "小区")
    return headers, out, name
