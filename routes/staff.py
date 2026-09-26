# -*- coding: utf-8 -*-
"""人员管理：员工档案、月度工资、社保缴纳、人力成本报表、档案导入。"""
import json
from datetime import date

from flask import Blueprint, flash, redirect, render_template, request, url_for

from database import query_all
from routes.helpers import safe
from services import staff_service
from utils import STAFF_DEPARTMENTS, csv_response, parse_int, safe_int

staff_bp = Blueprint("staff", __name__)


@staff_bp.route("/staff")
@safe
def staff_list(db):
    args = request.args
    rows = staff_service.list_staff(
        db, keyword=args.get("keyword", ""), department=args.get("department", ""),
        status=args.get("status", ""), community_id=safe_int(args.get("community_id")),
        show_left=bool(args.get("show_left")))
    communities = query_all(db, "SELECT id, name FROM community ORDER BY id")
    return render_template("staff/list.html", rows=rows, communities=[dict(c) for c in communities],
                           departments=STAFF_DEPARTMENTS,
                           f_keyword=args.get("keyword", ""), f_department=args.get("department", ""),
                           f_status=args.get("status", ""), f_community=args.get("community_id", ""),
                           f_show_left=args.get("show_left", ""), active_nav="staff")


@staff_bp.route("/staff/add")
def staff_add():
    return render_template("staff/form.html", staff=None, departments=STAFF_DEPARTMENTS,
                           active_nav="staff")


@staff_bp.route("/staff/add", methods=["POST"])
@safe
def staff_add_post(db):
    sid = staff_service.add_staff(db, request.form)
    db.commit()
    flash("员工添加成功", "success")
    return redirect(url_for("staff.staff_detail", sid=sid))


@staff_bp.route("/staff/<int:sid>")
@safe
def staff_detail(db, sid):
    staff = staff_service.get_staff(db, sid)
    salary_records = staff_service.staff_salary_records(db, sid)
    social_records = staff_service.staff_social_records(db, sid)
    return render_template("staff/detail.html", staff=staff, salary_records=salary_records,
                           social_records=social_records, active_nav="staff")


@staff_bp.route("/staff/<int:sid>/edit")
@safe
def staff_edit(db, sid):
    staff = staff_service.get_staff(db, sid)
    return render_template("staff/form.html", staff=staff, departments=STAFF_DEPARTMENTS,
                           active_nav="staff")


@staff_bp.route("/staff/<int:sid>/edit", methods=["POST"])
@safe
def staff_edit_post(db, sid):
    staff_service.update_staff(db, sid, request.form)
    db.commit()
    flash("员工信息已保存", "success")
    return redirect(url_for("staff.staff_detail", sid=sid))


@staff_bp.route("/staff/<int:sid>/delete", methods=["POST"])
@safe
def staff_delete(db, sid):
    staff_service.delete_staff(db, sid)
    db.commit()
    flash("员工已删除", "success")
    return redirect(url_for("staff.staff_list"))


# ---------------------------------------------------------------- 工资

@staff_bp.route("/staff/salary")
@safe
def salary_batch(db):
    year = parse_int(request.args.get("year"), "年份", 2000, 2100, required=False,
                     default=None) or date.today().year
    month = parse_int(request.args.get("month"), "月份", 1, 12, required=False, default=None) or date.today().month
    rows = staff_service.salary_batch_rows(db, year, month)
    existing = sum(1 for r in rows if r["record"])
    return render_template("staff/salary.html", rows=rows, year=year, month=month,
                           existing=existing, active_nav="staff")


@staff_bp.route("/staff/salary/save", methods=["POST"])
@safe
def salary_save(db):
    year = request.form.get("year")
    month = request.form.get("month")
    created, updated = staff_service.save_salary_batch(db, year, month, request.form)
    db.commit()
    flash("工资保存成功：%d 年 %d 月，新建 %d 条、更新 %d 条" % (int(year), int(month), created, updated),
          "success")
    return redirect(url_for("staff.salary_batch", year=year, month=month))


@staff_bp.route("/staff/salary/<int:salary_id>/delete", methods=["POST"])
@safe
def salary_delete(db, salary_id):
    staff_service.delete_salary(db, salary_id)
    db.commit()
    flash("工资记录已删除", "success")
    return redirect(request.referrer or url_for("staff.salary_batch"))


# ---------------------------------------------------------------- 社保

@staff_bp.route("/staff/social")
@safe
def social_batch(db):
    year = parse_int(request.args.get("year"), "年份", 2000, 2100, required=False,
                     default=None) or date.today().year
    month = parse_int(request.args.get("month"), "月份", 1, 12, required=False, default=None) or date.today().month
    rows = staff_service.social_batch_rows(db, year, month)
    existing = sum(1 for r in rows if r["record"])
    return render_template("staff/social.html", rows=rows, year=year, month=month,
                           existing=existing, active_nav="staff")


@staff_bp.route("/staff/social/save", methods=["POST"])
@safe
def social_save(db):
    year = request.form.get("year")
    month = request.form.get("month")
    created, updated = staff_service.save_social_batch(db, year, month, request.form)
    db.commit()
    flash("社保保存成功：%d 年 %d 月，新建 %d 条、更新 %d 条" % (int(year), int(month), created, updated),
          "success")
    return redirect(url_for("staff.social_batch", year=year, month=month))


