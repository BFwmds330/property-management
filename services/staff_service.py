# -*- coding: utf-8 -*-
"""物业企业人员管理：员工档案、月度工资、社保缴纳、人力成本报表。

约定：
- 金额一律以「分」为整数存储（复用 utils.parse_amount / fmt_money）；
- 应发 = 基本 + 津贴 + 加班 + 补贴；实发 = 应发 − 社保个人代扣 − 个税 − 其他扣款，
  两者均为计算值不落库；
- 社保个人代扣默认自动带出当月社保记录的个人三项合计，可覆盖但需填写原因并留痕；
- 同一员工同一月份的工资 / 社保记录唯一（保存即覆盖更新，并计数留痕）。
"""
from database import log_op, query_all, query_one, scalar
from utils import (STAFF_DEPARTMENTS, STAFF_STATUS, UserError, clean_str,
                   fmt_money, parse_amount, parse_date, parse_idcard,
                   parse_int, parse_phone)


def get_staff(db, sid):
    row = query_one(db, "SELECT * FROM staff WHERE id=?", (sid,))
    if not row:
        raise UserError("没有找到这名员工，可能已被删除，请刷新页面")
    return row


def staff_community_name(db, community_id):
    if not community_id:
        return ""
    row = query_one(db, "SELECT name FROM community WHERE id=?", (community_id,))
    return row["name"] if row else ""


def _staff_fields(db, form):
    name = clean_str(form.get("name"), "姓名", 50, required=True)
    gender = (form.get("gender") or "").strip()
    if gender not in ("", "男", "女"):
        raise UserError("性别只能是 男 / 女")
    birth = parse_date(form.get("birth_date"), "出生日期")
    id_number = parse_idcard(form.get("id_number"), "身份证号")
    phone = parse_phone(form.get("phone"), "手机号")
    department = (form.get("department") or "").strip()
    if department and department not in STAFF_DEPARTMENTS:
        department = "其他"
    position = clean_str(form.get("position"), "职位", 50)
    community_id = parse_int(form.get("community_id"), "工作小区", 1, 10**9,
                             required=False, default=None) or None
    if community_id and not query_one(db, "SELECT id FROM community WHERE id=?", (community_id,)):
        raise UserError("所选工作小区不存在，请重新选择")
    hire_date = parse_date(form.get("hire_date"), "入职日期")
    leave_date = parse_date(form.get("leave_date"), "离职日期")
    status = (form.get("status") or "active").strip()
    if status not in STAFF_STATUS:
        raise UserError("在岗状态不正确")
    if hire_date and leave_date and leave_date < hire_date:
        raise UserError("离职日期不能早于入职日期")
    contract_end = parse_date(form.get("contract_end"), "合同到期日")
    cert_name = clean_str(form.get("cert_name"), "证照名称", 50)
    cert_end = parse_date(form.get("cert_end"), "证照到期日")
    emergency_name = clean_str(form.get("emergency_name"), "紧急联系人姓名", 50)
    emergency_phone = parse_phone(form.get("emergency_phone"), "紧急联系人电话")
    address = clean_str(form.get("address"), "现居住址", 200)
    remark = clean_str(form.get("remark"), "备注", 300)
    return dict(name=name, gender=gender, birth_date=birth, id_number=id_number,
                phone=phone, department=department, position=position,
                community_id=community_id, hire_date=hire_date, leave_date=leave_date,
                status=status, contract_end=contract_end, cert_name=cert_name,
                cert_end=cert_end, emergency_name=emergency_name,
                emergency_phone=emergency_phone, address=address, remark=remark)


