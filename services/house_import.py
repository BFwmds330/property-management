# -*- coding: utf-8 -*-
"""业主名册 CSV 批量导入：下载模板 → 上传预览 → 确认导入。

模板列（可在 /house/import/template 下载示例）：
栋编号,单元,房号,建筑面积,车位号,业主姓名,业主电话,业主证件号,业主性别,业主工作单位,
家庭成员,租户,车辆,备注

- 家庭成员列：每组“姓名|电话|证件号|与业主关系”，多组用分号（；或 ;）分隔
- 租户列：每组“姓名|电话|证件号”，多组用分号分隔
- 车辆列：每组“车牌|备注（可写车位号/租期）”，多组用分号分隔
- 房号按“单元内序号”理解（如 1#栋1单元-3 号房 → 1栋1单元3室）
- 编码支持 UTF-8（含 BOM）与 GBK；推荐用 Excel 另存 CSV 后直接上传
"""
import csv
import io
import re

from database import log_op, query_all, query_one, scalar
from utils import (UserError, HOUSE_STATUS, clean_str, parse_area, parse_idcard,
                   parse_int, parse_phone)

REQUIRED_COLS = ["栋编号", "单元", "房号", "业主姓名"]


def read_import_csv(raw_bytes):
    """把上传的文件内容解析成行列表（自动识别 UTF-8/GBK 编码）。"""
    text = None
    for enc in ("utf-8-sig", "gbk"):
        try:
            text = raw_bytes.decode(enc)
            break
        except (UnicodeDecodeError, AttributeError):
            continue
    if text is None:
        raise UserError("无法识别文件编码：请用下载的模板填写后另存为 CSV 再上传")
    reader = csv.reader(io.StringIO(text))
    rows = [r for r in reader if any(x.strip() for x in r)]
    if not rows:
        raise UserError("文件是空的，请先在模板里填写数据")
    header = [h.strip() for h in rows[0]]
    missing = [c for c in REQUIRED_COLS if c not in header]
    if missing:
        raise UserError("表头缺少必需列：%s。请下载最新模板填写（不要修改表头）" % "、".join(missing))
    idx = {h: i for i, h in enumerate(header)}
    out = []
    for lineno, r in enumerate(rows[1:], start=2):
        get = lambda name: (r[idx[name]].strip() if idx.get(name) is not None
                            and idx[name] < len(r) else "")
        if not any(x.strip() for x in r):
            continue
        out.append((lineno, {
            "栋编号": get("栋编号"), "单元": get("单元"), "房号": get("房号"),
            "建筑面积": get("建筑面积"), "车位号": get("车位号"),
            "业主姓名": get("业主姓名"), "业主电话": get("业主电话"),
            "业主证件号": get("业主证件号"), "业主性别": get("业主性别"),
            "业主工作单位": get("业主工作单位"),
            "家庭成员": get("家庭成员"), "租户": get("租户"),
            "车辆": get("车辆"), "备注": get("备注"),
        }))
    if not out:
        raise UserError("没有读到任何数据行，请检查文件内容")
    return out


def _groups(text):
    """按分号拆成多组。"""
    if not text:
        return []
    return [p.strip() for p in re.split(r"[；;]", text) if p.strip()]


def _person_group(group, lineno, field):
    """解析一组“姓名|电话|证件号|关系”。"""
    segs = [s.strip() for s in group.split("|")]
    while len(segs) < 4:
        segs.append("")
    name = segs[0]
    if not name:
        raise UserError("第 %d 行：%s 里有一组缺少姓名" % (lineno, field))
    if len(name) > 50:
        raise UserError("第 %d 行：%s 里姓名太长：%s…" % (lineno, field, name[:10]))
    phone = parse_phone(segs[1], "%s-%s 的手机号" % (field, name)) if segs[1] else ""
    idno = parse_idcard(segs[2], "%s-%s 的证件号" % (field, name)) if segs[2] else ""
    return {"name": name, "phone": phone, "id_number": idno, "relation": segs[3]}


