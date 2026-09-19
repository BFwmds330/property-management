# -*- coding: utf-8 -*-
"""演示数据：一键载入 2 个小区的完整练习数据，可一键清空（按 is_demo 标记删除）。

所有数据都是虚构的：姓名来自常见姓名池，手机号使用 1380000xxxx 段，
身份证号按规则生成（校验位合法但纯属演示）。
"""
from datetime import date, timedelta

from utils import idcard_check_digit, month_add, this_month

NAME_POOL = [
    "张伟", "王芳", "李强", "赵敏", "刘洋", "陈静", "杨帆", "黄丽", "周涛", "吴敏",
    "徐磊", "孙丽", "马超", "朱婷", "胡军", "郭燕", "林峰", "何平", "高翔", "罗娜",
    "郑波", "梁雪", "谢东", "宋佳", "唐磊", "韩梅", "冯刚", "曹颖", "彭飞", "董洁",
]

UNIT_NAMES = ["阳光物业服务中心", "阳光科技", "诚和贸易", "蓝天设计", "百安居建材", "安泰物流"]


def _ins(db, sql, args):
    return db.execute(sql, args).lastrowid


def _gen_id(db, seq, birth, gender):
    """生成一个格式合法的演示身份证号（110101 开头，校验位正确）。"""
    y, m, d = birth
    first17 = "110101%04d%02d%02d%03d" % (y, m, d, seq * 2 + (1 if gender == "男" else 0))
    return first17 + idcard_check_digit(first17)


def _phone(i):
    return "1380000%04d" % (1000 + i)


def _birth(i, base_year=1958):
    y = base_year + (i * 7) % 38
    m = (i * 5) % 12 + 1
    d = (i * 11) % 27 + 1
    return y, m, d


def _date_str(t):
    return t.strftime("%Y-%m-%d")


