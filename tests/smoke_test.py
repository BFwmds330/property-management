# -*- coding: utf-8 -*-
"""自动化冒烟测试：用 Flask 测试客户端把验收标准逐条过一遍。

运行：python tests/smoke_test.py
（使用临时目录里的独立数据库，不碰 data/ 里的真实数据）
"""
import io
import os
import sqlite3
import sys
import tempfile
import traceback
from datetime import date

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)

# 必须在导入 database / app 之前把数据路径指到临时目录
import config
_TMP = tempfile.mkdtemp(prefix="wuye_test_")
config.DATA_DIR = _TMP
config.BACKUP_DIR = os.path.join(_TMP, "backups")
config.DB_PATH = os.path.join(_TMP, "test.db")

import database
database.DB_PATH = config.DB_PATH

from app import create_app

TODAY = date.today()
CUR_MONTH = TODAY.strftime("%Y-%m")

PASS, FAIL = 0, []


def check(name, cond, detail=""):
    global PASS
    if cond:
        PASS += 1
        print("  ✅ %s" % name)
    else:
        FAIL.append((name, detail))
        print("  ❌ %s  %s" % (name, detail))


def db_rows(sql, args=()):
    con = sqlite3.connect(config.DB_PATH)
    con.row_factory = sqlite3.Row
    try:
        return con.execute(sql, args).fetchall()
    finally:
        con.close()