def _vehicle_group(group, lineno):
    segs = [s.strip() for s in group.split("|")]
    plate = segs[0]
    if not plate or len(plate) > 20:
        raise UserError("第 %d 行：车辆组的车牌号不合法：%r" % (lineno, plate))
    return {"plate": plate, "remark": segs[1] if len(segs) > 1 else ""}


def parse_rows(rows):
    """把原始行解析成结构化数据，返回 (records, errors)。"""
    records, errors = [], []
    for lineno, row in rows:
        rec = {"lineno": lineno, "errors": [], "members": [], "tenants": [],
               "vehicles": [], "remark": row["备注"]}
        try:
            rec["building_code"] = _normalize_building(row["栋编号"])
            rec["unit"] = parse_int(row["单元"], "单元", 1, 99)
            rec["room_no"] = parse_int(row["房号"], "房号", 1, 9999)
            rec["area_100"] = parse_area(row["建筑面积"], "建筑面积") if row["建筑面积"] else 0
            rec["parking_no"] = clean_str(row["车位号"], "车位号", 50)
            rec["owner"] = _person_group(row["业主姓名"], lineno, "业主")
            if row["业主姓名"] and row["业主电话"]:
                rec["owner"]["phone"] = parse_phone(row["业主电话"], "业主手机号")
            elif row["业主电话"]:
                raise UserError("第 %d 行：填写了业主电话但没有业主姓名" % lineno)
            rec["owner"]["id_number"] = (parse_idcard(row["业主证件号"], "业主证件号")
                                         if row["业主证件号"] else "")
            gender = (row["业主性别"] or "").strip()
            if gender not in ("", "男", "女"):
                raise UserError("第 %d 行：业主性别只能是 男 / 女 / 空" % lineno)
            rec["owner"]["gender"] = gender
            rec["owner"]["work_unit"] = clean_str(row["业主工作单位"], "业主工作单位", 100)
            for g in _groups(row["家庭成员"]):
                rec["members"].append(_person_group(g, lineno, "家庭成员"))
            for g in _groups(row["租户"]):
                rec["tenants"].append(_person_group(g, lineno, "租户"))
            for g in _groups(row["车辆"]):
                rec["vehicles"].append(_vehicle_group(g, lineno))
        except UserError as e:
            errors.append(str(e))
            continue
        records.append(rec)
    return records, errors


def _normalize_building(text):
    code = clean_str(text, "栋编号", 20, required=True)
    code = code.replace("＃", "#").replace("＃", "#").strip()
    if re.fullmatch(r"\d+", code):
        code += "#"
    return code


def _building_units_floors(records):
    """统计每栋的最大单元/房号，用于建楼栋。"""
    stats = {}
    for r in records:
        s = stats.setdefault(r["building_code"], {"units": 1, "floors": 1})
        s["units"] = max(s["units"], r["unit"])
        s["floors"] = max(s["floors"], min(r["room_no"], 99))
    return stats


def _get_or_create_building(db, community_id, code, units, floors, created):
    row = query_one(db, "SELECT * FROM building WHERE community_id=? AND code=?",
                    (community_id, code))
    if row:
        # 已有楼栋范围不够时自动扩到能容纳导入的房屋
        if row["units"] < units or row["floors"] < floors:
            db.execute("UPDATE building SET units=?, floors=? WHERE id=?",
                       (max(row["units"], units), max(row["floors"], floors), row["id"]))
            created["buildings_updated"] += 1
        return row["id"]
    cur = db.execute(
        "INSERT INTO building (community_id, code, units, floors, has_elevator, remark) "
        "VALUES (?,?,?,?,0,'名册导入')", (community_id, code, units, floors))
    created["buildings_new"] += 1
    return cur.lastrowid


def _house_key_exists(db, building_id, unit, room_no):
    return query_one(db, "SELECT id FROM house WHERE building_id=? AND unit=? AND room_no=?",
                     (building_id, unit, room_no))


