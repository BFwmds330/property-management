# -*- coding: utf-8 -*-
"""通用工具：输入校验、金额/日期/账期处理、标签字典、CSV 导出。

设计原则：
- 金额一律以“分”为单位的整数存储，杜绝浮点误差；
- 所有用户输入先在这里校验，校验不过抛出 UserError（带友好中文提示），
  页面和接口统一捕获，绝不因为乱输入而崩溃。
"""
import csv
import io
import re
from datetime import date, datetime
from urllib.parse import quote

from flask import Response


class UserError(Exception):
    """业务校验错误，提示信息直接给用户看。"""


# ---------------------------------------------------------------- 金额

def fmt_money(fen):
    """分 -> '1,234.50'（元，保留两位小数，带千分位）。"""
    fen = int(fen or 0)
    sign = "-" if fen < 0 else ""
    fen = abs(fen)
    yuan = fen // 100
    rest = fen % 100
    return "%s%s.%02d" % (sign, format(yuan, ","), rest)


def parse_amount(value, field="金额", required=True, allow_zero=True):
    """把用户输入的金额字符串解析成“分”。非法输入抛 UserError。"""
    if value is None:
        value = ""
    s = str(value).strip().replace(",", "").replace("，", "")
    s = s.replace("￥", "").replace("¥", "").replace("元", "").replace(" ", "")
    if s == "":
        if required:
            raise UserError("请填写%s" % field)
        return 0
    if not re.fullmatch(r"\d+(\.\d{1,2})?", s):
        raise UserError("%s格式不正确：请输入不小于 0 的数字，最多两位小数，例如 350.50" % field)
    fen = int(round(float(s) * 100))
    if fen == 0 and not allow_zero:
        raise UserError("%s不能为 0" % field)
    if fen > 99999999999:
        raise UserError("%s太大了，请检查后重新输入" % field)
    return fen


def parse_area(value, field="面积", required=False):
    """面积（平方米，最多两位小数）-> 存成“0.01平方米”单位的整数。"""
    if value is None or str(value).strip() == "":
        if required:
            raise UserError("请填写%s" % field)
        return 0
    s = str(value).strip().replace("㎡", "").replace(" ", "")
    if not re.fullmatch(r"\d+(\.\d{1,2})?", s):
        raise UserError("%s格式不正确：请输入不小于 0 的数字，最多两位小数，例如 89.5" % field)
    v = int(round(float(s) * 100))
    if v > 99999999:
        raise UserError("%s太大了，请检查后重新输入" % field)
    return v


def fmt_area(area_100):
    """0.01平方米单位的整数 -> '89.50'。"""
    v = int(area_100 or 0)
    return "%.2f" % (v / 100)


# ---------------------------------------------------------------- 整数 / 文本

def parse_int(value, field, minv=None, maxv=None, required=True, default=None):
    if value is None or str(value).strip() == "":
        if required:
            raise UserError("请选择或填写%s" % field)
        return default
    s = str(value).strip()
    if not re.fullmatch(r"-?\d+", s):
        raise UserError("%s应为整数，请重新输入" % field)
    v = int(s)
    if minv is not None and v < minv:
        raise UserError("%s不能小于 %s" % (field, minv))
    if maxv is not None and v > maxv:
        raise UserError("%s不能大于 %s" % (field, maxv))
    return v


def safe_int(value, default=0):
    """把任意用户输入安全地转成整数；转不了就给默认值（绝不抛异常）。"""
    try:
        s = str(value).strip()
        if s == "":
            return default
        return int(s)
    except (TypeError, ValueError):
        return default


def clean_str(value, field, max_len=200, required=False):
    """清理普通文本输入：去首尾空格、限制长度。"""
    if value is None:
        s = ""
    else:
        s = str(value).strip()
    if not s:
        if required:
            raise UserError("请填写%s" % field)
        return ""
    if len(s) > max_len:
        raise UserError("%s太长了（最多 %d 个字）" % (field, max_len))
    return s


# ---------------------------------------------------------------- 手机号 / 身份证

def parse_phone(value, field="手机号", required=False):
    if value is None or str(value).strip() == "":
        if required:
            raise UserError("请填写%s" % field)
        return ""
    s = str(value).strip().replace(" ", "").replace("-", "")
    if not re.fullmatch(r"1\d{10}", s):
        raise UserError("%s应为 11 位数字，且以 1 开头（例如 13812345678）" % field)
    return s


_ID_WEIGHTS = [7, 9, 10, 5, 8, 4, 2, 1, 6, 3, 7, 9, 10, 5, 8, 4, 2]
_ID_MAP = "10X98765432"


def idcard_check_digit(first17):
    """根据前 17 位算出身份证第 18 位校验码。"""
    code = sum(int(c) * w for c, w in zip(first17, _ID_WEIGHTS)) % 11
    return _ID_MAP[code]


