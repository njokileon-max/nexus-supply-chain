import frappe
from frappe.utils import getdate, today, add_days

@frappe.whitelist()
def get_sales_team():
    """
    Fetches the baseline active sales team using the strict relational hierarchy:
    Sales Person ➔ Employee ➔ User.
    
    This provides the Dispatch dashboard with the full names and emails 
    so they can be tracked (shown as Offline) even before they send their first live ping.
    It guarantees that only properly configured, active field reps are loaded.
    """
    
    team_data = frappe.db.sql("""
        SELECT 
            usr.name as email, 
            usr.full_name
        FROM `tabSales Person` sp
        JOIN `tabEmployee` emp ON sp.employee = emp.name
        JOIN `tabUser` usr ON emp.user_id = usr.name
        WHERE 
            sp.enabled = 1 
            AND emp.status = 'Active' 
            AND usr.enabled = 1
            AND usr.user_type = 'System User'
        ORDER BY usr.full_name ASC
    """, as_dict=True)

    return team_data or []

@frappe.whitelist()
def get_sales_attendance(date_filter, start_date=None, end_date=None, sales_person=None):
    """
    Pulls visit attendance, total orders placed, order values, total invoices,
    and invoiced amount per sales rep for the selected period.
    """

    # 1. Date Context Resolution
    if date_filter == 'Today':
        start_d = today()
        end_d = today()
    elif date_filter == 'Yesterday':
        start_d = add_days(today(), -1)
        end_d = add_days(today(), -1)
    else:
        start_d = start_date
        end_d = end_date

    if not start_d or not end_d:
        frappe.throw("Start Date and End Date are required when using a Custom Range.")

    # 2. Precise time-box boundaries
    start_datetime = f"{start_d} 00:00:00"
    end_datetime   = f"{end_d} 23:59:59"

    filters = {
        "start":      start_datetime,
        "end":        end_datetime,
        "start_date": start_d,
        "end_date":   end_d,
    }

    # 3. Master query
    query = """
        SELECT
            v.sales_person                          AS email,
            sp.sales_person_name,
            MIN(v.check_in_time)                    AS first_visit,
            MAX(v.check_in_time)                    AS last_visit,
            COUNT(v.name)                           AS total_visits,
            COALESCE(ord.total_orders, 0)           AS total_orders,
            COALESCE(ord.total_order_value, 0)      AS total_order_value,
            COALESCE(inv.total_invoices, 0)         AS total_invoices,
            COALESCE(inv.invoiced_amount, 0)        AS invoiced_amount

        FROM `tabNexus Sales Visit` v

        LEFT JOIN `tabEmployee`    emp ON emp.user_id   = v.sales_person
        LEFT JOIN `tabSales Person` sp ON sp.employee   = emp.name

        /* ── Orders placed (Draft + Submitted, excludes Cancelled) ── */
        LEFT JOIN (
            SELECT
                st.sales_person,
                COUNT(DISTINCT so.name) AS total_orders,
                SUM(so.grand_total) AS total_order_value
            FROM `tabSales Order` so
            JOIN `tabSales Team`  st
                ON  st.parent     = so.name
                AND st.parenttype = 'Sales Order'
            WHERE
                so.transaction_date BETWEEN %(start_date)s AND %(end_date)s
                AND so.docstatus != 2
                AND so.status != 'Cancelled'
            GROUP BY st.sales_person
        ) ord ON ord.sales_person = sp.name

        /* ── Invoices generated (Strictly Submitted, docstatus = 1) ── */
        LEFT JOIN (
            SELECT
                st.sales_person,
                COUNT(DISTINCT si.name) AS total_invoices,
                SUM(si.grand_total) AS invoiced_amount
            FROM `tabSales Invoice` si
            JOIN `tabSales Team`    st
                ON  st.parent     = si.name
                AND st.parenttype = 'Sales Invoice'
            WHERE
                si.posting_date BETWEEN %(start_date)s AND %(end_date)s
                AND si.docstatus = 1
            GROUP BY st.sales_person
        ) inv ON inv.sales_person = sp.name

        WHERE
            v.check_in_time BETWEEN %(start)s AND %(end)s
    """

    # 4. Optional per-rep filter
    if sales_person:
        query += " AND sp.name = %(sales_person)s"
        filters["sales_person"] = sales_person

    query += " GROUP BY v.sales_person ORDER BY total_visits DESC"

    return frappe.db.sql(query, filters, as_dict=True)