def _ensure_person(db, name, phone, id_number, gender="", work_unit=""):
    """按 姓名+电话/证件号 查找已有住户，避免重复建档。"""
    row = None
    if id_number:
        row = query_one(db, "SELECT * FROM resident WHERE id_number=? AND id_number!=''", (id_number,))
    if not row and phone:
        row = query_one(db, "SELECT * FROM resident WHERE phone=? AND phone!=''", (phone,))
    if not row:
        row = query_one(db, """SELECT * FROM resident WHERE name=? AND phone='' AND id_number='' """, (name,))
    if row:
        # 补全空缺信息
        db.execute("""UPDATE resident SET phone=CASE WHEN phone='' THEN ? ELSE phone END,
                      id_number=CASE WHEN id_number='' THEN ? ELSE id_number END,
                      gender=CASE WHEN gender='' THEN ? ELSE gender END,
                      work_unit=CASE WHEN work_unit='' THEN ? ELSE work_unit END
                      WHERE id=?""", (phone, id_number, gender, work_unit, row["id"]))
        return row["id"], False
    cur = db.execute(
        """INSERT INTO resident (name, gender, birth_date, id_number, phone, work_unit)
           VALUES (?,?,?,?,?,?)""", (name, gender, "", id_number, phone, work_unit))
    return cur.lastrowid, True


def analyze_import(db, community_id, records):
    """预览统计：不写库，只算清楚会新建/更新什么。"""
    stats = _building_units_floors(records)
    buildings_new, houses_new, houses_exist = 0, 0, 0
    for code in stats:
        if not query_one(db, "SELECT id FROM building WHERE community_id=? AND code=?",
                         (community_id, code)):
            buildings_new += 1
    for r in records:
        if r["errors"]:
            continue
        b = query_one(db, "SELECT id FROM building WHERE community_id=? AND code=?",
                      (community_id, r["building_code"]))
        if b and _house_key_exists(db, b["id"], r["unit"], r["room_no"]):
            houses_exist += 1
        else:
            houses_new += 1
    return {
        "buildings_new": buildings_new,
        "houses_new": houses_new,
        "houses_exist": houses_exist,
        "owners": sum(1 for r in records if not r["errors"]),
        "members": sum(len(r["members"]) for r in records if not r["errors"]),
        "tenants": sum(len(r["tenants"]) for r in records if not r["errors"]),
        "vehicles": sum(len(r["vehicles"]) for r in records if not r["errors"]),
    }