def parse_idcard(value, field="证件号", required=False):
    if value is None or str(value).strip() == "":
        if required:
            raise UserError("请填写%s" % field)
        return ""
    s = str(value).strip().upper().replace(" ", "")
    if not re.fullmatch(r"\d{17}[\dX]", s):
        raise UserError("%s应为 18 位：前 17 位是数字，最后一位是数字或 X" % field)
    if s[0] not in "123456":
        raise UserError("%s的前两位不是有效的省份代码，请核对" % field)
    try:
        datetime(int(s[6:10]), int(s[10:12]), int(s[12:14]))
    except ValueError:
        raise UserError("%s中的出生日期不正确，请核对" % field)
    if s[6:10] < "1900" or s[6:10] > "2030":
        raise UserError("%s中的出生年份不合理，请核对" % field)
    if idcard_check_digit(s[:17]) != s[17]:
        raise UserError("%s最后一位校验码不对，号码可能有误，请仔细核对" % field)
    return s


# ---------------------------------------------------------------- 日期

def parse_date(value, field="日期", required=False):
    if value is None or str(value).strip() == "":
        if required:
            raise UserError("请选择%s" % field)
        return ""
    s = str(value).strip()
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", s):
        raise UserError("%s格式应为 YYYY-MM-DD，请用日历控件选择" % field)
    try:
        datetime.strptime(s, "%Y-%m-%d")
    except ValueError:
        raise UserError("%s不是有效的日期，请核对" % field)
    return s


def house_label(code, unit, room_no):
    """房号的界面显示，如 1栋1单元1501室（楼栋编号里的 # 不参与显示）。"""
    return "%s栋%d单元%d室" % (str(code).replace("#", ""), unit, room_no)


_CN_DIGITS = "零壹贰叁肆伍陆柒捌玖"
_CN_UNITS = ["", "拾", "佰", "仟"]
_CN_GROUPS = ["", "万", "亿", "万亿"]


