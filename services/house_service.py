# -*- coding: utf-8 -*-
"""楼栋 / 户型 / 房屋管理，含批量生成与产权过户。"""
from database import log_op, query_all, query_one, scalar
from utils import (UserError, clean_str, fmt_area, month_add, parse_area,
                   parse_date, parse_int, HOUSE_STATUS)

# ---------------------------------------------------------------- 楼栋

def list_buildings(db, community_id):
    rows = query_all(db, """
        SELECT b.*,
               (SELECT COUNT(*) FROM house h WHERE h.building_id = b.id) AS house_count
        FROM building b WHERE b.community_id=? ORDER BY b.id""", (community_id,))
    return [dict(r) for r in rows]


def get_building(db, bid):
    row = query_one(db, "SELECT * FROM building WHERE id=?", (bid,))
    if not row:
        raise UserError("没有找到这个楼栋，可能已被删除，请刷新页面")
    return row


def add_building(db, community_id, form):
    code = clean_str(form.get("code"), "楼栋编号", 20, required=True)
    units = parse_int(form.get("units"), "单元数", 1, 20)
    floors = parse_int(form.get("floors"), "地上层数", 1, 99)
    has_elevator = 1 if form.get("has_elevator") else 0
    remark = clean_str(form.get("remark"), "备注", 200)
    if scalar(db, "SELECT COUNT(*) FROM building WHERE community_id=? AND code=?",
              (community_id, code)) > 0:
        raise UserError("这个小区里已经有“%s”号楼了" % code)
    cur = db.execute(
        "INSERT INTO building (community_id, code, units, floors, has_elevator, remark) VALUES (?,?,?,?,?,?)",
        (community_id, code, units, floors, has_elevator, remark))
    log_op(db, "房屋", "新增楼栋", "新增楼栋「%s」（%d 单元 %d 层）" % (code, units, floors))
    return cur.lastrowid


def update_building(db, bid, form):
    row = get_building(db, bid)
    code = clean_str(form.get("code"), "楼栋编号", 20, required=True)
    units = parse_int(form.get("units"), "单元数", 1, 20)
    floors = parse_int(form.get("floors"), "地上层数", 1, 99)
    has_elevator = 1 if form.get("has_elevator") else 0
    remark = clean_str(form.get("remark"), "备注", 200)
    if scalar(db, "SELECT COUNT(*) FROM building WHERE community_id=? AND code=? AND id!=?",
              (row["community_id"], code, bid)) > 0:
        raise UserError("这个小区里已经有“%s”号楼了" % code)
    # 已生成的房屋不能超出新的单元数/层数范围
    bad = scalar(db, "SELECT COUNT(*) FROM house WHERE building_id=? AND (unit>? OR floor>?)",
                 (bid, units, floors))
    if bad > 0:
        raise UserError("该楼栋已有 %d 套房屋的单元/楼层超出新范围，请先处理这些房屋" % bad)
    db.execute("UPDATE building SET code=?, units=?, floors=?, has_elevator=?, remark=? WHERE id=?",
               (code, units, floors, has_elevator, remark, bid))
    log_op(db, "房屋", "修改楼栋", "修改楼栋「%s」" % code)


def delete_building(db, bid):
    row = get_building(db, bid)
    houses = scalar(db, "SELECT COUNT(*) FROM house WHERE building_id=?", (bid,))
    db.execute("DELETE FROM building WHERE id=?", (bid,))
    db.execute("""DELETE FROM resident WHERE id NOT IN
                  (SELECT DISTINCT resident_id FROM resident_house)""")
    log_op(db, "房屋", "删除楼栋", "删除楼栋「%s」及其 %d 套房屋" % (row["code"], houses))


# ---------------------------------------------------------------- 户型

def list_house_types(db, community_id):
    rows = query_all(db, """
        SELECT t.*,
               (SELECT COUNT(*) FROM house h WHERE h.house_type_id = t.id) AS used_count
        FROM house_type t WHERE t.community_id=? ORDER BY t.id""", (community_id,))
    return [dict(r) for r in rows]


def get_house_type(db, tid):
    row = query_one(db, "SELECT * FROM house_type WHERE id=?", (tid,))
    if not row:
        raise UserError("没有找到这个户型，可能已被删除，请刷新页面")
    return row


