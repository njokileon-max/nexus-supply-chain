# gnleon29@gmail.com

import frappe
from frappe.utils import flt, getdate, nowdate
from datetime import timedelta

def get_working_days(start_date, end_date):
    start = getdate(start_date)
    end = getdate(end_date)
    days = 0
    curr = start
    while curr <= end:
        if curr.weekday() != 6:
            days += 1
        curr += timedelta(days=1)
    return days if days > 0 else 1

@frappe.whitelist()
def get_treasury_dashboard_data(start_date=None, end_date=None, custom_working_days=None):
    today_str = nowdate()
    if not end_date:
        end_date = today_str
    if not start_date:
        start_date = getdate(end_date).replace(day=1).strftime('%Y-%m-%d')
        
    start_dt = getdate(start_date)
    end_dt = getdate(end_date)
    
    if custom_working_days:
        days_elapsed = flt(custom_working_days)
    else:
        days_elapsed = get_working_days(start_dt, end_dt)
        
    if days_elapsed <= 0: 
        days_elapsed = 1

    bank_accounts = frappe.db.sql("""
        SELECT 
            acc.account_name, 
            SUM(gl.debit) - SUM(gl.credit) AS balance
        FROM `tabGL Entry` gl
        INNER JOIN `tabAccount` acc ON gl.account = acc.name
        WHERE gl.is_cancelled = 0
            AND gl.posting_date <= %(end)s
            AND acc.account_type IN ('Bank', 'Cash')
        GROUP BY acc.account_name
        HAVING balance != 0
        ORDER BY balance DESC
    """, {"end": end_dt}, as_dict=True)
    
    total_liquid_cash = sum(flt(acc.balance) for acc in bank_accounts)

    gl_data = frappe.db.sql("""
        SELECT SUM(gl.debit) - SUM(gl.credit) AS net_expense
        FROM `tabGL Entry` gl
        INNER JOIN `tabAccount` acc ON gl.account = acc.name
        WHERE gl.posting_date BETWEEN %(start)s AND %(end)s
            AND gl.is_cancelled = 0
            AND acc.root_type = 'Expense'
            AND acc.account_type NOT IN ('Cost of Goods Sold', 'Expenses Included In Valuation', 'Stock Adjustment')
            AND acc.account_name NOT LIKE 'Cost of Sales%%'
            AND acc.account_name NOT LIKE 'Cost Of Sales%%'
    """, {"start": start_dt, "end": end_dt}, as_dict=True)
    
    mtd_overhead = flt(gl_data[0].net_expense) if gl_data and gl_data[0].net_expense else 0.0
    daily_cash_burn = mtd_overhead / days_elapsed

    sales_invoices = frappe.db.sql("""
        SELECT 
            name as id, customer as entity_id, customer_name as entity_name, posting_date, due_date, 
            outstanding_amount,
            DATEDIFF(%(end)s, due_date) AS days_past_due
        FROM `tabSales Invoice`
        WHERE docstatus = 1 AND is_return = 0 AND outstanding_amount > 0
        ORDER BY days_past_due DESC
    """, {"end": end_dt}, as_dict=True)

    purchase_invoices = frappe.db.sql("""
        SELECT 
            name as id, supplier as entity_id, supplier_name as entity_name, posting_date, due_date, 
            outstanding_amount,
            DATEDIFF(%(end)s, due_date) AS days_past_due
        FROM `tabPurchase Invoice`
        WHERE docstatus = 1 AND is_return = 0 AND outstanding_amount > 0
        ORDER BY days_past_due DESC
    """, {"end": end_dt}, as_dict=True)

    total_ar = 0.0
    total_ap = 0.0
    top_debtors = {}
    top_creditors = {}

    for si in sales_invoices:
        amt = flt(si.outstanding_amount)
        total_ar += amt
        if flt(si.days_past_due) > 0:
            name_key = si.entity_name or si.entity_id
            top_debtors[name_key] = top_debtors.get(name_key, 0.0) + amt

    for pi in purchase_invoices:
        amt = flt(pi.outstanding_amount)
        total_ap += amt
        if flt(pi.days_past_due) > 0:
            name_key = pi.entity_name or pi.entity_id
            top_creditors[name_key] = top_creditors.get(name_key, 0.0) + amt

    sorted_debtors = sorted([{"entity_name": k, "amount": v} for k, v in top_debtors.items()], key=lambda x: x['amount'], reverse=True)[:5]
    sorted_creditors = sorted([{"entity_name": k, "amount": v} for k, v in top_creditors.items()], key=lambda x: x['amount'], reverse=True)[:5]

    cash_runway_days = (total_liquid_cash / daily_cash_burn) if daily_cash_burn > 0 else 999.0
    quick_ratio = ((total_liquid_cash + total_ar) / total_ap) if total_ap > 0 else 999.0
    net_cash_position = (total_liquid_cash + total_ar) - total_ap

    return {
        "period": f"{start_dt.strftime('%d-%b-%Y')} to {end_dt.strftime('%d-%b-%Y')}",
        "working_days": days_elapsed,
        "total_liquid_cash": total_liquid_cash,
        "bank_accounts": bank_accounts,
        "mtd_overhead": mtd_overhead,
        "daily_cash_burn": daily_cash_burn,
        "cash_runway_days": cash_runway_days,
        "total_ar": total_ar,
        "total_ap": total_ap,
        "net_cash_position": net_cash_position,
        "quick_ratio": quick_ratio,
        "sales_invoices": sales_invoices,
        "purchase_invoices": purchase_invoices,
        "top_debtors": sorted_debtors,
        "top_creditors": sorted_creditors
    }