def money_capital(fen):
    """分 -> 人民币大写，如 45500 -> '肆佰伍拾伍元整'（收据打印用）。"""
    fen = int(round(fen or 0))
    if fen == 0:
        return "零元整"
    sign = "负" if fen < 0 else ""
    fen = abs(fen)
    yuan, cents = divmod(fen, 100)
    jiao, fen_one = divmod(cents, 10)

    def four_digits(n):
        """把 0~9999 的整数转成大写（内部函数，含'零'的读法）。"""
        out = []
        zero_pending = False
        started = False
        for pos in (3, 2, 1, 0):
            d = (n // (10 ** pos)) % 10
            if d == 0:
                if started:
                    zero_pending = True
                continue
            if zero_pending:
                out.append("零")
                zero_pending = False
            out.append(_CN_DIGITS[d] + _CN_UNITS[pos])
            started = True
        return "".join(out) or "零"

    parts = []
    if yuan > 0:
        groups = []          # 从低位起的每 4 位一组
        n = yuan
        while n > 0:
            groups.append(n % 10000)
            n //= 10000
        yuan_txt = ""
        for gi in range(len(groups) - 1, -1, -1):
            g = groups[gi]
            unit = _CN_GROUPS[gi] if gi < len(_CN_GROUPS) else ""
            if g == 0:
                # 整组为 0：补一个零（若后面还有数字）
                if gi > 0 and not yuan_txt.endswith("零"):
                    yuan_txt += "零"
                continue
            seg = four_digits(g)
            if g < 1000 and gi < len(groups) - 1 and yuan_txt and not yuan_txt.endswith("零"):
                seg = "零" + seg     # 组首不足仟且前面有数字，补零
            yuan_txt += seg + unit
        yuan_txt = yuan_txt.rstrip("零")
        parts.append(sign + yuan_txt + "元")
    elif sign:
        parts.append(sign)

    if jiao == 0 and fen_one == 0:
        if yuan > 0:
            parts.append("整")
    else:
        if jiao > 0:
            parts.append(_CN_DIGITS[jiao] + "角")
        elif yuan > 0 and fen_one > 0:
            parts.append("零")
        if fen_one > 0:
            parts.append(_CN_DIGITS[fen_one] + "分")
    return "".join(parts)


def today_str():
    return date.today().strftime("%Y-%m-%d")


def this_month():
    return date.today().strftime("%Y-%m")


# ---------------------------------------------------------------- 账期

def parse_period(cycle, value, field="账期"):
    """根据收费项目的计费周期解析账期。

    返回 (账期显示文本, 账期首月'YYYY-MM', 包含月数)。
    月：2026-09；季：2026-Q3（7~9月）；年：2026。
    """
    v = (value or "").strip()
    if cycle == "month":
        if not re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", v):
            raise UserError("%s格式应为 年-月（例如 2026-09），请重新选择" % field)
        return v, v, 1
    if cycle == "quarter":
        m = re.fullmatch(r"(\d{4})-Q([1-4])", v.upper())
        if not m:
            raise UserError("%s格式应为 年-季度（例如 2026-Q3），请重新选择" % field)
        start_month = (int(m.group(2)) - 1) * 3 + 1
        return "%s-Q%d" % (m.group(1), int(m.group(2))), "%s-%02d" % (m.group(1), start_month), 3
    if cycle == "year":
        if not re.fullmatch(r"\d{4}", v):
            raise UserError("%s应为 4 位年份（例如 2026）" % field)
        return v, v + "-01", 12
    raise UserError("收费项目的计费周期不正确，请先在“收费项目”里检查")


def month_add(ym, k):
    """'2026-09' 加/减 k 个月。"""
    y, m = int(ym[:4]), int(ym[5:7])
    t = (y * 12 + m - 1) + k
    return "%04d-%02d" % (t // 12, t % 12 + 1)


def months_owed(period_start):
    """欠费月数：从账期首月到当前月的自然月数（含首尾）。"""
    cur = this_month()
    y1, m1 = int(period_start[:4]), int(period_start[5:7])
    y2, m2 = int(cur[:4]), int(cur[5:7])
    n = (y2 - y1) * 12 + (m2 - m1) + 1
    return max(n, 1)


def period_options(cycle, around=6):
    """生成账期下拉选项：返回 [(值, 显示文本)]。"""
    opts = []
    if cycle == "month":
        ym = this_month()
        for i in range(-1, around):
            v = month_add(ym, i)
            opts.append((v, v))
    elif cycle == "quarter":
        y, m = int(this_month()[:4]), int(this_month()[5:7])
        q = (m - 1) // 3 + 1
        for i in range(-1, 4):
            qq = q + i
            yy = y + (qq - 1) // 4
            qq = (qq - 1) % 4 + 1
            opts.append(("%d-Q%d" % (yy, qq), "%d 年第 %d 季度" % (yy, qq)))
    else:
        y = int(this_month()[:4])
        for i in range(-1, 3):
            opts.append((str(y + i), "%d 年" % (y + i)))
    return opts


# ---------------------------------------------------------------- 标签字典

HOUSE_STATUS = {"vacant": "空置", "self": "自住", "rent": "出租", "renovate": "装修中"}
HOUSE_STATUS_COLOR = {"vacant": "secondary", "self": "success", "rent": "primary", "renovate": "warning"}

BILL_STATUS = {"unpaid": "未缴", "partial": "部分缴纳", "paid": "已缴清", "void": "已作废"}
BILL_STATUS_COLOR = {"unpaid": "danger", "partial": "warning", "paid": "success", "void": "secondary"}

ROLE = {"owner": "业主", "member": "家庭成员", "tenant": "租户"}

RELATIONS = ["配偶", "子女", "父母", "兄弟姐妹", "祖孙", "其他亲属", "其他"]

PAY_METHODS = ["现金", "转账", "扫码"]

REPAIR_STATUS = {"pending": "待处理", "processing": "处理中", "done": "已完成"}
REPAIR_STATUS_COLOR = {"pending": "danger", "processing": "warning", "done": "success"}

CYCLE_NAME = {"month": "按月", "quarter": "按季", "year": "按年"}
PRICING_NAME = {"area": "按建筑面积单价", "fixed": "按户固定金额"}

STAFF_STATUS = {"active": "在职", "probation": "试用", "suspended": "停薪留职", "left": "离职"}
STAFF_STATUS_COLOR = {"active": "success", "probation": "info", "suspended": "warning", "left": "secondary"}
STAFF_DEPARTMENTS = ["管理", "客服", "秩序", "工程", "保洁", "其他"]


def age_from_birth(birth_date):
    """由出生日期算年龄；格式不合法返回 None。"""
    if not birth_date or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", birth_date):
        return None
    try:
        b = date.fromisoformat(birth_date)
    except ValueError:
        return None
    today = date.today()
    age = today.year - b.year - ((today.month, today.day) < (b.month, b.day))
    return age if 0 <= age <= 150 else None


# ---------------------------------------------------------------- CSV 导出

def _csv_cell(v):
    """防 Excel 公式注入：文本以 = + @ 开头（或以 - 开头但不是负数）时前置单引号。"""
    if isinstance(v, str) and v:
        if v[0] in ("=", "+", "@"):
            return "'" + v
        if v[0] == "-" and not re.fullmatch(r"-[\d.]+", v):
            return "'" + v
    return v


def csv_response(filename, headers, rows):
    """生成 Excel 打开不乱码的 CSV（UTF-8 带 BOM），文本字段防公式注入。"""
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow([_csv_cell(c) for c in headers])
    for row in rows:
        writer.writerow(["" if c is None else _csv_cell(c) for c in row])
    data = "﻿" + buf.getvalue()
    resp = Response(data, mimetype="text/csv; charset=utf-8")
    ascii_name = "export.csv"
    resp.headers["Content-Disposition"] = "attachment; filename=%s; filename*=UTF-8''%s" % (
        ascii_name, quote(filename))
    return resp