def execute_import(db, community_id, records):
    """执行导入。返回计数 dict。"""
    created = {"buildings_new": 0, "buildings_updated": 0, "houses_new": 0,
               "houses_updated": 0, "owners": 0, "members": 0, "tenants": 0,
               "vehicles": 0, "skipped": 0}
    stats = _building_units_floors(records)
    building_ids = {code: _get_or_create_building(db, community_id, code, s["units"], s["floors"], created)
                    for code, s in stats.items()}
    for r in records:
        if r["errors"]:
            created["skipped"] += 1
            continue
        bid = building_ids[r["building_code"]]
        floor = min(r["room_no"], 99)
        house = query_one(db, "SELECT * FROM house WHERE building_id=? AND unit=? AND room_no=?",
                          (bid, r["unit"], r["room_no"]))
        if house:
            hid = house["id"]
            # 只补空缺，不覆盖用户已填的数据
            db.execute("""UPDATE house SET area_100=CASE WHEN area_100=0 THEN ? ELSE area_100 END,
                          parking_no=CASE WHEN (parking_no IS NULL OR parking_no='') THEN ? ELSE parking_no END,
                          remark=CASE WHEN remark='' THEN ? ELSE remark END
                          WHERE id=?""",
                       (r["area_100"], r["parking_no"], r["remark"], hid))
            created["houses_updated"] += 1
        else:
            status = "vacant"
            if r["tenants"]:
                status = "rent"
            elif r["owner"]:
                status = "self"
            cur = db.execute(
                """INSERT INTO house (community_id, building_id, unit, floor, room_no,
                       area_100, parking_no, status, remark) VALUES (?,?,?,?,?,?,?,?,?)""",
                (community_id, bid, r["unit"], floor, r["room_no"],
                 r["area_100"], r["parking_no"], status, r["remark"]))
            hid = cur.lastrowid
            created["houses_new"] += 1
        # 业主
        if r["owner"]:
            has_owner = scalar(db, """SELECT COUNT(*) FROM resident_house
                WHERE house_id=? AND role='owner' AND is_current=1""", (hid,))
            if has_owner:
                created["skipped"] += 1      # 已有业主则不重复登记
            else:
                rid, _ = _ensure_person(db, r["owner"]["name"], r["owner"]["phone"],
                                        r["owner"]["id_number"], r["owner"]["gender"],
                                        r["owner"]["work_unit"])
                db.execute("""INSERT INTO resident_house (resident_id, house_id, role, is_current,
                              is_living, start_date) VALUES (?,?,?,1,1,'')""",
                           (rid, hid, "owner"))
                db.execute("UPDATE house SET owner_resident_id=?, status=CASE WHEN status='vacant' THEN 'self' ELSE status END WHERE id=?",
                           (rid, hid))
                created["owners"] += 1
        # 家庭成员 / 租户
        for m in r["members"]:
            rid, _ = _ensure_person(db, m["name"], m["phone"], m["id_number"])
            exists = scalar(db, """SELECT COUNT(*) FROM resident_house
                WHERE house_id=? AND resident_id=? AND is_current=1""", (hid, rid))
            if not exists:
                db.execute("""INSERT INTO resident_house (resident_id, house_id, role, relation,
                              is_current, is_living) VALUES (?,?,?,?,1,1)""",
                           (rid, hid, "member", m["relation"]))
                created["members"] += 1
        for t in r["tenants"]:
            rid, _ = _ensure_person(db, t["name"], t["phone"], t["id_number"])
            exists = scalar(db, """SELECT COUNT(*) FROM resident_house
                WHERE house_id=? AND resident_id=? AND role='tenant' AND is_current=1""", (hid, rid))
            if not exists:
                db.execute("""INSERT INTO resident_house (resident_id, house_id, role, is_current,
                              is_living) VALUES (?,?,?,1,1)""", (rid, hid, "tenant"))
                db.execute("UPDATE house SET status='rent' WHERE id=? AND status IN ('vacant','self')",
                           (hid,))
                created["tenants"] += 1
        # 车辆
        for v in r["vehicles"]:
            exists = scalar(db, "SELECT COUNT(*) FROM vehicle WHERE house_id=? AND plate=?",
                            (hid, v["plate"]))
            if not exists:
                db.execute("INSERT INTO vehicle (house_id, plate, remark) VALUES (?,?,?)",
                           (hid, v["plate"], v["remark"]))
                created["vehicles"] += 1
    total = sum(created[k] for k in ("houses_new", "houses_updated"))
    log_op(db, "房屋", "导入业主名册",
           "导入 %d 行：新建房屋 %d 套、更新 %d 套、业主 %d 人、家庭成员 %d 人、租户 %d 人、车辆 %d 辆"
           % (len(records), created["houses_new"], created["houses_updated"], created["owners"],
              created["members"], created["tenants"], created["vehicles"]))
    return created


IMPORT_TEMPLATE_ROWS = [
    ["栋编号", "单元", "房号", "建筑面积", "车位号", "业主姓名", "业主电话", "业主证件号",
     "业主性别", "业主工作单位", "家庭成员", "租户", "车辆", "备注"],
    ["1", "1", "1", "89.50", "12东", "张示例", "13800001111", "", "男", "示例单位",
     "李示例|13800002222||配偶；张小孩|13800004444||子女", "王租户|13800003333",
     "皖A11111|地下12号，25.1.1-27.12.31", "示例数据，导入前请删除本行"],
    ["1", "1", "2", "75.20", "", "刘示例", "13800005555", "", "女", "",
     "", "", "皖A22222|无车位", ""],
]
