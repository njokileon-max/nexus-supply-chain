# Copyright (c) 2026, Nexus Supply Chain and contributors
# For license information, please see license.txt

import frappe
import calendar
from frappe.utils import getdate, get_first_day, add_months, nowdate, get_datetime

def get_working_days(start_date, end_date):
    """Calculates working days between two dates, excluding Sundays."""
    start = get_datetime(start_date)
    end = get_datetime(end_date)
    days = 0
    curr = start
    while curr <= end:
        if curr.weekday() != 6:
            days += 1
        from datetime import timedelta
        curr += timedelta(days=1)
    return days

@frappe.whitelist()
def get_dashboard_data(start_date=None, end_date=None):
    is_custom = bool(start_date and end_date)
    
    today = getdate(end_date) if end_date else getdate(nowdate())
    period_start = getdate(start_date) if start_date else get_first_day(today)
    year_start = getdate(start_date) if start_date else today.replace(month=1, day=1)
    pdc_end_date = add_months(today, 2)
    
    total_working_days = get_working_days(period_start, today) or 1
    
    metrics_map = {}

    def init_rep(sp_id):
        if not sp_id:
            sp_id = "Unknown"
        if sp_id not in metrics_map:
            metrics_map[sp_id] = {
                "sales_person_id": sp_id,
                "sales_person_name": sp_id,
                "territory": "Unassigned",
                "sales_target": 0.0,
                "collection_target": 0.0,
                "target_prorated_sales": 0.0,
                "target_prorated_coll": 0.0,
                "actual_sales": 0.0,
                "actual_collections": 0.0,
                "total_outstanding": 0.0,
                "total_overdue": 0.0,
                "pdc_amount": 0.0
            }
        return metrics_map[sp_id]

    sales_data = frappe.db.sql("""
        SELECT 
            st.sales_person, 
            SUM(si.base_grand_total * (st.allocated_percentage / 100.0)) as actual_sales
        FROM `tabSales Invoice` si
        INNER JOIN `tabSales Team` st ON st.parent = si.name AND st.parenttype = 'Sales Invoice'
        WHERE si.docstatus = 1 AND si.is_return = 0 AND si.posting_date BETWEEN %(start)s AND %(end)s
        GROUP BY st.sales_person
    """, {"start": period_start, "end": today}, as_dict=True)
    
    for row in sales_data:
        rep = init_rep(row.sales_person)
        rep["actual_sales"] += float(row.actual_sales or 0)

    collections_data = frappe.db.sql("""
        SELECT 
            cust_st.sales_person,
            SUM(pe.base_received_amount * (cust_st.allocated_percentage / 100.0)) as actual_collections
        FROM `tabPayment Entry` pe
        INNER JOIN `tabSales Team` cust_st ON cust_st.parent = pe.party AND cust_st.parenttype = 'Customer'
        WHERE pe.docstatus = 1 AND pe.payment_type = 'Receive' AND pe.party_type = 'Customer'
          AND pe.posting_date BETWEEN %(start)s AND %(end)s
        GROUP BY cust_st.sales_person
    """, {"start": period_start, "end": today}, as_dict=True)
    
    for row in collections_data:
        rep = init_rep(row.sales_person)
        rep["actual_collections"] += float(row.actual_collections or 0)

    aging_data = frappe.db.sql("""
        SELECT 
            cust_st.sales_person,
            SUM(si.outstanding_amount * (cust_st.allocated_percentage / 100.0)) as total_outstanding,
            SUM(CASE WHEN si.due_date < %(today)s THEN si.outstanding_amount ELSE 0 END * (cust_st.allocated_percentage / 100.0)) as total_overdue
        FROM `tabSales Invoice` si
        INNER JOIN `tabSales Team` cust_st ON cust_st.parent = si.customer AND cust_st.parenttype = 'Customer'
        WHERE si.docstatus = 1 AND si.is_return = 0 AND si.outstanding_amount > 0 AND si.posting_date <= %(today)s
        GROUP BY cust_st.sales_person
    """, {"today": today}, as_dict=True)

    for row in aging_data:
        rep = init_rep(row.sales_person)
        rep["total_outstanding"] += float(row.total_outstanding or 0)
        rep["total_overdue"] += float(row.total_overdue or 0)

    pdc_data = frappe.db.sql("""
        SELECT 
            cust_st.sales_person, 
            SUM(pe.base_paid_amount * (cust_st.allocated_percentage / 100.0)) as pdc_amount
        FROM `tabPayment Entry` pe
        INNER JOIN `tabSales Team` cust_st ON cust_st.parent = pe.party AND cust_st.parenttype = 'Customer'
        WHERE pe.docstatus < 2 AND pe.payment_type = 'Receive' AND pe.party_type = 'Customer'
          AND pe.reference_date BETWEEN %(today)s AND %(pdc_end)s
        GROUP BY cust_st.sales_person
    """, {"today": today, "pdc_end": pdc_end_date}, as_dict=True)

    for row in pdc_data:
        rep = init_rep(row.sales_person)
        rep["pdc_amount"] += float(row.pdc_amount or 0)

    sp_keys = list(metrics_map.keys())
    if sp_keys:
        sp_details = frappe.db.sql("""
            SELECT 
                name, 
                sales_person_name, 
                COALESCE(custom_territory_assigned, 'Unassigned') as territory,
                COALESCE(custom_sales_target, 0) as custom_sales_target, 
                COALESCE(custom_collections_target, 0) as custom_collections_target
            FROM `tabSales Person`
            WHERE name IN %s
        """, (tuple(sp_keys),), as_dict=True)
        
        for sp in sp_details:
            rep = metrics_map.get(sp.name)
            if not rep:
                continue
                
            st = float(sp.custom_sales_target)
            ct = float(sp.custom_collections_target)

            rep["sales_person_name"] = sp.sales_person_name or sp.name
            rep["territory"] = sp.territory
            rep["sales_target"] = st
            rep["collection_target"] = ct
            rep["target_prorated_sales"] = st if is_custom else ((st / 26.0) * total_working_days)
            rep["target_prorated_coll"] = ct if is_custom else ((ct / 26.0) * total_working_days)

    for sp_id, vals in metrics_map.items():
        vals["sales_pct"] = (vals["actual_sales"] / vals["target_prorated_sales"] * 100.0) if vals["target_prorated_sales"] else 0.0
        vals["sales_deficit"] = vals["actual_sales"] - vals["target_prorated_sales"]
        
        vals["collection_pct"] = (vals["actual_collections"] / vals["target_prorated_coll"] * 100.0) if vals["target_prorated_coll"] else 0.0
        vals["collection_deficit"] = vals["actual_collections"] - vals["target_prorated_coll"]
        
        vals["coll_sales_diff"] = vals["actual_collections"] - vals["actual_sales"]

    active_reps = len(metrics_map)

    return {
        "is_custom": is_custom,
        "active_reps": active_reps,
        "dates": {
            "start": period_start.strftime('%d-%b-%Y'),
            "end": today.strftime('%d-%b-%Y'),
            "year_start": year_start.strftime('%d-%b-%Y'),
            "pdc_end": pdc_end_date.strftime('%d-%b-%Y')
        },
        "metrics": list(metrics_map.values())
    }