def add_staff(db, form):
    f = _staff_fields(db, form)
    if f["id_number"]:
        dup = query_one(db, "SELECT id, name FROM staff WHERE id_number=? AND id_number!=''",
                        (f["id_number"],))
        if dup:
            raise UserError("已存在同身份证号的员工「%s」（#%d），请勿重复建档"
                            % (dup["name"], dup["id"]))
    cur = db.execute(
        """INSERT INTO staff (name, gender, birth_date, id_number, phone, department,
           position, community_id, hire_date, leave_date, status, contract_end,
           cert_name, cert_end, emergency_name, emergency_phone, address, remark)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (f["name"], f["gender"], f["birth_date"], f["id_number"], f["phone"],
         f["department"], f["position"], f["community_id"], f["hire_date"],
         f["leave_date"], f["status"], f["contract_end"], f["cert_name"],
         f["cert_end"], f["emergency_name"], f["emergency_phone"], f["address"], f["remark"]))
    log_op(db, "人员", "新增员工", "新增员工「%s」（%s）" % (f["name"], f["position"] or f["department"]))
    return cur.lastrowid


def update_staff(db, sid, form):
    get_staff(db, sid)
    f = _staff_fields(db, form)
    if f["id_number"]:
        dup = query_one(db, "SELECT id, name FROM staff WHERE id_number=? AND id_number!='' AND id!=?",
                        (f["id_number"], sid))
        if dup:
            raise UserError("已存在同身份证号的员工「%s」（#%d），请勿重复建档"
                            % (dup["name"], dup["id"]))
    db.execute(
        """UPDATE staff SET name=?, gender=?, birth_date=?, id_number=?, phone=?, department=?,
           position=?, community_id=?, hire_date=?, leave_date=?, status=?, contract_end=?,
           cert_name=?, cert_end=?, emergency_name=?, emergency_phone=?, address=?, remark=?
           WHERE id=?""",
        (f["name"], f["gender"], f["birth_date"], f["id_number"], f["phone"],
         f["department"], f["position"], f["community_id"], f["hire_date"],
         f["leave_date"], f["status"], f["contract_end"], f["cert_name"],
         f["cert_end"], f["emergency_name"], f["emergency_phone"], f["address"], f["remark"], sid))
    log_op(db, "人员", "修改员工", "修改员工「%s」信息" % f["name"])


def delete_impact(db, sid):
    return {"salary": scalar(db, "SELECT COUNT(*) FROM staff_salary WHERE staff_id=?", (sid,)),
            "social": scalar(db, "SELECT COUNT(*) FROM staff_social WHERE staff_id=?", (sid,))}


def delete_staff(db, sid):
    get_staff(db, sid)
    impact = delete_impact(db, sid)
    if impact["salary"] or impact["social"]:
        raise UserError("该员工名下有 %d 条工资记录、%d 条社保记录，不能直接删除；"
                        "请把状态改为「离职」保留历史" % (impact["salary"], impact["social"]))
    row = get_staff(db, sid)
    db.execute("DELETE FROM staff WHERE id=?", (sid,))
    log_op(db, "人员", "删除员工", "删除员工「%s」（无任何工资/社保记录）" % row["name"])


def list_staff(db, keyword="", department="", status="", community_id=0, show_left=False):
    sql = """SELECT s.*, c.name AS community_name FROM staff s
             LEFT JOIN community c ON c.id = s.community_id WHERE 1=1"""
    args = []
    kw = keyword.strip()
    if kw:
        sql += " AND (s.name LIKE ? OR s.phone LIKE ? OR s.position LIKE ?)"
        like = f"%{kw}%"
        args += [like, like, like]
    if department:
        sql += " AND s.department = ?"
        args.append(department)
    if status:
        sql += " AND s.status = ?"
        args.append(status)
    if community_id:
        sql += " AND s.community_id = ?"
        args.append(community_id)
    if not show_left:
        sql += " AND s.status != 'left'"
    sql += " ORDER BY CASE s.status WHEN 'left' THEN 1 ELSE 0 END, s.id"
    return [dict(r) for r in query_all(db, sql, args)]


def social_personal_total(social_row):
    if not social_row:
        return 0
    return (social_row["personal_pension"] + social_row["personal_medical"]
            + social_row["personal_unemployment"])


def social_company_total(social_row):
    if not social_row:
        return 0
    return (social_row["co_pension"] + social_row["co_medical"] + social_row["co_unemployment"]
            + social_row["co_injury"] + social_row["co_maternity"] + social_row["co_fund"])


def salary_batch_rows(db, year, month):
    """批量录入页的数据行：全部未离职员工 + 已有工资记录 + 当月社保个人合计。"""
    staff_list = [dict(r) for r in query_all(
        db, "SELECT * FROM staff WHERE status != 'left' ORDER BY department, id")]
    existing = {r["staff_id"]: r for r in query_all(
        db, "SELECT * FROM staff_salary WHERE year=? AND month=?", (year, month))}
    socials = {r["staff_id"]: r for r in query_all(
        db, "SELECT * FROM staff_social WHERE year=? AND month=?", (year, month))}
    rows = []
    for s in staff_list:
        rec = existing.get(s["id"])
        social = socials.get(s["id"])
        rows.append({
            "staff": s,
            "record": rec,
            "social_auto": social_personal_total(social),
            "has_social": social is not None,
            "checked": rec is not None or s["status"] in ("active", "probation"),
        })
    return rows


def save_salary_batch(db, year, month, form):
    year = parse_int(year, "年份", 2000, 2100)
    month = parse_int(month, "月份", 1, 12)
    staff_ids = [int(x) for x in form.getlist("staff_ids")]
    if not staff_ids:
        raise UserError("请至少勾选一名员工再保存")
    created = updated = 0
    for sid in staff_ids:
        s = get_staff(db, sid)
        p = lambda f: form.get(f"{f}_{sid}", "")
        base = parse_amount(p("base"), f"{s['name']} 基本工资", required=False)
        allowance = parse_amount(p("allowance"), f"{s['name']} 岗位津贴", required=False)
        overtime = parse_amount(p("overtime"), f"{s['name']} 加班费", required=False)
        subsidy = parse_amount(p("subsidy"), f"{s['name']} 其他补贴", required=False)
        social_deduct = parse_amount(p("social_deduct"), f"{s['name']} 社保个人代扣", required=False)
        tax = parse_amount(p("tax"), f"{s['name']} 个税", required=False)
        other_deduct = parse_amount(p("other_deduct"), f"{s['name']} 其他扣款", required=False)
        pay_date = parse_date(p("pay_date"), f"{s['name']} 发放日期")
        method = (p("method") or "转账").strip()
        if method not in ("现金", "转账", "扫码"):
            raise UserError("%s 的发放方式不正确" % s["name"])
        remark = clean_str(p("remark"), f"{s['name']} 备注", 200)
        social_auto = parse_amount(p("social_auto"), f"{s['name']} 社保自动值", required=False)
        override_reason = clean_str(p("override_reason"), f"{s['name']} 覆盖原因", 200)
        if social_deduct != social_auto and not override_reason:
            raise UserError("%s 的社保个人代扣与社保记录不一致（自动 %s 元 / 填写 %s 元），"
                            "请填写覆盖原因，或改回自动值"
                            % (s["name"], fmt_money(social_auto), fmt_money(social_deduct)))
        rec = query_one(db, "SELECT id FROM staff_salary WHERE staff_id=? AND year=? AND month=?",
                        (sid, year, month))
        if rec:
            db.execute(
                """UPDATE staff_salary SET base=?, allowance=?, overtime=?, subsidy=?,
                   social_deduct=?, social_auto=?, override_reason=?, tax=?, other_deduct=?,
                   pay_date=?, method=?, remark=? WHERE id=?""",
                (base, allowance, overtime, subsidy, social_deduct, social_auto,
                 override_reason, tax, other_deduct, pay_date, method, remark, rec["id"]))
            updated += 1
        else:
            db.execute(
                """INSERT INTO staff_salary (staff_id, year, month, base, allowance, overtime,
                   subsidy, social_deduct, social_auto, override_reason, tax, other_deduct,
                   pay_date, method, remark) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (sid, year, month, base, allowance, overtime, subsidy, social_deduct,
                 social_auto, override_reason, tax, other_deduct, pay_date, method, remark))
            created += 1
    log_op(db, "人员", "录入工资", "%d 年 %d 月工资保存：新建 %d 条、更新 %d 条" % (year, month, created, updated))
    return created, updated


