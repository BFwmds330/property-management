# -*- coding: utf-8 -*-
"""小区管理：新增 / 修改 / 删除 / 搜索，以及删除前的级联影响统计。"""
from database import log_op, query_all, query_one, scalar
from utils import UserError, clean_str, parse_area, parse_date, parse_int


def list_communities(db, keyword=""):
    """小区列表（可按名称/地址模糊搜索），附带每个小区的统计数字。"""
    kw = f"%{keyword.strip()}%"
    rows = query_all(
        db, """SELECT * FROM community
               WHERE name LIKE ? OR address LIKE ?
               ORDER BY is_demo, id""",
        (kw, kw))
    stats = {
        s["community_id"]: s
        for s in query_all(db, """
            SELECT c.id AS community_id,
                   (SELECT COUNT(*) FROM house h WHERE h.community_id = c.id) AS houses,
                   (SELECT COUNT(*) FROM building b WHERE b.community_id = c.id) AS buildings,
                   (SELECT COUNT(*) FROM house h WHERE h.community_id = c.id
                        AND h.status IN ('self','rent')) AS occupied,
                   (SELECT COALESCE(SUM(b.amount_receivable - b.amount_received), 0)
                        FROM bill b JOIN house h ON h.id = b.house_id
                        WHERE h.community_id = c.id AND b.status != 'void') AS owed_fen
            FROM community c""")
    }
    result = []
    for r in rows:
        item = dict(r)
        s = stats.get(r["id"])
        item["houses"] = s["houses"] if s else 0
        item["buildings_real"] = s["buildings"] if s else 0
        item["occupied"] = s["occupied"] if s else 0
        item["owed_fen"] = s["owed_fen"] if s else 0
        result.append(item)
    return result


def get_community(db, cid):
    row = query_one(db, "SELECT * FROM community WHERE id=?", (cid,))
    if not row:
        raise UserError("没有找到这个小区，可能已被删除，请刷新页面")
    return row


def name_taken(db, name, exclude_id=None):
    if exclude_id:
        return scalar(db, "SELECT COUNT(*) FROM community WHERE name=? AND id!=?", (name, exclude_id)) > 0
    return scalar(db, "SELECT COUNT(*) FROM community WHERE name=?", (name,)) > 0


def _fill_community_fields(db, form, is_new):
    data = {}
    data["name"] = clean_str(form.get("name"), "小区名称", 50, required=True)
    data["address"] = clean_str(form.get("address"), "详细地址", 200)
    data["building_count"] = parse_int(form.get("building_count"), "楼栋数量", 0, 999, required=False, default=0)
    data["parking_info"] = clean_str(form.get("parking_info"), "车位情况", 200)
    data["delivery_date"] = parse_date(form.get("delivery_date"), "房屋交付日期")
    data["takeover_date"] = parse_date(form.get("takeover_date"), "物业接管日期")
    data["default_unit_price"] = parse_area(
        form.get("default_unit_price"), "物业费默认单价（元/㎡/月）")
    data["remark"] = clean_str(form.get("remark"), "备注", 500)
    return data


def add_community(db, form, is_demo=0):
    data = _fill_community_fields(db, form, True)
    if name_taken(db, data["name"]):
        raise UserError("已经有叫“%s”的小区了，小区名称不能重复" % data["name"])
    cur = db.execute(
        """INSERT INTO community (name, address, building_count, parking_info,
               delivery_date, takeover_date, default_unit_price, remark, is_demo)
           VALUES (?,?,?,?,?,?,?,?,?)""",
        (data["name"], data["address"], data["building_count"], data["parking_info"],
         data["delivery_date"], data["takeover_date"], data["default_unit_price"],
         data["remark"], is_demo))
    log_op(db, "小区", "新增小区", "新增小区「%s」" % data["name"], is_demo)
    return cur.lastrowid


def update_community(db, cid, form):
    row = get_community(db, cid)
    data = _fill_community_fields(db, form, False)
    if name_taken(db, data["name"], cid):
        raise UserError("已经有叫“%s”的小区了，小区名称不能重复" % data["name"])
    db.execute(
        """UPDATE community SET name=?, address=?, building_count=?, parking_info=?,
               delivery_date=?, takeover_date=?, default_unit_price=?, remark=? WHERE id=?""",
        (data["name"], data["address"], data["building_count"], data["parking_info"],
         data["delivery_date"], data["takeover_date"], data["default_unit_price"],
         data["remark"], cid))
    log_op(db, "小区", "修改小区", "修改小区「%s」" % data["name"])


def delete_impact(db, cid):
    """删除小区会影响的数据量，用于二次确认弹窗。"""
    return {
        "buildings": scalar(db, "SELECT COUNT(*) FROM building WHERE community_id=?", (cid,)),
        "houses": scalar(db, "SELECT COUNT(*) FROM house WHERE community_id=?", (cid,)),
        "bills": scalar(db, """SELECT COUNT(*) FROM bill b JOIN house h ON h.id=b.house_id
                               WHERE h.community_id=?""", (cid,)),
        "residents": scalar(db, """SELECT COUNT(DISTINCT rh.resident_id) FROM resident_house rh
                                   JOIN house h ON h.id=rh.house_id WHERE h.community_id=?""", (cid,)),
    }


def delete_community(db, cid):
    row = get_community(db, cid)
    impact = delete_impact(db, cid)
    db.execute("DELETE FROM community WHERE id=?", (cid,))
    # 删除后，不再属于任何房屋的住户（人）一并清理，避免留下孤儿数据
    db.execute("""DELETE FROM resident WHERE id NOT IN
                  (SELECT DISTINCT resident_id FROM resident_house)""")
    log_op(db, "小区", "删除小区",
           "删除小区「%s」（含 %d 栋楼、%d 套房屋、%d 笔账单）"
           % (row["name"], impact["buildings"], impact["houses"], impact["bills"]))
    return impact