def _type_fields(db, form):
    name = clean_str(form.get("name"), "户型名称", 50, required=True)
    rooms = parse_int(form.get("rooms"), "室", 0, 20)
    halls = parse_int(form.get("halls"), "厅", 0, 20)
    baths = parse_int(form.get("baths"), "卫", 0, 20)
    area_100 = parse_area(form.get("area"), "建筑面积")
    orientation = clean_str(form.get("orientation"), "朝向", 20)
    has_balcony = 1 if form.get("has_balcony") else 0
    remark = clean_str(form.get("remark"), "备注", 200)
    return name, rooms, halls, baths, area_100, orientation, has_balcony, remark


def add_house_type(db, community_id, form):
    (name, rooms, halls, baths, area_100, orientation, has_balcony, remark) = _type_fields(db, form)
    if scalar(db, "SELECT COUNT(*) FROM house_type WHERE community_id=? AND name=?",
              (community_id, name)) > 0:
        raise UserError("已经有叫“%s”的户型了" % name)
    cur = db.execute(
        """INSERT INTO house_type (community_id, name, rooms, halls, baths, area_100,
               orientation, has_balcony, remark) VALUES (?,?,?,?,?,?,?,?,?)""",
        (community_id, name, rooms, halls, baths, area_100, orientation, has_balcony, remark))
    log_op(db, "房屋", "新增户型", "新增户型「%s」" % name)
    return cur.lastrowid


def update_house_type(db, tid, form):
    row = get_house_type(db, tid)
    (name, rooms, halls, baths, area_100, orientation, has_balcony, remark) = _type_fields(db, form)
    if scalar(db, "SELECT COUNT(*) FROM house_type WHERE community_id=? AND name=? AND id!=?",
              (row["community_id"], name, tid)) > 0:
        raise UserError("已经有叫“%s”的户型了" % name)
    db.execute(
        """UPDATE house_type SET name=?, rooms=?, halls=?, baths=?, area_100=?,
               orientation=?, has_balcony=?, remark=? WHERE id=?""",
        (name, rooms, halls, baths, area_100, orientation, has_balcony, remark, tid))
    used = scalar(db, "SELECT COUNT(*) FROM house WHERE house_type_id=?", (tid,))
    log_op(db, "房屋", "修改户型", "修改户型「%s」（%d 套房屋在用）" % (name, used))


def delete_house_type(db, tid):
    row = get_house_type(db, tid)
    used = scalar(db, "SELECT COUNT(*) FROM house WHERE house_type_id=?", (tid,))
    if used > 0:
        raise UserError("有 %d 套房屋正在使用这个户型，不能删除；可以先修改这些房屋的户型，"
                        "或者把该户型停用" % used)
    db.execute("DELETE FROM house_type WHERE id=?", (tid,))
    log_op(db, "房屋", "删除户型", "删除户型「%s」" % row["name"])


# ---------------------------------------------------------------- 房屋

def house_code(b, unit, room_no):
    """房号编码，如 1-1-1501；楼栋编号里的 # 不参与编码。"""
    return "%s-%d-%d" % (str(b["code"]).replace("#", ""), unit, room_no)


def house_label(b, unit, room_no):
    """界面显示，如 1栋1单元1501室。"""
    return "%s栋%d单元%d室" % (str(b["code"]).replace("#", ""), unit, room_no)


def list_houses(db, community_id, building_id=0, unit=0, status="", keyword="", only_owed=0):
    kw = f"%{keyword.strip()}%"
    sql = """
        SELECT h.*, b.code AS building_code, b.units AS building_units,
               t.name AS type_name, r.name AS owner_name, r.phone AS owner_phone,
               (SELECT COALESCE(SUM(b2.amount_receivable - b2.amount_received), 0)
                    FROM bill b2 WHERE b2.house_id = h.id AND b2.status != 'void') AS owed_fen,
               (SELECT COUNT(*) FROM resident_house rh WHERE rh.house_id = h.id
                    AND rh.is_current = 1) AS resident_count
        FROM house h
        JOIN building b ON b.id = h.building_id
        LEFT JOIN house_type t ON t.id = h.house_type_id
        LEFT JOIN resident r ON r.id = h.owner_resident_id
        WHERE h.community_id = ?"""
    args = [community_id]
    if building_id:
        sql += " AND h.building_id = ?"
        args.append(building_id)
    if unit:
        sql += " AND h.unit = ?"
        args.append(unit)
    if status:
        sql += " AND h.status = ?"
        args.append(status)
    if kw != "%%":
        sql += " AND (r.name LIKE ? OR r.phone LIKE ? OR (b.code || '-' || h.unit || '-' || h.room_no) LIKE ?)"
        args += [kw, kw, kw]
    if only_owed:
        sql += " AND h.id IN (SELECT house_id FROM bill WHERE status != 'void' GROUP BY house_id HAVING SUM(amount_receivable - amount_received) > 0)"
    sql += " ORDER BY b.id, h.unit, h.floor, h.room_no"
    return [dict(r) for r in query_all(db, sql, args)]


