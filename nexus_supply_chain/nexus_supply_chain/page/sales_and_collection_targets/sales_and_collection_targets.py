# Copyright (c) 2026, Nexus Supply Chain and contributors
# For license information, please see license.txt

import frappe
from frappe.utils import getdate, get_first_day, nowdate, add_days, flt

@frappe.whitelist()
def get_dashboard_data(start_date=None, end_date=None):
    if not frappe.has_permission("Sales Invoice", "read"):
        frappe.throw("You do not have permission to access Ledger Analytics.")

    is_custom = bool(start_date and end_date)
    today         = getdate(end_date)   if end_date   else getdate(nowdate())
    period_start  = getdate(start_date) if start_date else get_first_day(today)
    pdc_end_date  = add_days(today, 120)

    metrics_map = {}

    def init_rep(sp_id):
        if not sp_id:
            sp_id = "Unassigned"
        if sp_id not in metrics_map:
            metrics_map[sp_id] = {
                "sales_person_id":      sp_id,
                "sales_person_name":    sp_id,
                "territory":            "Unassigned",
                # Targets (populated in Phase 6)
                "sales_target":         0.0,
                "collection_target":    0.0,
                # IMAGE 1 — Sales Invoice Report
                "actual_sales":         0.0,   # SUM(base_grand_total * alloc%)
                "today_sales":          0.0,
                # IMAGE 2 — Test 8 Collections Register
                "actual_collections":   0.0,   # SUM(amount collected * alloc%)
                "today_collections":    0.0,
                # Delivery Notes
                "actual_delivered":     0.0,   # SUM(base_grand_total * alloc%)
                # IMAGE 3 — Outstanding Debts Report
                "total_outstanding":    0.0,   # SUM(outstanding_amount * alloc%) all-time
                "total_overdue":        0.0,   # subset where due_date < today
                # IMAGE 4 — PDC Register
                "pdc_amount":           0.0,   # Total PDC (ref_date >= today, <= today+120)
                "pdc_m1":               0.0,   # Days   1-30
                "pdc_m2":               0.0,   # Days  31-60
                "pdc_m3":               0.0,   # Days  61-90
                "pdc_m4":               0.0,   # Days 91-120
            }
        return metrics_map[sp_id]


    sales_data = frappe.db.sql("""
        SELECT
            st.sales_person,
            SUM(si.base_grand_total * (st.allocated_percentage / 100.0))
                AS actual_sales,
            SUM(
                CASE WHEN si.posting_date = %(today)s
                     THEN si.base_grand_total * (st.allocated_percentage / 100.0)
                     ELSE 0
                END
            ) AS today_sales
        FROM `tabSales Invoice` si
        INNER JOIN `tabSales Team` st
            ON  st.parent     = si.name
            AND st.parenttype = 'Sales Invoice'
        WHERE si.docstatus      = 1
          AND si.is_return      = 0
          AND si.posting_date BETWEEN %(start)s AND %(end)s
        GROUP BY st.sales_person
    """, {"start": period_start, "end": today, "today": today}, as_dict=True)

    for row in sales_data:
        rep = init_rep(row.sales_person)
        rep["actual_sales"]  += flt(row.actual_sales)
        rep["today_sales"]   += flt(row.today_sales)

    if not metrics_map:
        return {"is_custom": is_custom, "active_reps": 0, "metrics": []}


    delivery_data = frappe.db.sql("""
        SELECT
            st.sales_person,
            SUM(dn.base_grand_total * (st.allocated_percentage / 100.0))
                AS actual_delivered
        FROM `tabDelivery Note` dn
        INNER JOIN `tabSales Team` st
            ON  st.parent     = dn.name
            AND st.parenttype = 'Delivery Note'
        WHERE dn.docstatus = 1
          AND dn.is_return  = 0
          AND dn.status NOT IN ('Closed', 'Cancelled', 'Return')
          AND dn.posting_date BETWEEN %(start)s AND %(end)s
        GROUP BY st.sales_person
    """, {"start": period_start, "end": today}, as_dict=True)

    for row in delivery_data:
        if row.sales_person in metrics_map:
            metrics_map[row.sales_person]["actual_delivered"] += flt(row.actual_delivered)

    linked_collections = frappe.db.sql("""
        SELECT
            st.sales_person,
            SUM(per.allocated_amount * (st.allocated_percentage / 100.0))
                AS actual_collections,
            SUM(
                CASE WHEN pe.posting_date = %(today)s
                     THEN per.allocated_amount * (st.allocated_percentage / 100.0)
                     ELSE 0
                END
            ) AS today_collections
        FROM `tabPayment Entry` pe
        INNER JOIN `tabPayment Entry Reference` per
            ON  per.parent              = pe.name
            AND per.reference_doctype   = 'Sales Invoice'
        INNER JOIN `tabSales Team` st
            ON  st.parent     = per.reference_name
            AND st.parenttype = 'Sales Invoice'
        WHERE pe.docstatus       = 1
          AND pe.payment_type    = 'Receive'
          AND pe.party_type      = 'Customer'
          AND pe.posting_date BETWEEN %(start)s AND %(end)s
        GROUP BY st.sales_person
    """, {"start": period_start, "end": today, "today": today}, as_dict=True)

    for row in linked_collections:
        if row.sales_person in metrics_map:
            metrics_map[row.sales_person]["actual_collections"] += flt(row.actual_collections)
            metrics_map[row.sales_person]["today_collections"]  += flt(row.today_collections)

    unlinked_collections = frappe.db.sql("""
        SELECT
            cst.sales_person,
            SUM(pe.base_received_amount * (cst.allocated_percentage / 100.0))
                AS actual_collections,
            SUM(
                CASE WHEN pe.posting_date = %(today)s
                     THEN pe.base_received_amount * (cst.allocated_percentage / 100.0)
                     ELSE 0
                END
            ) AS today_collections
        FROM `tabPayment Entry` pe
        INNER JOIN `tabSales Team` cst
            ON  cst.parent     = pe.party
            AND cst.parenttype = 'Customer'
        WHERE pe.docstatus    = 1
          AND pe.payment_type = 'Receive'
          AND pe.party_type   = 'Customer'
          AND pe.posting_date BETWEEN %(start)s AND %(end)s
          -- Exclude PEs that have at least one invoice reference (handled in 3-A)
          AND NOT EXISTS (
              SELECT 1 FROM `tabPayment Entry Reference` per2
              WHERE per2.parent            = pe.name
                AND per2.reference_doctype = 'Sales Invoice'
          )
        GROUP BY cst.sales_person
    """, {"start": period_start, "end": today, "today": today}, as_dict=True)

    for row in unlinked_collections:
        if row.sales_person in metrics_map:
            metrics_map[row.sales_person]["actual_collections"] += flt(row.actual_collections)
            metrics_map[row.sales_person]["today_collections"]  += flt(row.today_collections)

    outstanding_data = frappe.db.sql("""
        SELECT
            st.sales_person,
            SUM(si.outstanding_amount * (st.allocated_percentage / 100.0))
                AS total_outstanding,
            SUM(
                CASE WHEN si.due_date < %(today)s
                     THEN si.outstanding_amount * (st.allocated_percentage / 100.0)
                     ELSE 0
                END
            ) AS total_overdue
        FROM `tabSales Invoice` si
        INNER JOIN `tabSales Team` st
            ON  st.parent     = si.name
            AND st.parenttype = 'Sales Invoice'
        WHERE si.docstatus        = 1
          AND si.is_return        = 0
          AND si.outstanding_amount > 0
        GROUP BY st.sales_person
    """, {"today": today}, as_dict=True)

    for row in outstanding_data:
        if row.sales_person in metrics_map:
            metrics_map[row.sales_person]["total_outstanding"] += flt(row.total_outstanding)
            metrics_map[row.sales_person]["total_overdue"]     += flt(row.total_overdue)


    pdc_data = frappe.db.sql("""
        SELECT
            cst.sales_person,
            SUM(pe.base_received_amount * (cst.allocated_percentage / 100.0))
                AS pdc_amount,
            SUM(
                CASE WHEN DATEDIFF(pe.reference_date, %(today)s) BETWEEN 0  AND 30
                     THEN pe.base_received_amount * (cst.allocated_percentage / 100.0)
                     ELSE 0 END
            ) AS pdc_m1,
            SUM(
                CASE WHEN DATEDIFF(pe.reference_date, %(today)s) BETWEEN 31 AND 60
                     THEN pe.base_received_amount * (cst.allocated_percentage / 100.0)
                     ELSE 0 END
            ) AS pdc_m2,
            SUM(
                CASE WHEN DATEDIFF(pe.reference_date, %(today)s) BETWEEN 61 AND 90
                     THEN pe.base_received_amount * (cst.allocated_percentage / 100.0)
                     ELSE 0 END
            ) AS pdc_m3,
            SUM(
                CASE WHEN DATEDIFF(pe.reference_date, %(today)s) BETWEEN 91 AND 120
                     THEN pe.base_received_amount * (cst.allocated_percentage / 100.0)
                     ELSE 0 END
            ) AS pdc_m4
        FROM `tabPayment Entry` pe
        INNER JOIN `tabSales Team` cst
            ON  cst.parent     = pe.party
            AND cst.parenttype = 'Customer'
        WHERE pe.docstatus       = 1
          AND pe.payment_type    = 'Receive'
          AND pe.party_type      = 'Customer'
          AND pe.reference_date >= %(today)s          -- FIXED: was > today (missed same-day)
          AND pe.reference_date <= %(pdc_end)s
        GROUP BY cst.sales_person
    """, {"today": today, "pdc_end": pdc_end_date}, as_dict=True)

    for row in pdc_data:
        if row.sales_person in metrics_map:
            metrics_map[row.sales_person]["pdc_amount"] += flt(row.pdc_amount)
            metrics_map[row.sales_person]["pdc_m1"]     += flt(row.pdc_m1)
            metrics_map[row.sales_person]["pdc_m2"]     += flt(row.pdc_m2)
            metrics_map[row.sales_person]["pdc_m3"]     += flt(row.pdc_m3)
            metrics_map[row.sales_person]["pdc_m4"]     += flt(row.pdc_m4)


    sp_keys = list(metrics_map.keys())
    if sp_keys:
        sp_details = frappe.db.sql("""
            SELECT
                name,
                sales_person_name,
                COALESCE(custom_territory_assigned, 'Unassigned') AS territory,
                COALESCE(custom_sales_target,       0)            AS custom_sales_target,
                COALESCE(custom_collections_target, 0)            AS custom_collections_target
            FROM `tabSales Person`
            WHERE name IN %s
        """, (tuple(sp_keys),), as_dict=True)

        for sp in sp_details:
            rep = metrics_map.get(sp.name)
            if not rep:
                continue
            rep["sales_person_name"]  = sp.sales_person_name or sp.name
            rep["territory"]          = sp.territory
            rep["sales_target"]       = flt(sp.custom_sales_target)
            rep["collection_target"]  = flt(sp.custom_collections_target)

    for sp_id, vals in metrics_map.items():
        vals["sales_pct"]     = (
            vals["actual_sales"] / vals["sales_target"] * 100.0
            if vals["sales_target"] else 0.0
        )
        vals["sales_deficit"] = vals["actual_sales"] - vals["sales_target"]

        vals["collection_pct"]     = (
            vals["actual_collections"] / vals["collection_target"] * 100.0
            if vals["collection_target"] else 0.0
        )
        vals["collection_deficit"] = vals["actual_collections"] - vals["collection_target"]

        vals["coll_sales_diff"] = vals["actual_collections"] - vals["actual_sales"]

    return {
        "is_custom":   is_custom,
        "active_reps": len(metrics_map),
        "dates": {
            "start":   period_start.strftime('%d-%b-%Y'),
            "end":     today.strftime('%d-%b-%Y'),
            "pdc_end": pdc_end_date.strftime('%d-%b-%Y'),
        },
        "metrics": list(metrics_map.values()),
    }