def main():
    app = create_app()
    app.config["TESTING"] = True
    c = app.test_client()

    # ============ 0. 空库引导 ============
    print("\n== 空库首次使用引导 ==")
    r = c.get("/")
    check("空库时首页跳转到创建小区", r.status_code == 302 and "/community/add" in r.headers.get("Location", ""),
          "%s %s" % (r.status_code, r.headers.get("Location")))
    r = c.get("/community/add")
    check("创建小区页面可打开", r.status_code == 200)

    # ============ 1. 创建两个小区并切换 ============
    print("\n== 小区管理 ==")
    r = c.post("/community/add", data={"name": "阳光花园", "address": "幸福路88号",
                                       "building_count": "2", "parking_info": "地上80个",
                                       "delivery_date": "2021-06-30", "takeover_date": "2021-07-01",
                                       "default_unit_price": "2.20", "remark": ""}, follow_redirects=True)
    check("创建小区1（阳光花园）", r.status_code == 200 and "阳光花园".encode() in r.data)
    r = c.post("/community/add", data={"name": "翡翠湾", "address": "临湖路12号"}, follow_redirects=True)
    check("创建小区2（翡翠湾）", r.status_code == 200 and "翡翠湾".encode() in r.data)
    r = c.get("/community/list")
    check("小区列表显示两个小区", "阳光花园".encode() in r.data and "翡翠湾".encode() in r.data)

    # 重名拦截
    r = c.post("/community/add", data={"name": "阳光花园"}, follow_redirects=False)
    check("小区重名被拦截", r.status_code == 302)
    # 超长输入不崩溃
    r = c.post("/community/add", data={"name": "超" * 300}, follow_redirects=False)
    check("超长小区名不崩溃（友好提示）", r.status_code == 302)

    # ============ 2. 楼栋 / 户型 / 批量生成 ============
    print("\n== 楼栋与房屋（当前小区=翡翠湾，先验证隔离）==")
    c.post("/community/switch/1", data={"next": "/"})
    r = c.post("/building/add", data={"code": "1#", "units": "2", "floors": "5",
                                      "has_elevator": "1", "remark": ""}, follow_redirects=True)
    check("添加楼栋1#", "1#".encode() in r.data)
    r = c.post("/house-type/add", data={"name": "两室两厅一卫", "rooms": "2", "halls": "2",
                                        "baths": "1", "area": "75.5", "orientation": "南北",
                                        "has_balcony": "1", "remark": ""}, follow_redirects=True)
    check("添加户型", "两室两厅一卫".encode() in r.data)
    r = c.post("/house-type/add", data={"name": "三室两厅", "rooms": "3", "halls": "2",
                                        "baths": "2", "area": "105", "orientation": "南"}, follow_redirects=True)
    tid = db_rows("SELECT id FROM house_type WHERE name='三室两厅'")[0]["id"]

    print("\n== 批量生成房屋（2单元×5层×每层2户=20套）==")
    r = c.post("/house/batch", data={"building_id": "1", "units": "2", "floors": "5",
                                     "per_floor": "2", "house_type_id": str(tid), "area": "100"},
               follow_redirects=True)
    check("批量生成成功", "新建 20 套".encode() in r.data)
    n = db_rows("SELECT COUNT(*) AS n FROM house")[0]["n"]
    check("数据库中 20 套房屋", n == 20, "实际 %d" % n)
    r = c.post("/house/batch", data={"building_id": "1", "units": "2", "floors": "5",
                                     "per_floor": "2", "area": "100"}, follow_redirects=True)
    check("重复批量生成全部跳过", "跳过已存在的 20 套".encode() in r.data)
    # 房号编码检查
    row = db_rows("""SELECT b.code, h.unit, h.floor, h.room_no FROM house h
                     JOIN building b ON b.id=h.building_id
                     ORDER BY b.id, h.unit, h.floor, h.room_no LIMIT 1""")[0]
    check("房号自动编号（101 起）", row["room_no"] == 101, str(dict(row)))

    # ============ 3. 小区数据隔离 ============
    print("\n== 小区切换与数据隔离 ==")
    c.post("/community/switch/2", data={"next": "/"})
    r = c.get("/house")
    check("切换到翡翠湾后房屋列表为空", "共 0 套房屋".encode() in r.data)
    r = c.get("/building")
    check("翡翠湾没有楼栋", "共 0 栋楼".encode() in r.data)
    c.post("/community/switch/1", data={"next": "/"})
    r = c.get("/house")
    check("切回阳光花园房屋仍在", "共 20 套房屋".encode() in r.data)

    # ============ 4. 单套新增房屋 + 重复拦截 ============
    print("\n== 单套新增房屋 ==")
    r = c.post("/house/add", data={"building_id": "1", "unit": "1", "floor": "5", "room_no": "503",
                                   "area": "120", "inner_area": "98", "house_type_id": str(tid),
                                   "status": "self", "occupied_date": "2022-01-01",
                                   "owner_mode": "new", "owner_name": "张三", "owner_phone": "13812345678",
                                   "remark": "顶楼"}, follow_redirects=True)
    check("新增房屋并登记业主", "张三".encode() in r.data)
    r = c.post("/house/add", data={"building_id": "1", "unit": "1", "floor": "6", "room_no": "601",
                                   "status": "self", "owner_mode": "none"}, follow_redirects=False)
    check("超出楼栋层数的房屋被拦截", r.status_code == 302)
    r = c.post("/house/add", data={"building_id": "1", "unit": "1", "floor": "5", "room_no": "503",
                                   "status": "self", "owner_mode": "none"}, follow_redirects=False)
    check("重复房号被拦截", r.status_code == 302)

    houses = db_rows("SELECT id, room_no FROM house ORDER BY id")
    hid_main = houses[-1]["id"]         # 新加的 1-5-503（业主=张三）
    hid_payall = houses[3]["id"]
    hid_void = houses[4]["id"]

    # ============ 5. 住户：业主已有，加 2 名家庭成员 + 1 名租户 ============
    print("\n== 住户登记（1 业主 + 2 成员 + 1 租户）==")
    for rel, name, phone in (("配偶", "李四", "13812345679"), ("子女", "张小明", "")):
        r = c.post("/resident/add", data={
            "house_id": str(hid_main), "role": "member", "name": name, "gender": "女",
            "birth_date": "1985-05-01", "id_number": "", "phone": phone,
            "relation": rel, "is_living": "1", "start_date": "2022-01-01"}, follow_redirects=False)
        check("添加家庭成员 %s" % name, r.status_code == 302 and b"resident" not in r.headers.get("Location", "").encode())
    r = c.post("/resident/add", data={
        "house_id": str(hid_main), "role": "tenant", "name": "王五", "phone": "13812345680",
        "start_date": "2026-01-01", "lease_start": "2026-01-01",
        "lease_end": (TODAY.replace(day=1).isoformat() if False else "2027-01-01"),
        "monthly_rent": "2000"}, follow_redirects=True)
    check("添加租户", "王五".encode() in r.data)
    r = c.get("/house/%d" % hid_main)
    check("房屋详情显示 4 名住户", "王五".encode() in r.data and "李四".encode() in r.data
          and "张三".encode() in r.data and "张小明".encode() in r.data)

    # 手机号/身份证校验
    r = c.post("/resident/add", data={"house_id": str(hid_main), "role": "member", "name": "赵六",
                                      "phone": "123", "relation": "其他"}, follow_redirects=False)
    check("手机号位数不对被拦截", r.status_code == 302)
    bad_id = "11010119900101123X"     # 校验位错误
    r = c.post("/resident/add", data={"house_id": str(hid_main), "role": "member", "name": "赵六",
                                      "id_number": bad_id}, follow_redirects=False)
    check("身份证校验位错误被拦截", r.status_code == 302)
    # 第二个业主拦截
    r = c.post("/resident/add", data={"house_id": str(hid_main), "role": "owner", "name": "测试"},
               follow_redirects=False)
    check("重复登记业主被拦截", r.status_code == 302)
    # 搬出一名成员 → 历史时间线
    link = db_rows("SELECT id FROM resident_house WHERE role='member' LIMIT 1")[0]["id"]
    r = c.post("/resident/%d/move-out" % link, follow_redirects=True)
    check("成员搬出转历史", "历史住户".encode() in r.data)
    r = c.get("/house/%d" % hid_main)
    check("历史时间线显示已搬出", "已搬出".encode() in r.data)

    # ============ 6. 收费项目与账单生成 ============
    print("\n== 收费项目与生成账单 ==")
    r = c.post("/fee/items/add", data={"name": "物业费", "pricing_mode": "area",
                                       "unit_price": "2.20", "cycle": "month", "remark": ""},
               follow_redirects=True)
    check("添加收费项目 物业费(2.2元/㎡/月)", "物业费".encode() in r.data)
    r = c.post("/fee/items/add", data={"name": "公摊水电费", "pricing_mode": "fixed",
                                       "unit_price": "15.00", "cycle": "month"}, follow_redirects=True)
    check("添加收费项目 公摊水电费(15元/户/月)", "公摊水电费".encode() in r.data)
    f1 = db_rows("SELECT id FROM fee_item WHERE name='物业费'")[0]["id"]
    f2 = db_rows("SELECT id FROM fee_item WHERE name='公摊水电费'")[0]["id"]

    r = c.post("/fee/generate", data={"fee_item_id": str(f1), "period": CUR_MONTH,
                                      "building_id": "", "status_scope": "all"})
    check("生成预览页可打开", r.status_code == 200 and "第 2 步".encode() in r.data)
    r = c.post("/fee/generate/confirm", data={"fee_item_id": str(f1), "period": CUR_MONTH,
                                              "building_id": "0", "status_scope": "all"},
               follow_redirects=True)
    check("生成物业费账单 21 笔", "共 21 笔".encode() in r.data)
    r = c.post("/fee/generate/confirm", data={"fee_item_id": str(f2), "period": CUR_MONTH,
                                              "building_id": "0", "status_scope": "all"},
               follow_redirects=True)
    check("生成公摊账单 21 笔", "共 21 笔".encode() in r.data)
    r = c.post("/fee/generate/confirm", data={"fee_item_id": str(f1), "period": CUR_MONTH,
                                              "building_id": "0", "status_scope": "all"},
               follow_redirects=False)
    check("同账期重复生成被提示无需生成", r.status_code == 302)
    # 金额核算：100㎡ × 2.2 = 220 元；公摊 15 元
    amt = db_rows("SELECT amount_receivable FROM bill WHERE fee_item_id=?", (f1,))[0]["amount_receivable"]
    check("物业费金额=220.00元（整数分存储）", amt == 22000, str(amt))
    amt2 = db_rows("SELECT amount_receivable FROM bill WHERE fee_item_id=?", (f2,))[0]["amount_receivable"]
    check("公摊金额=15.00元", amt2 == 1500, str(amt2))

    # ============ 7. 部分缴费 + 全额缴费 + 状态流转 ============
    print("\n== 缴费与状态流转 ==")
    bill = db_rows("""SELECT b.id, b.amount_receivable FROM bill b
                      JOIN house h ON h.id=b.house_id
                      WHERE b.fee_item_id=? AND h.room_no=101 ORDER BY b.id LIMIT 1""", (f1,))[0]
    r = c.post("/fee/bill/%d/pay" % bill["id"], data={"amount": "100.00", "pay_date": TODAY.isoformat(),
                                                      "method": "现金", "receipt_no": "SK001"},
               follow_redirects=True)
    check("部分缴费 100 元", r.status_code == 200)
    st = db_rows("SELECT status, amount_received FROM bill WHERE id=?", (bill["id"],))[0]
    check("状态变为部分缴纳", st["status"] == "partial" and st["amount_received"] == 10000,
          "%s %s" % (st["status"], st["amount_received"]))
    r = c.post("/fee/bill/%d/pay" % bill["id"], data={"amount": "999", "pay_date": TODAY.isoformat(),
                                                      "method": "现金"}, follow_redirects=False)
    check("超额缴费被拦截", r.status_code == 302)
    r = c.post("/fee/bill/%d/pay" % bill["id"], data={"amount": "120.00", "pay_date": TODAY.isoformat(),
                                                      "method": "转账"}, follow_redirects=True)
    st = db_rows("SELECT status, amount_received FROM bill WHERE id=?", (bill["id"],))[0]
    check("补齐后状态变为已缴清", st["status"] == "paid" and st["amount_received"] == 22000)

    # 一键缴清（多张账单合并收款）
    r = c.get("/fee/house/%d/payall" % hid_payall)
    check("一键缴清页可打开", r.status_code == 200)
    r = c.post("/fee/house/%d/payall" % hid_payall,
               data={"bill_ids": [str(b["id"]) for b in db_rows(
                   "SELECT id FROM bill WHERE house_id=? AND status IN ('unpaid','partial')", (hid_payall,))],
                   "total_amount": "235.00", "pay_date": TODAY.isoformat(), "method": "扫码"},
               follow_redirects=True)
    st = db_rows("SELECT COUNT(*) AS n FROM bill WHERE house_id=? AND status='paid'", (hid_payall,))[0]["n"]
    check("合并收款后该房 2 笔账单全部缴清", st == 2, "已缴清 %d 笔" % st)

    # 调整金额（留痕）
    bill_v = db_rows("SELECT id, amount_receivable FROM bill WHERE house_id=? AND fee_item_id=?",
                     (hid_void, f1))[0]
    r = c.post("/fee/bill/%d/adjust" % bill_v["id"], data={"new_amount": "200.00", "reason": "空置房减免"},
               follow_redirects=True)
    r = c.get("/fee/bill/%d" % bill_v["id"])
    check("金额调整留痕（原因可查）", "空置房减免".encode() in r.data)
    # 调整必须填原因
    r = c.post("/fee/bill/%d/adjust" % bill_v["id"], data={"new_amount": "100.00", "reason": ""},
               follow_redirects=False)
    check("调整不填原因被拦截", r.status_code == 302)
    # 作废
    r = c.post("/fee/bill/%d/void" % bill_v["id"], follow_redirects=True)
    st = db_rows("SELECT status FROM bill WHERE id=?", (bill_v["id"],))[0]["status"]
    check("账单作废成功", st == "void")

    # ============ 8. 欠费清单与报表数字核算 ============
    print("\n== 欠费清单与报表核算 ==")
    # 20 套×22000（100㎡×2.2）+ 新增 1 套 26400（120㎡×2.2）+ 21 套公摊×1500
    # 减：调整 -2000；作废账单 20000（作废后不计应收）
    expected_recv = 20 * 22000 + 26400 + 21 * 1500 - 2000 - 20000
    expected_got = 22000 + 23500                       # 101室全额 + payall 房
    void_amt = db_rows("SELECT amount_receivable FROM bill WHERE status='void'")[0]["amount_receivable"]
    check("作废账单的调整已生效（22000→20000）", void_amt == 20000, str(void_amt))
    row = db_rows("""
        SELECT COALESCE(SUM(amount_receivable),0) recv, COALESCE(SUM(amount_received),0) got
        FROM bill b JOIN house h ON h.id=b.house_id
        WHERE h.community_id=1 AND b.status!='void'""")[0]
    check("欠费报表：应收合计与手工核算一致", row["recv"] == expected_recv,
          "库 %d / 算 %d" % (row["recv"], expected_recv))
    check("报表：实收合计与手工核算一致", row["got"] == expected_got,
          "库 %d / 算 %d" % (row["got"], expected_got))
    rate = round(row["got"] * 100 / row["recv"])
    r = c.get("/report?mode=month&month=%s&scope=current" % CUR_MONTH)
    check("报表页显示收缴率 %d%%" % rate, ("%d%%" % rate).encode() in r.data)
    r = c.get("/fee/overdue")
    check("欠费管理页可打开且显示欠费", r.status_code == 200 and "欠费总额".encode() in r.data)
    hid_sms = db_rows("""SELECT DISTINCT house_id FROM bill WHERE status='unpaid' LIMIT 1""")[0]["house_id"]
    r = c.get("/fee/overdue/sms/%d" % hid_sms)
    check("催缴短信生成", r.status_code == 200 and b"\\u7269\\u4e1a" in r.data.replace(b" ", b""))

    # ============ 9. 全局搜索 / 导出 ============
    print("\n== 搜索与导出 ==")
    r = c.get("/resident?keyword=张三")
    check("全局搜索住户（按姓名）", "张三".encode() in r.data)
    r = c.get("/resident?keyword=13812345680")
    check("全局搜索住户（按手机号）", "王五".encode() in r.data)
    r = c.get("/resident/export")
    check("通讯录 CSV 带 BOM（Excel 不乱码）", r.status_code == 200 and r.data.startswith(b"\xef\xbb\xbf"))
    r = c.get("/fee/overdue/export")
    check("催缴名单 CSV 可导出", r.status_code == 200 and r.data.startswith(b"\xef\xbb\xbf"))
    r = c.get("/fee/payments/export")
    check("缴费流水 CSV 可导出", r.status_code == 200)

    # ============ 10. 产权过户 ============
    print("\n== 产权过户 ==")
    r = c.post("/house/%d/transfer" % hid_main, data={"transfer_date": TODAY.isoformat(),
                                                      "owner_mode": "new", "owner_name": "新业主",
                                                      "owner_phone": "13999999999"}, follow_redirects=True)
    check("过户成功提示", "产权过户完成".encode() in r.data)
    r = c.get("/house/%d" % hid_main)
    check("旧业主进历史、新业主生效", "新业主".encode() in r.data and "已搬出".encode() in r.data)

    # ============ 11. 报修 ============
    print("\n== 报修登记 ==")
    r = c.post("/repair/add", data={"house_id": str(hid_main), "content": "厨房漏水"}, follow_redirects=True)
    check("新增报修", "厨房漏水".encode() in r.data)
    rid = db_rows("SELECT id FROM repair LIMIT 1")[0]["id"]
    c.post("/repair/%d/status" % rid, data={"action": "start"})
    c.post("/repair/%d/status" % rid, data={"action": "finish"})
    st = db_rows("SELECT status FROM repair WHERE id=?", (rid,))[0]["status"]
    check("报修状态流转到已完成", st == "done")

    # ============ 12. 备份与恢复 ============
    print("\n== 备份与恢复 ==")
    r = c.get("/settings/backup")
    check("备份数据可下载", r.status_code == 200 and ".db" in r.headers.get("Content-Disposition", ""))
    backup_bytes = r.data
    # 做一些“之后的变化”，再用备份恢复回去
    c.post("/community/add", data={"name": "恢复测试专用小区"})
    n_new = db_rows("SELECT COUNT(*) AS n FROM community WHERE name='恢复测试专用小区'")[0]["n"]
    check("备份后新增了一个小区", n_new == 1)
    r = c.post("/settings/restore", data={"file": (io.BytesIO(backup_bytes), "物业数据备份.db")},
               content_type="multipart/form-data", follow_redirects=True)
    check("恢复数据成功", "数据恢复成功".encode() in r.data)
    n_new = db_rows("SELECT COUNT(*) AS n FROM community WHERE name='恢复测试专用小区'")[0]["n"]
    n_sun = db_rows("SELECT COUNT(*) AS n FROM community WHERE name='阳光花园'")[0]["n"]
    check("恢复后新小区消失、原小区回来", n_new == 0 and n_sun == 1)
    # 换目录恢复：备份文件放到新目录，用 fresh 连接打开并校验数据完整
    tmp2 = tempfile.mkdtemp(prefix="wuye_restore_")
    with open(os.path.join(tmp2, "backup.db"), "wb") as f:
        f.write(backup_bytes)
    con = sqlite3.connect(os.path.join(tmp2, "backup.db"))
    n = con.execute("SELECT COUNT(*) FROM house").fetchone()[0]
    con.close()
    check("备份文件在任意目录可独立打开（换电脑迁移）", n == 21, "房屋数 %d" % n)
    # 恢复非 db 文件被拦截
    r = c.post("/settings/restore", data={"file": (io.BytesIO(b"hello"), "x.db")},
               content_type="multipart/form-data", follow_redirects=False)
    check("伪备份文件被识别拦截", r.status_code == 302)

    # ============ 13. 演示数据载入/清空 ============
    print("\n== 演示数据 ==")
    r = c.post("/settings/demo/load", follow_redirects=True)
    check("演示数据载入", "演示数据已载入".encode() in r.data)
    n = db_rows("SELECT COUNT(*) AS n FROM community WHERE is_demo=1")[0]["n"]
    check("载入了 2 个演示小区", n == 2, str(n))
    n_res = db_rows("SELECT COUNT(*) AS n FROM resident")[0]["n"]
    check("演示住户超过 20 人", n_res >= 20, str(n_res))
    r = c.post("/settings/demo/clear", follow_redirects=True)
    check("一键清空演示数据", "演示数据已清空".encode() in r.data)
    n = db_rows("SELECT COUNT(*) AS n FROM community WHERE is_demo=1")[0]["n"]
    check("清空后演示小区为 0", n == 0)
    n_sun = db_rows("SELECT COUNT(*) AS n FROM community WHERE name='阳光花园'")[0]["n"]
    check("清空演示数据不影响真实小区", n_sun == 1)

    # ============ 14. 乱输入轰炸（都不能 500）============
    print("\n== 乱输入轰炸 ==")
    fuzz_cases = [
        ("POST", "/community/add", {"name": "x", "building_count": "abc"}),
        ("POST", "/community/add", {"name": "-" * 5, "default_unit_price": "-3"}),
        ("GET", "/house?building_id=abc&unit=xyz", None),
        ("GET", "/fee/bills?fee_item_id=abc&building_id=xyz", None),
        ("GET", "/fee/payments?fee_item_id=abc&date_from=not-a-date", None),
        ("POST", "/fee/generate/confirm", {"fee_item_id": "abc", "period": "garbage"}),
        ("POST", "/fee/bill/99999/pay", {"amount": "abc", "pay_date": "2026-13-40"}),
        ("POST", "/fee/bill/%d/pay" % bill["id"], {"amount": "-5.00", "pay_date": "2026-01-01"}),
        ("POST", "/fee/bill/%d/pay" % bill["id"], {"amount": "1.999", "pay_date": "2026-01-01"}),
        ("GET", "/house/99999", None),
        ("GET", "/fee/bill/99999", None),
        ("POST", "/resident/add", {"house_id": "99999", "role": "member", "name": "测试"}),
        ("POST", "/resident/add", {"house_id": "abc", "role": "member", "name": "测试"}),
        ("POST", "/house/batch", {"building_id": "99999", "units": "2", "floors": "5", "per_floor": "2"}),
        ("POST", "/house/batch", {"building_id": "1", "units": "999", "floors": "-5", "per_floor": "2"}),
        ("POST", "/community/switch/99999", {}),
        ("POST", "/fee/house/%d/payall" % hid_main, {"bill_ids": ["99999", "abc"], "total_amount": "1"}),
        ("POST", "/fee/items/add", {"name": "x" * 500, "unit_price": "abc"}),
        ("POST", "/fee/items/add", {"name": "物业费", "unit_price": "0.00"}),
        ("GET", "/resident?keyword=<script>alert(1)</script>", None),
        ("POST", "/settings/demo/load", {}),      # 已清空后可再次载入，不算错；这里主要验证不崩
        ("POST", "/repair/add", {"content": ""}),
        ("GET", "/不存在的页面", None),
    ]
    ok = True
    for method, url, data in fuzz_cases:
        try:
            r = c.open(url, method=method, data=data, follow_redirects=True)
            if r.status_code >= 500:
                ok = False
                print("    💥 500: %s %s" % (method, url))
        except Exception as e:
            ok = False
            print("    💥 异常: %s %s -> %s" % (method, url, e))
    check("全部乱输入都不崩溃（无 500）", ok)
    r = c.get("/")
    check("轰炸后首页仍正常", r.status_code == 200)

    # ============ 15. 重启数据不丢失 ============
    print("\n== 重启程序数据不丢失 ==")
    app2 = create_app()      # 模拟关掉窗口重新启动
    c2 = app2.test_client()
    r = c2.get("/house")
    check("重启后房屋数据仍在", "共 21 套房屋".encode() in r.data)
    r = c2.get("/resident?keyword=王五")
    check("重启后住户数据仍在", "王五".encode() in r.data)

    # ============ 15.5 端口占用自动顺延（Windows 兼容）============
    print("\n== 端口占用自动顺延 ==")
    import socket as _socket
    from app import _pick_port
    blocker = _socket.socket()
    blocker.setsockopt(_socket.SOL_SOCKET, _socket.SO_REUSEADDR, 1)   # 模拟另一个物业管家实例
    blocker.bind(("0.0.0.0", 5998))
    blocker.listen(1)
    picked = _pick_port(5998)
    check("端口被占用时自动换下一个", picked is not None and picked != 5998, str(picked))
    # 把 6019~6039 全部占住，_pick_port 应返回 None
    blockers = []
    for p in range(6019, 6040):
        b = _socket.socket()
        b.setsockopt(_socket.SOL_SOCKET, _socket.SO_REUSEADDR, 1)
        try:
            b.bind(("0.0.0.0", p))
            b.listen(1)
            blockers.append(b)
        except OSError:
            b.close()      # 已被系统占用也无所谓，效果相同
    check("全部端口被占时返回 None", _pick_port(6019) is None)
    for b in blockers:
        b.close()
    blocker.close()

    # ============ 16. 名册导入与车辆管理 ============
    print("\n== 名册导入与车辆管理 ==")
    import json as _json
    from services import house_import as _hi
    id17 = "11010119900307777"
    good_id = id17 + __import__("sys").modules["utils"].idcard_check_digit(id17)
    csv_lines = [
        "栋编号,单元,房号,建筑面积,车位号,业主姓名,业主电话,业主证件号,业主性别,业主工作单位,家庭成员,租户,车辆,备注",
        "9,1,1,89.50,12东,钱测试,13800009001,,男,测试单位,孙测试|13800009002||配偶,周租户|13800009003,皖A99999|地下9号,导入测试",
        "9,1,2,75.20,,吴测试,13800009004,{},女,,,".format(good_id),
        "9,2,1,99.00,,錯测试,x,y,,",   # 坏行：电话/证件号非法 → 应被拦截
    ]
    csv_bytes = ("\n".join(csv_lines)).encode("gbk")     # 顺带验证 GBK 编码兼容
    r = c2.post("/house/import/preview", data={"file": (io.BytesIO(csv_bytes), "名册.csv")},
                content_type="multipart/form-data")
    html = r.data.decode("utf-8")
    check("名册导入预览可打开", r.status_code == 200 and "预览确认" in html)
    check("坏行被拦截并提示", "必须全部修正" in html and "1 个问题" in html)
    # 去掉坏行后确认导入
    rows = _hi.read_import_csv(("\n".join(csv_lines[:-1])).encode("utf-8-sig"))
    payload = _json.dumps(rows, ensure_ascii=False)
    r = c2.post("/house/import/confirm", data={"payload": payload}, follow_redirects=True)
    check("导入成功提示", "导入完成" in r.data.decode("utf-8"))
    n_house = db_rows("SELECT COUNT(*) AS n FROM house WHERE community_id=1 AND building_id IN "
                      "(SELECT id FROM building WHERE code='9#')")[0]["n"]
    check("导入新建 2 套房屋", n_house == 2, str(n_house))
    r = c2.get("/resident?keyword=钱测试")
    check("导入的业主可搜索", "钱测试".encode() in r.data)
    hid_imp = db_rows("SELECT h.id FROM house h JOIN building b ON b.id=h.building_id "
                      "WHERE b.code='9#' AND h.room_no=1")[0]["id"]
    r = c2.get("/house/%d" % hid_imp)
    check("房屋详情显示车位号", "12东".encode() in r.data)
    check("房屋详情显示车辆", "皖A99999".encode() in r.data)
    check("家庭成员已导入", "孙测试".encode() in r.data and "配偶".encode() in r.data)
    check("租户已导入且状态变为出租", "周租户".encode() in r.data)
    st = db_rows("SELECT status FROM house WHERE id=?", (hid_imp,))[0]["status"]
    check("有租户的房屋状态=出租", st == "rent", st)
    # 重复导入：不应重复登记业主/车辆
    r = c2.post("/house/import/confirm", data={"payload": payload}, follow_redirects=True)
    n_owner = db_rows("""SELECT COUNT(*) AS n FROM resident_house rh WHERE rh.house_id=? AND rh.role='owner'
                         AND rh.is_current=1""", (hid_imp,))[0]["n"]
    n_car = db_rows("SELECT COUNT(*) AS n FROM vehicle WHERE house_id=?", (hid_imp,))[0]["n"]
    check("重复导入不产生重复业主/车辆", n_owner == 1 and n_car == 1,
          "业主 %d 车 %d" % (n_owner, n_car))
    # 车辆登记/删除
    r = c2.post("/house/%d/vehicle/add" % hid_imp, data={"plate": "皖B88888", "vehicle_remark": "测试"},
                follow_redirects=True)
    check("手工登记车辆", "皖B88888".encode() in r.data)
    vid = db_rows("SELECT id FROM vehicle WHERE plate='皖B88888'")[0]["id"]
    r = c2.post("/house/vehicle/%d/delete" % vid, follow_redirects=True)
    check("删除车辆登记", "皖B88888".encode() not in r.data)

    # 停车费账单关联车辆（车辆卡片显示缴纳情况）
    r = c2.post("/fee/items/add", data={"name": "停车费", "pricing_mode": "fixed",
                                        "unit_price": "20", "cycle": "month"}, follow_redirects=True)
    fid_park = db_rows("SELECT id FROM fee_item WHERE name='停车费'")[0]["id"]
    vid = db_rows("SELECT id FROM vehicle WHERE house_id=? AND plate='皖A99999'", (hid_imp,))[0]["id"]
    con_t = sqlite3.connect(config.DB_PATH)
    con_t.execute("""INSERT INTO bill (house_id, fee_item_id, vehicle_id, period, period_start,
        months, amount_receivable, amount_received, status)
        VALUES (?,?,?,?,?,?,?,?,?)""",
        (hid_imp, fid_park, vid, "2026.1-2026.12[皖A99999]", "2026-01", 12, 24000, 24000, "paid"))
    con_t.commit(); con_t.close()
    r = c2.get("/house/%d" % hid_imp)
    html2 = r.data.decode("utf-8")
    check("车辆卡片显示停车费缴纳情况", "2026.1-2026.12" in html2 and "缴至" in html2)
    check("显示缴至月份", "2026-12" in html2)

    # ============ 16. 所有页面可访问 ============
    print("\n== 所有页面可访问 ==")
    pages = ["/", "/community/list", "/community/add", "/house", "/house/add", "/house/batch",
             "/house/import", "/building", "/house-type", "/resident", "/resident/add?house_id=%d&role=member" % hid_main,
             "/fee/items", "/fee/bills", "/fee/generate", "/fee/overdue", "/fee/payments",
             "/report", "/repair", "/settings", "/house/%d" % hid_main,
             "/house/%d/edit" % hid_main, "/fee/house/%d/payall" % hid_main,
             "/fee/bill/%d" % bill["id"], "/resident/export"]
    ok = True
    for p in pages:
        r = c2.get(p)
        if r.status_code != 200:
            ok = False
            print("    💥 %s -> %d" % (p, r.status_code))
    check("全部 %d 个页面返回 200" % len(pages), ok)

    print("\n" + "=" * 50)
    print("通过 %d 项检查，失败 %d 项" % (PASS, len(FAIL)))
    for name, detail in FAIL:
        print("  ❌ %s  %s" % (name, detail))
    return 0 if not FAIL else 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        traceback.print_exc()
        sys.exit(2)