@staff_bp.route("/staff/social/<int:social_id>/delete", methods=["POST"])
@safe
def social_delete(db, social_id):
    staff_service.delete_social(db, social_id)
    db.commit()
    flash("社保记录已删除", "success")
    return redirect(request.referrer or url_for("staff.social_batch"))


# ---------------------------------------------------------------- 报表

@staff_bp.route("/staff/report")
@safe
def staff_report(db):
    year = parse_int(request.args.get("year"), "年份", 2000, 2100, required=False,
                     default=None) or date.today().year
    department = request.args.get("department", "")
    data = staff_service.yearly_report(db, year, department=department)
    return render_template("staff/report.html", data=data, f_department=department,
                           departments=STAFF_DEPARTMENTS, active_nav="staff")


@staff_bp.route("/staff/report/salary.csv")
@safe
def salary_csv(db):
    year = parse_int(request.args.get("year"), "年份", 2000, 2100, required=False, default=None) or date.today().year
    department = request.args.get("department", "")
    rows = staff_service.salary_month_rows(db, year, 0, department)
    headers = ["姓名", "部门", "职位", "月份", "基本工资(元)", "岗位津贴(元)", "加班费(元)",
               "其他补贴(元)", "社保个人代扣(元)", "个税(元)", "其他扣款(元)", "实发(元)",
               "发放日期", "方式", "备注"]
    out = []
    for r in rows:
        net = (r["base"] + r["allowance"] + r["overtime"] + r["subsidy"]
               - r["social_deduct"] - r["tax"] - r["other_deduct"])
        out.append([r["name"], r["department"], r["position"], "%d 年 %d 月" % (r["year"], r["month"]),
                    "%.2f" % (r["base"] / 100), "%.2f" % (r["allowance"] / 100),
                    "%.2f" % (r["overtime"] / 100), "%.2f" % (r["subsidy"] / 100),
                    "%.2f" % (r["social_deduct"] / 100), "%.2f" % (r["tax"] / 100),
                    "%.2f" % (r["other_deduct"] / 100), "%.2f" % (net / 100),
                    r["pay_date"], r["method"], r["remark"]])
    return csv_response("%d 年工资表.csv" % year, headers, out)


@staff_bp.route("/staff/report/social.csv")
@safe
def social_csv(db):
    year = parse_int(request.args.get("year"), "年份", 2000, 2100, required=False, default=None) or date.today().year
    department = request.args.get("department", "")
    rows = staff_service.social_year_rows(db, year, department)
    headers = ["姓名", "部门", "职位", "月份", "缴纳基数(元)", "单位养老(元)", "单位医疗(元)",
               "单位失业(元)", "单位工伤(元)", "单位生育(元)", "单位公积金(元)", "个人养老(元)",
               "个人医疗(元)", "个人失业(元)", "个人公积金(元)", "是否补缴", "备注"]
    out = []
    for r in rows:
        out.append([r["name"], r["department"], r["position"], "%d 年 %d 月" % (r["year"], r["month"]),
                    "%.2f" % (r["base"] / 100)] +
                   ["%.2f" % (r[f] / 100) for f in
                    ("co_pension", "co_medical", "co_unemployment", "co_injury",
                     "co_maternity", "co_fund", "personal_pension", "personal_medical",
                     "personal_unemployment", "personal_fund")] +
                   ["是" if r["is_backpay"] else "否", r["remark"]])
    return csv_response("%d 年社保缴纳汇总.csv" % year, headers, out)


# ---------------------------------------------------------------- 档案导入

@staff_bp.route("/staff/import")
def staff_import_form():
    return render_template("staff/import.html", active_nav="staff")


@staff_bp.route("/staff/import/template")
def staff_import_template():
    return csv_response("员工档案导入模板.csv",
                        staff_service.IMPORT_TEMPLATE_ROWS[0],
                        staff_service.IMPORT_TEMPLATE_ROWS[1:])


@staff_bp.route("/staff/import/preview", methods=["POST"])
@safe
def staff_import_preview(db):
    f = request.files.get("file")
    if not f or not f.filename:
        from utils import UserError
        raise UserError("请先选择要导入的 CSV 文件")
    rows = staff_service.read_staff_csv(f.read())
    records, errors = staff_service.parse_staff_rows(db, rows)
    stats = staff_service.analyze_staff_import(db, records)
    return render_template("staff/import.html", records=records, errors=errors, stats=stats,
                           payload=json.dumps(rows, ensure_ascii=False),
                           total_rows=len(records), active_nav="staff")


@staff_bp.route("/staff/import/confirm", methods=["POST"])
@safe
def staff_import_confirm(db):
    payload = request.form.get("payload", "")
    try:
        rows = [(int(i), r) for i, r in json.loads(payload)]
    except (ValueError, TypeError):
        flash("导入数据已过期，请重新上传文件", "warning")
        return redirect(url_for("staff.staff_import_form"))
    records, errors = staff_service.parse_staff_rows(db, rows)
    if errors:
        flash("还有 %d 行数据有问题，无法导入，请修正后重新上传" % len(errors), "danger")
        return redirect(url_for("staff.staff_import_form"))
    result = staff_service.execute_staff_import(db, records)
    db.commit()
    flash("导入完成：新建员工 %d 人、更新 %d 人" % (result["created"], result["updated"]), "success")
    return redirect(url_for("staff.staff_list"))