def get_house_full(db, hid):
    """取一套房屋的完整信息（含楼栋、户型、业主、当前住户、欠费）。"""
    row = query_one(db, """
        SELECT h.*, b.code AS building_code, b.floors AS building_floors, b.units AS building_units,
               t.name AS type_name, r.name AS owner_name, r.phone AS owner_phone,
               (SELECT COALESCE(SUM(b2.amount_receivable - b2.amount_received), 0)
                    FROM bill b2 WHERE b2.house_id = h.id AND b2.status != 'void') AS owed_fen
        FROM house h
        JOIN building b ON b.id = h.building_id
        LEFT JOIN house_type t ON t.id = h.house_type_id
        LEFT JOIN resident r ON r.id = h.owner_resident_id
        WHERE h.id = ?""", (hid,))
    if not row:
        raise UserError("没有找到这套房屋，可能已被删除，请刷新页面")
    d = dict(row)
    d["vehicles"] = []
    for v in query_all(db, "SELECT * FROM vehicle WHERE house_id=? ORDER BY id", (hid,)):
        v = dict(v)
        bills = [dict(b) for b in query_all(db, """
            SELECT b.*, f.name AS item_name FROM bill b
            JOIN fee_item f ON f.id = b.fee_item_id
            WHERE b.vehicle_id = ? AND b.status != 'void'
            ORDER BY b.period_start, b.id""", (v["id"],))]
        paid_until = ""
        for b in bills:
            if b["status"] == "paid":
                end = month_add(b["period_start"], max(b["months"], 1) - 1)
                if end > paid_until:
                    paid_until = end
        v["bills"] = bills
        v["paid_until"] = paid_until
        d["vehicles"].append(v)
    return d


def _house_unique_fields(db, form, building):
    unit = parse_int(form.get("unit"), "单元", 1, building["units"])
    floor = parse_int(form.get("floor"), "楼层", 1, building["floors"])
    room_no = parse_int(form.get("room_no"), "房号", 1, 9999)
    return unit, floor, room_no