def delete_salary(db, salary_id):
    r = query_one(db, "SELECT * FROM staff_salary WHERE id=?", (salary_id,))
    if not r:
        raise UserError("没有找到这条工资记录，可能已被删除，请刷新页面")
    staff = get_staff(db, r["staff_id"])
    db.execute("DELETE FROM staff_salary WHERE id=?", (salary_id,))
    log_op(db, "人员", "删除工资记录", "删除 %s %d 年 %d 月工资记录" % (staff["name"], r["year"], r["month"]))


def social_batch_rows(db, year, month):
    staff_list = [dict(r) for r in query_all(
        db, "SELECT * FROM staff WHERE status != 'left' ORDER BY department, id")]
    existing = {r["staff_id"]: r for r in query_all(
        db, "SELECT * FROM staff_social WHERE year=? AND month=?", (year, month))}
    rows = []
    for s in staff_list:
        rows.append({"staff": s, "record": existing.get(s["id"]),
                     "checked": existing.get(s["id"]) is not None})
    return rows


SOCIAL_CO_FIELDS = ["co_pension", "co_medical", "co_unemployment", "co_injury",
                    "co_maternity", "co_fund"]
SOCIAL_PERSONAL_FIELDS = ["personal_pension", "personal_medical",
                          "personal_unemployment", "personal_fund"]


