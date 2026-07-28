import frappe
import math
from frappe.utils import getdate, today, add_days


def _haversine_km(lat1, lon1, lat2, lon2):
    """
    Standard haversine distance in kilometers between two lat/lng points.
    Used only for the rep's own consecutive check-in-to-check-in legs (their
    own GPS trail recorded at check-in time) — deliberately NOT computed
    against raw live-ping data, since pings are ephemeral/in-RAM and pruned,
    while Nexus Sales Visit rows are the durable, already-indexed record of
    where the rep actually stood when they checked in.
    """
    if lat1 is None or lon1 is None or lat2 is None or lon2 is None:
        return 0.0
    try:
        lat1, lon1, lat2, lon2 = float(lat1), float(lon1), float(lat2), float(lon2)
    except (TypeError, ValueError):
        return 0.0

    R = 6371.0
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (math.sin(dlat / 2) ** 2
         + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon / 2) ** 2)
    return R * (2 * math.atan2(math.sqrt(a), math.sqrt(1 - a)))

@frappe.whitelist()
def get_sales_team():

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
def get_sales_person_route(sales_person_email, route_date):
    """
    Reconstructs a rep's route for a given date as the ORDERED SEQUENCE of
    their own Nexus Sales Visit check-in coordinates (visit-time order, not
    a live-ping trail) — the same durable, already-indexed dataset the
    Attendance report reads from. Total distance is the sum of consecutive
    checkpoint-to-checkpoint haversine legs, which is deliberately simpler
    and cheaper than reconstructing a path from raw GPS pings: it needs no
    new persistence layer, and "how far did the rep travel between the
    places they actually checked into" is exactly what a route view for
    accountability purposes needs.

    Order value per checkpoint uses the same Sales Team attribution as
    get_sales_attendance (direct sales_person match on the Sales Person
    record resolved from the rep's own Employee/User), scoped to Sales
    Orders placed on that same date for that checked-in customer.
    """
    if not sales_person_email or not route_date:
        frappe.throw("sales_person_email and route_date are required.")

    visits = frappe.db.sql("""
        SELECT
            v.name,
            v.customer,
            c.customer_name,
            v.check_in_time,
            v.check_out_time,
            v.latitude,
            v.longitude
        FROM `tabNexus Sales Visit` v
        LEFT JOIN `tabCustomer` c ON c.name = v.customer
        WHERE v.sales_person = %(email)s
        AND DATE(v.check_in_time) = %(route_date)s
        AND v.latitude IS NOT NULL AND v.latitude != ''
        AND v.longitude IS NOT NULL AND v.longitude != ''
        ORDER BY v.check_in_time ASC
    """, {"email": sales_person_email, "route_date": route_date}, as_dict=True)

    if not visits:
        return {"status": "success", "checkpoints": [], "total_km": 0.0, "order_totals": {}}

    # Total distance: sum of consecutive-checkpoint haversine legs, in visit order
    total_km = 0.0
    for i in range(1, len(visits)):
        prev, curr = visits[i - 1], visits[i]
        total_km += _haversine_km(prev.latitude, prev.longitude, curr.latitude, curr.longitude)

    # Order value per checked-in customer that same date — same Sales Team
    # attribution pattern as get_sales_attendance's `ord` subquery.
    order_totals = {}
    emp = frappe.db.get_value("Employee", {"user_id": sales_person_email}, "name")
    sp_name = frappe.db.get_value("Sales Person", {"employee": emp}, "name") if emp else None

    customer_names = list({v.customer for v in visits if v.customer})
    if sp_name and customer_names:
        format_custs = ','.join(['%s'] * len(customer_names))
        rows = frappe.db.sql(f"""
            SELECT so.customer, SUM(so.grand_total) as total
            FROM `tabSales Order` so
            JOIN `tabSales Team` st ON st.parent = so.name AND st.parenttype = 'Sales Order'
            WHERE so.docstatus != 2
            AND so.transaction_date = %s
            AND st.sales_person = %s
            AND so.customer IN ({format_custs})
            GROUP BY so.customer
        """, tuple([route_date, sp_name] + customer_names), as_dict=True)
        order_totals = {r.customer: float(r.total or 0.0) for r in rows}

    checkpoints = []
    for v in visits:
        checkpoints.append({
            "visit_id": v.name,
            "customer": v.customer,
            "customer_name": v.customer_name or v.customer,
            "check_in_time": str(v.check_in_time) if v.check_in_time else None,
            "check_out_time": str(v.check_out_time) if v.check_out_time else None,
            "lat": float(v.latitude),
            "lng": float(v.longitude),
            "order_value": order_totals.get(v.customer, 0.0)
        })

    return {
        "status": "success",
        "checkpoints": checkpoints,
        "total_km": round(total_km, 2),
        "order_totals": order_totals
    }