def add_house(db, community_id, form):
    building = get_building(db, parse_int(form.get("building_id"), "楼栋", 1, 10**9))
    if building["community_id"] != community_id:
        raise UserError("该楼栋不属于当前小区，请重新选择")
    unit, floor, room_no = _house_unique_fields(db, form, building)
    area_100 = parse_area(form.get("area"), "建筑面积")
    inner_area_100 = parse_area(form.get("inner_area"), "套内面积")
    type_id = parse_int(form.get("house_type_id"), "户型", 1, 10**9, required=False, default=None) or None
    status = form.get("status", "vacant")
    if status not in HOUSE_STATUS:
        raise UserError("房屋状态不正确")
    occupied_date = parse_date(form.get("occupied_date"), "入住日期")
    remark = clean_str(form.get("remark"), "备注", 300)
    if scalar(db, """SELECT COUNT(*) FROM house WHERE building_id=? AND unit=? AND floor=? AND room_no=?""",
              (building["id"], unit, floor, room_no)) > 0:
        raise UserError("房屋已存在：%s，同一楼栋+单元+楼层+房号不能重复" % house_label(building, unit, room_no))
    if type_id is not None:
        t = get_house_type(db, type_id)
        if t["community_id"] != community_id:
            raise UserError("所选户型不属于当前小区")
    owner_resident_id = _resolve_owner(db, community_id, form)
    cur = db.execute(
        """INSERT INTO house (community_id, building_id, unit, floor, room_no, area_100,
               inner_area_100, house_type_id, status, owner_resident_id, occupied_date, remark, parking_no)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (community_id, building["id"], unit, floor, room_no, area_100, inner_area_100,
         type_id, status, owner_resident_id, occupied_date, remark,
         clean_str(form.get("parking_no"), "车位号", 50)))
    if owner_resident_id:
        _link_owner(db, cur.lastrowid, owner_resident_id)
    log_op(db, "房屋", "新增房屋", "新增房屋 %s" % house_label(building, unit, room_no))
    return cur.lastrowid


def _resolve_owner(db, community_id, form):
    """表单里业主的三种填法：不填 / 选已有住户 / 新建（姓名+手机号）。"""
    mode = form.get("owner_mode", "none")
    if mode == "existing":
        rid = parse_int(form.get("owner_resident_id"), "业主", 1, 10**9)
        r = query_one(db, "SELECT * FROM resident WHERE id=?", (rid,))
        if not r:
            raise UserError("所选业主不存在，请重新选择")
        return rid
    if mode == "new":
        name = clean_str(form.get("owner_name"), "业主姓名", 50, required=True)
        phone = clean_str(form.get("owner_phone"), "业主手机号", 20)
        if phone:
            from utils import parse_phone
            phone = parse_phone(phone, "业主手机号")
        cur = db.execute(
            "INSERT INTO resident (name, phone) VALUES (?,?)", (name, phone))
        return cur.lastrowid
    return None


def _link_owner(db, house_id, resident_id, start_date=""):
    db.execute(
        """INSERT INTO resident_house (resident_id, house_id, role, is_current, is_living, start_date)
           VALUES (?,?,?,?,1,?)""", (resident_id, house_id, "owner", 1, start_date))


def update_house(db, hid, form):
    row = get_house_full(db, hid)
    building = get_building(db, row["building_id"])
    unit, floor, room_no = _house_unique_fields(db, form, building)
    area_100 = parse_area(form.get("area"), "建筑面积")
    inner_area_100 = parse_area(form.get("inner_area"), "套内面积")
    type_id = parse_int(form.get("house_type_id"), "户型", 1, 10**9, required=False, default=None) or None
    status = form.get("status", row["status"])
    if status not in HOUSE_STATUS:
        raise UserError("房屋状态不正确")
    occupied_date = parse_date(form.get("occupied_date"), "入住日期")
    remark = clean_str(form.get("remark"), "备注", 300)
    if scalar(db, """SELECT COUNT(*) FROM house WHERE building_id=? AND unit=? AND floor=? AND room_no=? AND id!=?""",
              (building["id"], unit, floor, room_no, hid)) > 0:
        raise UserError("房屋已存在：%s，同一楼栋+单元+楼层+房号不能重复" % house_label(building, unit, room_no))
    db.execute(
        """UPDATE house SET unit=?, floor=?, room_no=?, area_100=?, inner_area_100=?,
               house_type_id=?, status=?, occupied_date=?, remark=?, parking_no=? WHERE id=?""",
        (unit, floor, room_no, area_100, inner_area_100, type_id, status, occupied_date, remark,
         clean_str(form.get("parking_no"), "车位号", 50), hid))
    log_op(db, "房屋", "修改房屋", "修改房屋信息 %s" % house_label(building, unit, room_no))


def delete_house(db, hid):
    row = get_house_full(db, hid)
    bills = scalar(db, "SELECT COUNT(*) FROM bill WHERE house_id=?", (hid,))
    db.execute("DELETE FROM house WHERE id=?", (hid,))
    db.execute("""DELETE FROM resident WHERE id NOT IN
                  (SELECT DISTINCT resident_id FROM resident_house)""")
    log_op(db, "房屋", "删除房屋", "删除房屋 %s（含 %d 笔账单）" % (row["building_code"] and house_label(
        {"code": row["building_code"]}, row["unit"], row["room_no"]), bills))


def batch_generate(db, community_id, form):
    """批量生成整栋房屋。返回 (新建套数, 跳过套数)。"""
    building = get_building(db, parse_int(form.get("building_id"), "楼栋", 1, 10**9))
    if building["community_id"] != community_id:
        raise UserError("该楼栋不属于当前小区，请重新选择")
    units = parse_int(form.get("units"), "单元数", 1, min(building["units"], 20))
    floors = parse_int(form.get("floors"), "层数", 1, building["floors"])
    per_floor = parse_int(form.get("per_floor"), "每层户数", 1, 9)
    type_id = parse_int(form.get("house_type_id"), "户型", 1, 10**9, required=False, default=None) or None
    area_100 = parse_area(form.get("area"), "默认建筑面积")
    if type_id is not None:
        t = get_house_type(db, type_id)
        if t["community_id"] != community_id:
            raise UserError("所选户型不属于当前小区")
    created, skipped = 0, 0
    for unit in range(1, units + 1):
        for floor in range(1, floors + 1):
            for seq in range(1, per_floor + 1):
                room_no = floor * 100 + seq
                exists = scalar(db, """SELECT COUNT(*) FROM house
                    WHERE building_id=? AND unit=? AND floor=? AND room_no=?""",
                    (building["id"], unit, floor, room_no))
                if exists:
                    skipped += 1
                    continue
                db.execute(
                    """INSERT INTO house (community_id, building_id, unit, floor, room_no,
                           area_100, house_type_id, status) VALUES (?,?,?,?,?,?,?, 'vacant')""",
                    (community_id, building["id"], unit, floor, room_no, area_100, type_id))
                created += 1
    log_op(db, "房屋", "批量生成房屋",
           "在楼栋「%s」批量生成 %d 套房屋（跳过已存在 %d 套）" % (building["code"], created, skipped))
    return created, skipped


# ---------------------------------------------------------------- 车辆

def add_vehicle(db, hid, form):
    house = get_house_full(db, hid)
    plate = clean_str(form.get("plate"), "车牌号", 20, required=True)
    remark = clean_str(form.get("vehicle_remark"), "车辆备注", 200)
    if scalar(db, "SELECT COUNT(*) FROM vehicle WHERE house_id=? AND plate=?", (hid, plate)) > 0:
        raise UserError("这套房已经登记过车牌 %s 了" % plate)
    db.execute("INSERT INTO vehicle (house_id, plate, remark) VALUES (?,?,?)", (hid, plate, remark))
    log_op(db, "房屋", "登记车辆", "%s栋%d单元%d室 登记车辆 %s" % (
        str(house["building_code"]).replace("#", ""), house["unit"], house["room_no"], plate))


def delete_vehicle(db, vehicle_id):
    row = query_one(db, "SELECT * FROM vehicle WHERE id=?", (vehicle_id,))
    if not row:
        raise UserError("没有找到这辆车，可能已被删除，请刷新页面")
    db.execute("DELETE FROM vehicle WHERE id=?", (vehicle_id,))
    log_op(db, "房屋", "删除车辆", "删除车辆登记 %s（房屋 #%d）" % (row["plate"], row["house_id"]))


def transfer_owner(db, hid, form):
    """产权过户：旧业主转历史，新业主生效。"""
    house = get_house_full(db, hid)
    building = get_building(db, house["building_id"])
    old_resident = None
    if house["owner_resident_id"]:
        old_resident = query_one(db, "SELECT * FROM resident WHERE id=?", (house["owner_resident_id"],))
    date = parse_date(form.get("transfer_date"), "过户日期")
    new_id = None
    mode = form.get("owner_mode", "new")
    if mode == "existing":
        new_id = parse_int(form.get("owner_resident_id"), "新业主", 1, 10**9)
        if old_resident and new_id == old_resident["id"]:
            raise UserError("新业主和旧业主是同一个人，无需过户")
        if not query_one(db, "SELECT id FROM resident WHERE id=?", (new_id,)):
            raise UserError("所选的新业主不存在，请重新选择")
    else:
        name = clean_str(form.get("owner_name"), "新业主姓名", 50, required=True)
        phone = clean_str(form.get("owner_phone"), "新业主手机号", 20)
        if phone:
            from utils import parse_phone
            phone = parse_phone(phone, "新业主手机号")
        id_number = ""
        if clean_str(form.get("owner_id_number"), ""):
            from utils import parse_idcard
            id_number = parse_idcard(form.get("owner_id_number"), "新业主证件号")
        cur = db.execute(
            "INSERT INTO resident (name, phone, id_number) VALUES (?,?,?)", (name, phone, id_number))
        new_id = cur.lastrowid
    # 旧业主关系转历史
    if old_resident:
        db.execute(
            """UPDATE resident_house SET is_current=0, end_date=?
               WHERE house_id=? AND role='owner' AND is_current=1""", (date, hid))
    db.execute(
        """INSERT INTO resident_house (resident_id, house_id, role, is_current, is_living, start_date)
           VALUES (?,?,?,?,1,?)""", (new_id, hid, "owner", 1, date))
    db.execute("UPDATE house SET owner_resident_id=? WHERE id=?", (new_id, hid))
    new_r = query_one(db, "SELECT name FROM resident WHERE id=?", (new_id,))
    old_name = old_resident["name"] if old_resident else "（原无登记业主）"
    log_op(db, "住户", "产权过户", "%s 业主由「%s」变更为「%s」" % (
        house_label(building, house["unit"], house["room_no"]), old_name, new_r["name"]))
    return old_name, new_r["name"]