def build_demo(db):
    today = date.today()
    soon = today + timedelta(days=18)

    # ------------------------------------------------ 小区 1：阳光花园
    c1 = _ins(db, """INSERT INTO community (name, address, building_count, parking_info,
        delivery_date, takeover_date, default_unit_price, remark, is_demo)
        VALUES (?,?,?,?,?,?,?,?,1)""",
        ("阳光花园（演示）", "幸福路 88 号", 3, "地上车位 80 个、地下车位 120 个",
         "2021-06-30", "2021-07-01", 220, "演示数据：2021 年接管，共 3 栋楼"))

    t1 = _ins(db, """INSERT INTO house_type (community_id, name, rooms, halls, baths, area_100,
        orientation, has_balcony, remark) VALUES (?,?,?,?,?,?,?,?,?)""",
        (c1, "两室两厅一卫", 2, 2, 1, 7550, "南北", 1, "小户型"))
    t2 = _ins(db, """INSERT INTO house_type (community_id, name, rooms, halls, baths, area_100,
        orientation, has_balcony, remark) VALUES (?,?,?,?,?,?,?,?,?)""",
        (c1, "三室两厅两卫", 3, 2, 2, 10520, "南北", 1, ""))
    t3 = _ins(db, """INSERT INTO house_type (community_id, name, rooms, halls, baths, area_100,
        orientation, has_balcony, remark) VALUES (?,?,?,?,?,?,?,?,?)""",
        (c1, "三室两厅两卫 A 户型", 3, 2, 2, 11880, "南", 1, "边户"))

    types = [t1, t2, t3]
    buildings = {}
    for code, units, elevator in (("1#", 2, 1), ("2#", 2, 1), ("3#", 1, 0)):
        buildings[code] = _ins(db, """INSERT INTO building (community_id, code, units, floors,
            has_elevator, remark) VALUES (?,?,?,?,?,?)""", (c1, code, units, 6, elevator, ""))

    # 批量生成房屋：1#、2# 各 2 单元，3# 1 个单元；每栋 6 层、每层 2 户
    houses = []          # (house_id, building_code, unit, room_no, floor)
    for code, unit_list in (("1#", (1, 2)), ("2#", (1, 2)), ("3#", (1,))):
        for unit in unit_list:
            for floor in range(1, 7):
                for seq in range(1, 3):
                    room_no = floor * 100 + seq
                    type_id = types[(floor + seq) % 3]
                    area = {t1: 7550, t2: 10520, t3: 11880}[type_id]
                    hid = _ins(db, """INSERT INTO house (community_id, building_id, unit, floor,
                        room_no, area_100, house_type_id, status) VALUES (?,?,?,?,?,?,?,'vacant')""",
                        (c1, buildings[code], unit, floor, room_no, area, type_id))
                    houses.append((hid, code, unit, room_no, floor))

    # ------------------------------------------------ 住户：业主 / 家庭成员 / 租户
    # 前 34 套（1#、2# 全部）登记业主；其中 3 套出租；其余空置
    resident_rows = []
    rid_seq = 0
    rent_houses = {idx for idx in (5, 17, 26)}     # 出租的房屋下标
    for idx, (hid, code, unit, room_no, floor) in enumerate(houses[:34]):
        name = NAME_POOL[idx % len(NAME_POOL)]
        gender = "男" if idx % 2 == 0 else "女"
        by, bm, bd = _birth(idx)
        birth = "%04d-%02d-%02d" % (by, bm, bd)
        idno = _gen_id(db, idx, (by, bm, bd), gender)
        rid = _ins(db, """INSERT INTO resident (name, gender, birth_date, id_number, phone,
            work_unit, emergency_name, emergency_phone, remark) VALUES (?,?,?,?,?,?,?,?,?)""",
            (name, gender, birth, idno, _phone(rid_seq),
             UNIT_NAMES[idx % len(UNIT_NAMES)],
             NAME_POOL[(idx + 7) % len(NAME_POOL)], _phone(rid_seq + 100),
             "演示数据" if idx % 9 == 0 else ""))
        rid_seq += 1
        occupied = "%04d-%02d-%02d" % (2021 + idx % 4, (idx % 12) + 1, (idx % 27) + 1)
        status = "rent" if idx in rent_houses else ("vacant" if idx % 11 == 7 else "self")
        db.execute("UPDATE house SET status=?, owner_resident_id=?, occupied_date=? WHERE id=?",
                   (status, rid, occupied, hid))
        _ins(db, """INSERT INTO resident_house (resident_id, house_id, role, is_current, is_living,
            start_date) VALUES (?, ?, 'owner', 1, 1, ?)""", (rid, hid, occupied))
        resident_rows.append((rid, name, gender, birth))

    # 演示一位近期生日的业主（12 天后）
    bday = today + timedelta(days=12)
    rid0 = resident_rows[2][0]
    db.execute("UPDATE resident SET birth_date=? WHERE id=?",
               ("1980-%02d-%02d" % (bday.month, bday.day), rid0))

    # 家庭成员：每逢下标是 3 的倍数的业主，配一名配偶；是 5 的倍数的，再加一名子女
    member_seq = 0
    for idx, (rid, name, gender, birth) in enumerate(resident_rows):
        hid = houses[idx][0]
        if idx % 3 == 0:
            member_seq += 1
            sp_name = NAME_POOL[(idx + 13) % len(NAME_POOL)]
            sp_gender = "女" if gender == "男" else "男"
            by = _birth(idx + 3)
            sp_id = _ins(db, """INSERT INTO resident (name, gender, birth_date, id_number, phone)
                VALUES (?,?,?,?,?)""",
                (sp_name, sp_gender, "%04d-%02d-%02d" % by, _gen_id(db, 200 + member_seq, by, sp_gender),
                 _phone(rid_seq)))
            rid_seq += 1
            _ins(db, """INSERT INTO resident_house (resident_id, house_id, role, relation, is_current,
                is_living, start_date) VALUES (?,?,?,?,1,1,?)""",
                (sp_id, hid, "member", "配偶", "2021-08-01"))
        if idx % 5 == 0:
            member_seq += 1
            ch_name = NAME_POOL[(idx + 23) % len(NAME_POOL)]
            ch_gender = "男" if idx % 2 == 0 else "女"
            ch_birth = today + timedelta(days=25)          # 演示：近期生日
            ch_year = 2012
            ch_id = _ins(db, """INSERT INTO resident (name, gender, birth_date, id_number, phone)
                VALUES (?,?,?,?,?)""",
                (ch_name, ch_gender, "%04d-%02d-%02d" % (ch_year, ch_birth.month, ch_birth.day),
                 _gen_id(db, 300 + member_seq, (ch_year, ch_birth.month, ch_birth.day), ch_gender),
                 _phone(rid_seq)))
            rid_seq += 1
            _ins(db, """INSERT INTO resident_house (resident_id, house_id, role, relation, is_current,
                is_living, start_date) VALUES (?,?,?,?,1,1,?)""",
                (ch_id, hid, "member", "子女", "2021-08-01"))

    # 租户：3 套出租房
    rent_idx = sorted(rent_houses)
    lease_plans = [
        (rent_idx[0], month_add(this_month(), -6), (today + timedelta(days=18)).isoformat(), 1800),
        (rent_idx[1], (today - timedelta(days=165)).isoformat(), (today + timedelta(days=200)).isoformat(), 2200),
        (rent_idx[2], month_add(this_month(), -2), (today + timedelta(days=90)).isoformat(), 1500),
    ]
    for k, (hidx, ls, le, rent) in enumerate(lease_plans):
        hid = houses[hidx][0]
        name = NAME_POOL[(k + 27) % len(NAME_POOL)]
        gender = "女" if k % 2 == 0 else "男"
        by = _birth(40 + k, 1985)
        tid = _ins(db, """INSERT INTO resident (name, gender, birth_date, id_number, phone)
            VALUES (?,?,?,?,?)""",
            (name, gender, "%04d-%02d-%02d" % by, _gen_id(db, 400 + k, by, gender), _phone(rid_seq)))
        rid_seq += 1
        _ins(db, """INSERT INTO resident_house (resident_id, house_id, role, relation, is_current,
            is_living, start_date, lease_start, lease_end, monthly_rent)
            VALUES (?,?,?,?,1,1,?,?,?,?)""",
            (tid, hid, "tenant", "", (today - timedelta(days=30)).isoformat(), ls, le, rent * 100))

    # ------------------------------------------------ 收费项目
    f_wuye = _ins(db, """INSERT INTO fee_item (community_id, name, pricing_mode, unit_price, cycle,
        enabled, remark) VALUES (?,?,?,?,?,1,?)""",
        (c1, "物业费", "area", 220, "month", "按建筑面积 2.20 元/㎡/月"))
    f_gongtan = _ins(db, """INSERT INTO fee_item (community_id, name, pricing_mode, unit_price, cycle,
        enabled, remark) VALUES (?,?,?,?,?,1,?)""",
        (c1, "公摊水电费", "fixed", 1500, "month", "每户每月 15 元"))
    f_chewei = _ins(db, """INSERT INTO fee_item (community_id, name, pricing_mode, unit_price, cycle,
        enabled, remark) VALUES (?,?,?,?,?,1,?)""",
        (c1, "车位费", "fixed", 15000, "quarter", "每户每季度 150 元，地下车位"))

    # ------------------------------------------------ 账单 + 缴费（近 3 个月）
    month = this_month()
    months = [month_add(month, -2), month_add(month, -1), month]
    methods = ["现金", "转账", "扫码"]
    receipt_no = 0

    def pay_day(ym):
        day = 10 if ym != month else min(10, today.day)
        return "%s-%02d" % (ym, day)

    for mi, ym in enumerate(months):
        for idx, (hid, code, unit, room_no, floor) in enumerate(houses):
            hrow = db.execute("SELECT area_100 FROM house WHERE id=?", (hid,)).fetchone()
            area_100 = hrow["area_100"]
            amount_wuye = (area_100 * 220 + 50) // 100
            amount_gongtan = 1500
            b1 = _ins(db, """INSERT INTO bill (house_id, fee_item_id, period, period_start, months,
                amount_receivable, amount_received, status) VALUES (?,?,?,?,?,?,0,'unpaid')""",
                (hid, f_wuye, ym, ym, 1, amount_wuye))
            b2 = _ins(db, """INSERT INTO bill (house_id, fee_item_id, period, period_start, months,
                amount_receivable, amount_received, status) VALUES (?,?,?,?,?,?,0,'unpaid')""",
                (hid, f_gongtan, ym, ym, 1, amount_gongtan))
            paid = (mi == 0 and idx % 10 != 9) or (mi == 1 and idx % 3 != 2) or (mi == 2 and idx % 8 == 0)
            if paid:
                for b, amt in ((b1, amount_wuye), (b2, amount_gongtan)):
                    receipt_no += 1
                    db.execute("""INSERT INTO payment (bill_id, amount, pay_date, method, receipt_no)
                        VALUES (?,?,?,?,?)""",
                        (b, amt, pay_day(ym), methods[(idx + mi) % 3],
                         ("SK%05d" % receipt_no) if receipt_no % 2 == 0 else ""))
                    db.execute("UPDATE bill SET amount_received=?, status='paid' WHERE id=?", (amt, b))
            # 演示一笔“部分缴纳”
            if mi == 1 and idx == 3:
                half = amount_wuye // 2 // 100 * 100
                db.execute("""INSERT INTO payment (bill_id, amount, pay_date, method, remark)
                    VALUES (?,?,?,?,?)""", (b1, half, pay_day(ym), "转账", "先缴一半，余款下月补齐"))
                db.execute("UPDATE bill SET amount_received=?, status='partial' WHERE id=?", (half, b1))
            # 演示一笔“减免调整”
            if mi == 1 and idx == 9:
                after = amount_gongtan - 500
                db.execute("INSERT INTO bill_adjust (bill_id, before_amount, after_amount, reason) "
                           "VALUES (?,?,?,?)", (b2, amount_gongtan, after, "长期出差空置，协商减免 5 元"))
                db.execute("UPDATE bill SET amount_receivable=? WHERE id=?", (after, b2))

    # 车位费：本季度账单，仅部分房屋有车位
    q = month[:4] + "-Q" + str((int(month[5:7]) - 1) // 3 + 1)
    q_start = month[:4] + "-%02d" % ((int(q[-1]) - 1) * 3 + 1)
    for idx, (hid, *_rest) in enumerate(houses):
        if idx % 4 != 0:
            continue
        b = _ins(db, """INSERT INTO bill (house_id, fee_item_id, period, period_start, months,
            amount_receivable, amount_received, status) VALUES (?,?,?,?,?,?,0,'unpaid')""",
            (hid, f_chewei, q, q_start, 3, 45000))
        if idx % 8 == 0:
            db.execute("""INSERT INTO payment (bill_id, amount, pay_date, method) VALUES (?,?,?,?)""",
                (b, 45000, pay_day(month), "扫码"))
            db.execute("UPDATE bill SET amount_received=45000, status='paid' WHERE id=?", (b,))

    # ------------------------------------------------ 报修
    h1 = houses[3][0]
    h2 = houses[15][0]
    _ins(db, "INSERT INTO repair (community_id, house_id, content, status, handler_note) VALUES (?,?,?,?,?)",
         (c1, h1, "厨房下水道堵塞，返水", "pending", ""))
    _ins(db, "INSERT INTO repair (community_id, house_id, content, status, handler_note) VALUES (?,?,?,?,?)",
         (c1, h2, "入户门锁损坏，无法反锁", "processing", "已联系师傅，明天上午上门"))
    _ins(db, "INSERT INTO repair (community_id, house_id, content, status, handler_note) VALUES (?,?,?,?,?)",
         (c1, None, "3 栋楼道照明灯不亮", "done", "已更换灯管"))

    # ------------------------------------------------ 小区 2：翡翠湾（小体量，用于对比）
    c2 = _ins(db, """INSERT INTO community (name, address, building_count, parking_info,
        delivery_date, takeover_date, default_unit_price, remark, is_demo)
        VALUES (?,?,?,?,?,?,?,?,1)""",
        ("翡翠湾（演示）", "临湖路 12 号", 1, "地面车位 40 个",
         "2019-12-01", "2020-01-01", 200, "演示数据：老小区，仅 1 栋"))
    b6 = _ins(db, "INSERT INTO building (community_id, code, units, floors, has_elevator, remark) "
                  "VALUES (?,?,?,?,?,?)", (c2, "6#", 1, 6, 0, "无电梯"))
    fw = _ins(db, """INSERT INTO fee_item (community_id, name, pricing_mode, unit_price, cycle,
        enabled, remark) VALUES (?,?,?,?,?,1,?)""", (c2, "物业费", "area", 200, "month", "2.00 元/㎡/月"))
    c2_houses = []
    for floor in range(1, 7):
        for seq in range(1, 3):
            room_no = floor * 100 + seq
            hid = _ins(db, """INSERT INTO house (community_id, building_id, unit, floor, room_no,
                area_100, house_type_id, status) VALUES (?,?,?,?,?,?,NULL,'vacant')""",
                (c2, b6, 1, floor, room_no, 8000 + floor * 100))
            c2_houses.append(hid)
    for k, hid in enumerate(c2_houses[::2]):        # 一半房屋登记业主
        name = NAME_POOL[(k + 5) % len(NAME_POOL)]
        gender = "男" if k % 2 == 0 else "女"
        by = _birth(60 + k, 1960)
        rid = _ins(db, """INSERT INTO resident (name, gender, birth_date, id_number, phone)
            VALUES (?,?,?,?,?)""",
            (name, gender, "%04d-%02d-%02d" % by, _gen_id(db, 500 + k, by, gender), _phone(200 + k)))
        db.execute("UPDATE house SET status='self', owner_resident_id=?, occupied_date='2020-03-01' WHERE id=?",
                   (rid, hid))
        _ins(db, "INSERT INTO resident_house (resident_id, house_id, role, is_current, is_living, "
                 "start_date) VALUES (?, ?, 'owner', 1, 1, '2020-03-01')", (rid, hid))
    for k, hid in enumerate(c2_houses[::2]):
        amount = (db.execute("SELECT area_100 FROM house WHERE id=?", (hid,)).fetchone()["area_100"] * 200 + 50) // 100
        b = _ins(db, """INSERT INTO bill (house_id, fee_item_id, period, period_start, months,
            amount_receivable, amount_received, status) VALUES (?,?,?,?,?,?,0,'unpaid')""",
            (hid, fw, month, month, 1, amount))
        if k % 2 == 0:      # 一半缴清 → 收缴率约 50%，与阳光花园形成对比
            db.execute("""INSERT INTO payment (bill_id, amount, pay_date, method) VALUES (?,?,?,?)""",
                (b, amount, pay_day(month), "现金"))
            db.execute("UPDATE bill SET amount_received=?, status='paid' WHERE id=?", (amount, b))