@frappe.whitelist()
def get_sales_attendance(date_filter, start_date=None, end_date=None, sales_person=None):

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

    start_datetime = f"{start_d} 00:00:00"
    end_datetime   = f"{end_d} 23:59:59"

    filters = {
        "start":      start_datetime,
        "end":        end_datetime,
        "start_date": start_d,
        "end_date":   end_d,
    }

    query = """
        SELECT
            v.sales_person                                   AS email,
            sp.sales_person_name,

            /* ── Date range being pulled ── */
            %(start_date)s                                    AS period_start_date,
            %(end_date)s                                      AS period_end_date,

            /* ── First / last check-in (full timestamp, time extracted on display) ── */
            MIN(v.check_in_time)                              AS first_visit,
            MAX(v.check_in_time)                              AS last_visit,
            TIME(MIN(v.check_in_time))                        AS first_visit_time,
            TIME(MAX(v.check_in_time))                        AS last_visit_time,

            /* ── Visit counts ── */
            COUNT(v.name)                                     AS total_visits,
            SUM(CASE
                    WHEN v.distance_from_target_meters IS NOT NULL
                         AND v.distance_from_target_meters <= 100
                    THEN 1 ELSE 0
                END)                                           AS onsite_visits,
            SUM(CASE
                    WHEN v.distance_from_target_meters IS NOT NULL
                         AND v.distance_from_target_meters > 100
                    THEN 1 ELSE 0
                END)                                           AS offsite_visits,

            /* ── Orders: Draft + Submitted (excludes Cancelled) ── */
            COALESCE(ord.total_orders, 0)                     AS total_orders,
            COALESCE(ord.total_order_value, 0)                AS total_order_value,

            /* ── Orders: Confirmed only (Submitted, docstatus = 1) ── */
            COALESCE(ord_confirmed.total_confirmed_orders, 0) AS total_confirmed_orders,
            COALESCE(ord_confirmed.total_confirmed_value, 0)  AS total_confirmed_value,

            /* ── Invoices: strictly invoiced, no returns ── */
            COALESCE(inv.total_invoices, 0)                   AS total_invoices,
            COALESCE(inv.invoiced_amount, 0)                  AS invoiced_amount,

            /* ── Returns: strictly submitted returns ── */
            COALESCE(ret.total_returns, 0)                    AS total_returns,
            COALESCE(ret.returned_amount, 0)                  AS returned_amount

        FROM `tabNexus Sales Visit` v

        LEFT JOIN `tabEmployee`     emp ON emp.user_id  = v.sales_person
        LEFT JOIN `tabSales Person`  sp ON sp.employee  = emp.name

        /* ── Orders placed: Draft + Submitted, excludes Cancelled ── */
        LEFT JOIN (
            SELECT
                st.sales_person,
                COUNT(DISTINCT so.name)  AS total_orders,
                SUM(so.grand_total)      AS total_order_value
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

        /* ── Orders confirmed: strictly Submitted (docstatus = 1) ── */
        LEFT JOIN (
            SELECT
                st.sales_person,
                COUNT(DISTINCT so.name)  AS total_confirmed_orders,
                SUM(so.grand_total)      AS total_confirmed_value
            FROM `tabSales Order` so
            JOIN `tabSales Team`  st
                ON  st.parent     = so.name
                AND st.parenttype = 'Sales Order'
            WHERE
                so.transaction_date BETWEEN %(start_date)s AND %(end_date)s
                AND so.docstatus = 1
            GROUP BY st.sales_person
        ) ord_confirmed ON ord_confirmed.sales_person = sp.name

        /* ── Invoices generated: Submitted, strictly excludes returns ── */
        LEFT JOIN (
            SELECT
                st.sales_person,
                COUNT(DISTINCT si.name) AS total_invoices,
                SUM(si.grand_total)     AS invoiced_amount
            FROM `tabSales Invoice` si
            JOIN `tabSales Team`    st
                ON  st.parent     = si.name
                AND st.parenttype = 'Sales Invoice'
            WHERE
                si.posting_date BETWEEN %(start_date)s AND %(end_date)s
                AND si.docstatus = 1
                AND si.is_return = 0
            GROUP BY st.sales_person
        ) inv ON inv.sales_person = sp.name

        /* ── Returns: Submitted, strictly return invoices ── */
        LEFT JOIN (
            SELECT
                st.sales_person,
                COUNT(DISTINCT si.name) AS total_returns,
                SUM(si.grand_total)     AS returned_amount
            FROM `tabSales Invoice` si
            JOIN `tabSales Team`    st
                ON  st.parent     = si.name
                AND st.parenttype = 'Sales Invoice'
            WHERE
                si.posting_date BETWEEN %(start_date)s AND %(end_date)s
                AND si.docstatus = 1
                AND si.is_return = 1
            GROUP BY st.sales_person
        ) ret ON ret.sales_person = sp.name

        WHERE
            v.check_in_time BETWEEN %(start)s AND %(end)s
    """

    if sales_person:
        query += " AND sp.name = %(sales_person)s"
        filters["sales_person"] = sales_person

    query += " GROUP BY v.sales_person ORDER BY total_visits DESC"

    return frappe.db.sql(query, filters, as_dict=True)
