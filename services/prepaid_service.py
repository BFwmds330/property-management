# -*- coding: utf-8 -*-
"""预收款台账（v2.3.0）：多收的钱挂到房屋余额上，下次缴费可用。

记账规则（口径重要）：
- 正数 = 入账：一键缴清多收自动转预收、或手工登记预收；**不产生缴费流水**（现金当时
  没有对应到任何账期，不应计入收缴率的实收）；
- 负数 = 抵扣：用预收余额冲抵账单时，账单侧**同时**记一笔 method='预收抵扣' 的缴费流水
  （账单实收与流水口径保持一致），台账记等额负数；
- 删除"预收抵扣"流水时，等额退回预收余额（见 fee_service.delete_payment）。
"""
from database import log_op, query_all, query_one, scalar
from utils import UserError, clean_str, fmt_money, parse_amount, parse_date, PAY_METHODS


def get_balance(db, house_id):
    """该房屋当前的预收余额（分）。"""
    return scalar(db, "SELECT COALESCE(SUM(amount),0) FROM prepaid WHERE house_id=?", (house_id,))


def list_entries(db, house_id, limit=20):
    rows = query_all(db, "SELECT * FROM prepaid WHERE house_id=? ORDER BY id DESC LIMIT ?",
                     (house_id, limit))
    return [dict(r) for r in rows]


def add_ledger(db, house_id, amount_fen, method="", op_date="", remark="", log_detail=None):
    """写一条台账（正入负扣）。amount_fen=0 时跳过。返回台账 id 或 None。"""
    if not amount_fen:
        return None
    cur = db.execute(
        "INSERT INTO prepaid (house_id, amount, method, op_date, remark) VALUES (?,?,?,?,?)",
        (house_id, amount_fen, method, op_date, remark))
    if log_detail:
        log_op(db, "收费", "预收入账" if amount_fen > 0 else "预收抵扣", log_detail)
    return cur.lastrowid


def credit_from_form(db, house_id, form):
    """手工登记预收（房屋详情页表单）：金额/日期/方式必填，备注选填。"""
    from services.house_service import get_house_full, house_label
    house = get_house_full(db, house_id)
    amount = parse_amount(form.get("amount"), "预收金额", required=True, allow_zero=False)
    op_date = parse_date(form.get("op_date"), "日期", required=True)
    method = (form.get("method") or "").strip()
    if method not in PAY_METHODS:
        raise UserError("支付方式不正确")
    remark = clean_str(form.get("remark"), "备注", 200)
    label = house_label({"code": house["building_code"]}, house["unit"], house["room_no"])
    add_ledger(db, house_id, amount, method=method, op_date=op_date, remark=remark,
               log_detail="%s 登记预收 %s 元（%s）%s" % (
                   label, fmt_money(amount), method, "，备注：%s" % remark if remark else ""))
    return amount