def save_social_batch(db, year, month, form):
    year = parse_int(year, "年份", 2000, 2100)
    month = parse_int(month, "月份", 1, 12)
    staff_ids = [int(x) for x in form.getlist("staff_ids")]
    if not staff_ids:
        raise UserError("请至少勾选一名员工再保存")
    created = updated = 0
    for sid in staff_ids:
        s = get_staff(db, sid)
        vals = {"base": parse_amount(form.get(f"base_{sid}"), f"{s['name']} 缴纳基数", required=False)}
        for f in SOCIAL_CO_FIELDS + SOCIAL_PERSONAL_FIELDS:
            vals[f] = parse_amount(form.get(f"{f}_{sid}"), f"{s['name']} 社保项 {f}", required=False)
        is_backpay = 1 if form.get(f"is_backpay_{sid}") else 0
        remark = clean_str(form.get(f"remark_{sid}"), f"{s['name']} 社保备注", 200)
        rec = query_one(db, "SELECT id, is_backpay, remark FROM staff_social WHERE staff_id=? AND year=? AND month=?",
                        (sid, year, month))
        if rec:
            db.execute(
                """UPDATE staff_social SET base=?, co_pension=?, co_medical=?, co_unemployment=?,
                   co_injury=?, co_maternity=?, co_fund=?, personal_pension=?, personal_medical=?,
                   personal_unemployment=?, personal_fund=?, is_backpay=?, remark=? WHERE id=?""",
                (vals["base"], *[vals[f] for f in SOCIAL_CO_FIELDS],
                 *[vals[f] for f in SOCIAL_PERSONAL_FIELDS], is_backpay, remark, rec["id"]))
            updated += 1
        else:
            all_zero = vals["base"] == 0 and all(vals[f] == 0 for f in SOCIAL_CO_FIELDS + SOCIAL_PERSONAL_FIELDS)
            if all_zero:
                continue        # 全空行不落库
            db.execute(
                """INSERT INTO staff_social (staff_id, year, month, base, co_pension, co_medical,
                   co_unemployment, co_injury, co_maternity, co_fund, personal_pension,
                   personal_medical, personal_unemployment, personal_fund, is_backpay, remark)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (sid, year, month, vals["base"], *[vals[f] for f in SOCIAL_CO_FIELDS],
                 *[vals[f] for f in SOCIAL_PERSONAL_FIELDS], is_backpay, remark))
            created += 1
    log_op(db, "人员", "录入社保", "%d 年 %d 月社保保存：新建 %d 条、更新 %d 条" % (year, month, created, updated))
    return created, updated


def delete_social(db, social_id):
    r = query_one(db, "SELECT * FROM staff_social WHERE id=?", (social_id,))
    if not r:
        raise UserError("没有找到这条社保记录，可能已被删除，请刷新页面")
    staff = get_staff(db, r["staff_id"])
    db.execute("DELETE FROM staff_social WHERE id=?", (social_id,))
    log_op(db, "人员", "删除社保记录", "删除 %s %d 年 %d 月社保记录" % (staff["name"], r["year"], r["month"]))


def staff_salary_records(db, sid):
    return [dict(r) for r in query_all(
        db, "SELECT * FROM staff_salary WHERE staff_id=? ORDER BY year DESC, month DESC", (sid,))]


def staff_social_records(db, sid):
    return [dict(r) for r in query_all(
        db, "SELECT * FROM staff_social WHERE staff_id=? ORDER BY year DESC, month DESC", (sid,))]


def yearly_report(db, year, department="", community_id=0):
    """年度人力成本：按月汇总 + 部门排名 + 员工排名。"""
    cond = ""
    args = [year]
    if department:
        cond += " AND s.department = ?"
        args.append(department)
    if community_id:
        cond += " AND (s.community_id = ? OR s.community_id IS NULL)"
        args.append(community_id)
    monthly = []
    total = {"gross": 0, "net": 0, "social": 0, "fund": 0, "cost": 0}
    for month in range(1, 13):
        row = query_one(db, f"""
            SELECT COALESCE(SUM(sal.base + sal.allowance + sal.overtime + sal.subsidy),0) AS gross,
                   COALESCE(SUM(sal.base + sal.allowance + sal.overtime + sal.subsidy
                             - sal.social_deduct - sal.tax - sal.other_deduct),0) AS net
            FROM staff_salary sal JOIN staff s ON s.id = sal.staff_id
            WHERE sal.year = ? AND sal.month = ?{cond}""", args + [month])
        soc = query_one(db, f"""
            SELECT COALESCE(SUM(sc.co_pension + sc.co_medical + sc.co_unemployment
                            + sc.co_injury + sc.co_maternity),0) AS social,
                   COALESCE(SUM(sc.co_fund),0) AS fund
            FROM staff_social sc JOIN staff s ON s.id = sc.staff_id
            WHERE sc.year = ? AND sc.month = ?{cond}""", args + [month])
        item = {"month": month, "gross": row["gross"], "net": row["net"],
                "social": soc["social"], "fund": soc["fund"],
                "cost": row["net"] + soc["social"] + soc["fund"]}
        monthly.append(item)
        for k in total:
            total[k] += item[k]
    by_dept = [dict(r) for r in query_all(db, f"""
        SELECT s.department,
               COALESCE(SUM(sal.base + sal.allowance + sal.overtime + sal.subsidy
                         - sal.social_deduct - sal.tax - sal.other_deduct),0) AS net
        FROM staff_salary sal JOIN staff s ON s.id = sal.staff_id
        WHERE sal.year = ?{cond}
        GROUP BY s.department ORDER BY net DESC""", args)]
    soc_by_dept = {r["department"]: r["total"] for r in query_all(db, f"""
        SELECT s.department, COALESCE(SUM(sc.co_pension + sc.co_medical + sc.co_unemployment
                   + sc.co_injury + sc.co_maternity + sc.co_fund),0) AS total
        FROM staff_social sc JOIN staff s ON s.id = sc.staff_id
        WHERE sc.year = ?{cond} GROUP BY s.department""", args)}
    for d in by_dept:
        d["cost"] = d["net"] + soc_by_dept.get(d["department"], 0)
    by_staff = [dict(r) for r in query_all(db, f"""
        SELECT s.id, s.name, s.department, s.position,
               COALESCE(SUM(sal.base + sal.allowance + sal.overtime + sal.subsidy
                         - sal.social_deduct - sal.tax - sal.other_deduct),0) AS net
        FROM staff_salary sal JOIN staff s ON s.id = sal.staff_id
        WHERE sal.year = ?{cond}
        GROUP BY s.id ORDER BY net DESC LIMIT 10""", args)]
    soc_by_staff = {r["staff_id"]: r["total"] for r in query_all(db, f"""
        SELECT sc.staff_id, COALESCE(SUM(sc.co_pension + sc.co_medical + sc.co_unemployment
                   + sc.co_injury + sc.co_maternity + sc.co_fund),0) AS total
        FROM staff_social sc JOIN staff s ON s.id = sc.staff_id
        WHERE sc.year = ?{cond} GROUP BY sc.staff_id""", args)}
    for r in by_staff:
        r["cost"] = r["net"] + soc_by_staff.get(r["id"], 0)
    return {"year": year, "monthly": monthly, "total": total,
            "by_dept": [d for d in by_dept if d["department"]], "by_staff": by_staff}


def salary_month_rows(db, year, month=0, department=""):
    """导出用：某月（month=0 为全年）工资明细。"""
    sql = """SELECT sal.*, s.name, s.department, s.position FROM staff_salary sal
             JOIN staff s ON s.id = sal.staff_id
             WHERE sal.year = ?"""
    args = [year]
    if month:
        sql += " AND sal.month = ?"
        args.append(month)
    if department:
        sql += " AND s.department = ?"
        args.append(department)
    sql += " ORDER BY s.department, s.id"
    return [dict(r) for r in query_all(db, sql, args)]


def social_year_rows(db, year, department=""):
    sql = """SELECT sc.*, s.name, s.department, s.position FROM staff_social sc
             JOIN staff s ON s.id = sc.staff_id
             WHERE sc.year = ?"""
    args = [year]
    if department:
        sql += " AND s.department = ?"
        args.append(department)
    sql += " ORDER BY s.department, sc.month, s.id"
    return [dict(r) for r in query_all(db, sql, args)]


# ---------------------------------------------------------------- 员工档案导入

IMPORT_TEMPLATE_ROWS = [
    ["姓名", "性别", "出生日期", "身份证号", "手机号", "部门", "职位", "工作小区",
     "入职日期", "合同到期", "紧急联系人", "紧急电话", "现居住址", "备注"],
    ["张示例", "男", "1990-01-01", "", "13800001111", "客服", "客服主管", "示例小区",
     "2024-03-01", "2026-03-01", "李示例", "13800002222", "示例路 1 号", "示例数据，导入前请删除本行"],
    ["李演示", "女", "", "", "13800003333", "保洁", "保洁员", "", "", "", "", "", "", ""],
]

STAFF_CSV_COLS = ["姓名", "性别", "出生日期", "身份证号", "手机号", "部门", "职位",
                  "工作小区", "入职日期", "合同到期", "紧急联系人", "紧急电话",
                  "现居住址", "备注"]


def read_staff_csv(raw_bytes):
    import csv
    import io
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
    if "姓名" not in header:
        raise UserError("表头缺少必需列：姓名。请下载最新模板填写（不要修改表头）")
    idx = {h: i for i, h in enumerate(header)}
    out = []
    for lineno, r in enumerate(rows[1:], start=2):
        if not any(x.strip() for x in r):
            continue
        row = {c: (r[idx[c]].strip() if idx.get(c) is not None and idx[c] < len(r) else "")
               for c in STAFF_CSV_COLS}
        out.append((lineno, row))
    if not out:
        raise UserError("没有读到任何数据行，请检查文件内容")
    return out


def parse_staff_rows(db, rows):
    """解析员工档案 CSV 行：返回 (records, errors)。工作小区按小区名称匹配。"""
    col_map = {"姓名": "name", "性别": "gender", "出生日期": "birth_date",
               "身份证号": "id_number", "手机号": "phone", "部门": "department",
               "职位": "position", "入职日期": "hire_date", "合同到期": "contract_end",
               "紧急联系人": "emergency_name", "紧急电话": "emergency_phone",
               "现居住址": "address", "备注": "remark"}
    records, errors = [], []
    for lineno, row in rows:
        try:
            fake_form = {col_map.get(k, k): v for k, v in row.items()}
            fake_form["status"] = "active"
            fake_form["community_id"] = ""     # 工作小区按名称单独匹配
            f = _staff_fields(db, fake_form)
            community_id = None
            if row["工作小区"]:
                c = query_one(db, "SELECT id FROM community WHERE name=?",
                              (row["工作小区"],))
                if not c:
                    raise UserError("第 %d 行：工作小区“%s”不存在（请先在小区管理里创建）"
                                    % (lineno, row["工作小区"]))
                community_id = c["id"]
            records.append({"lineno": lineno, "f": f, "community_id": community_id,
                            "community_name": row["工作小区"], "errors": []})
        except UserError as e:
            errors.append(str(e))
    return records, errors


def analyze_staff_import(db, records):
    new, exist = 0, 0
    for r in records:
        if r["errors"]:
            continue
        f = r["f"]
        dup = None
        if f["id_number"]:
            dup = query_one(db, "SELECT id FROM staff WHERE id_number=? AND id_number!=''",
                            (f["id_number"],))
        if not dup and f["phone"]:
            dup = query_one(db, "SELECT id FROM staff WHERE phone=? AND phone!=''", (f["phone"],))
        if dup:
            exist += 1
        else:
            new += 1
    return {"new": new, "exist": exist, "valid": sum(1 for r in records if not r["errors"])}


def execute_staff_import(db, records):
    created = updated = skipped = 0
    for r in records:
        if r["errors"]:
            skipped += 1
            continue
        f = r["f"]
        dup = None
        if f["id_number"]:
            dup = query_one(db, "SELECT id FROM staff WHERE id_number=? AND id_number!=''",
                            (f["id_number"],))
        if not dup and f["phone"]:
            dup = query_one(db, "SELECT id FROM staff WHERE phone=? AND phone!=''", (f["phone"],))
        if dup:
            db.execute(
                """UPDATE staff SET name=?, gender=?, birth_date=?, department=?, position=?,
                   community_id=CASE WHEN ? IS NOT NULL THEN ? ELSE community_id END,
                   hire_date=CASE WHEN hire_date='' THEN ? ELSE hire_date END,
                   contract_end=CASE WHEN contract_end='' THEN ? ELSE contract_end END,
                   emergency_name=CASE WHEN emergency_name='' THEN ? ELSE emergency_name END,
                   emergency_phone=CASE WHEN emergency_phone='' THEN ? ELSE emergency_phone END,
                   address=CASE WHEN address='' THEN ? ELSE address END,
                   remark=CASE WHEN remark='' THEN ? ELSE remark END
                   WHERE id=?""",
                (f["name"], f["gender"], f["birth_date"], f["department"], f["position"],
                 f["community_id"], f["community_id"], f["hire_date"], f["contract_end"],
                 f["emergency_name"], f["emergency_phone"], f["address"], f["remark"], dup["id"]))
            updated += 1
        else:
            db.execute(
                """INSERT INTO staff (name, gender, birth_date, id_number, phone, department,
                   position, community_id, hire_date, leave_date, status, contract_end,
                   cert_name, cert_end, emergency_name, emergency_phone, address, remark)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (f["name"], f["gender"], f["birth_date"], f["id_number"], f["phone"],
                 f["department"], f["position"], f["community_id"], f["hire_date"],
                 "", "active", f["contract_end"], "", "", f["emergency_name"],
                 f["emergency_phone"], f["address"], f["remark"]))
            created += 1
    log_op(db, "人员", "导入员工档案", "导入员工：新建 %d 人、更新 %d 人、跳过 %d 行"
           % (created, updated, skipped))
    return {"created": created, "updated": updated, "skipped": skipped}

