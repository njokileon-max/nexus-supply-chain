import frappe
import math
import requests
from frappe.utils import getdate, today, add_days

CRYSTAL_API_BASE_URL = "https://crystal-api.crystalapps.dev"

CRYSTAL_API_INTERNAL_SECRET = frappe.conf.get("crystal_api_internal_secret")


def _haversine_km(lat1, lon1, lat2, lon2):

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


def _compute_distance_recorded(start_datetime, end_datetime, sales_person_filter_name=None):

    params = {"start": start_datetime, "end": end_datetime}
    query = """
        SELECT v.sales_person AS email, v.latitude, v.longitude, v.check_in_time,
               DATE(v.check_in_time) AS visit_date
        FROM `tabNexus Sales Visit` v
        LEFT JOIN `tabEmployee` emp ON emp.user_id = v.sales_person
        LEFT JOIN `tabSales Person` sp ON sp.employee = emp.name
        WHERE v.check_in_time BETWEEN %(start)s AND %(end)s
        AND v.latitude IS NOT NULL AND v.latitude != ''
        AND v.longitude IS NOT NULL AND v.longitude != ''
    """
    if sales_person_filter_name:
        query += " AND sp.name = %(sales_person)s"
        params["sales_person"] = sales_person_filter_name

    query += " ORDER BY v.sales_person ASC, v.check_in_time ASC"

    rows = frappe.db.sql(query, params, as_dict=True)

    by_rep_day = {}
    for r in rows:
        try:
            lat, lng = float(r.latitude), float(r.longitude)
        except (TypeError, ValueError):
            continue
        key = f"{r.email}|{r.visit_date}"
        by_rep_day.setdefault(key, {"email": r.email, "coords": []})
        by_rep_day[key]["coords"].append([lat, lng])

    groups = [
        {"key": key, "coordinates": v["coords"]}
        for key, v in by_rep_day.items()
        if len(v["coords"]) >= 2   # single-checkpoint days = 0 km, skip the call
    ]

    day_distances = _fetch_ors_route_distance_batch(groups)

    distance_map = {}
    for key, v in by_rep_day.items():
        km = day_distances.get(key, 0.0)
        distance_map[v["email"]] = round(distance_map.get(v["email"], 0.0) + km, 2)

    return distance_map

def _fetch_ors_route_distance(coordinates, include_geometry=False):

    try:
        resp = requests.post(
            f"{CRYSTAL_API_BASE_URL}/telemetry/sales-route-distance",
            json={"coordinates": coordinates, "include_geometry": include_geometry},
            headers={"x-internal-secret": CRYSTAL_API_INTERNAL_SECRET or ""},
            timeout=20
        )
        resp.raise_for_status()
        data = resp.json()
        if data.get("status") == "success":
            return (
                float(data.get("distance_km") or 0.0),
                data.get("geometry"),
                data.get("source", "ors")
            )
    except Exception as e:
        frappe.log_error(f"Crystal API route-distance call failed: {e}", "Nexus Sales Route Distance")

    return 0.0, None, "error"

def _fetch_ors_route_distance_batch(groups):

    if not groups:
        return {}

    try:
        resp = requests.post(
            f"{CRYSTAL_API_BASE_URL}/telemetry/sales-route-distance-batch",
            json={"groups": groups},
            headers={"x-internal-secret": CRYSTAL_API_INTERNAL_SECRET or ""},
            timeout=30
        )
        resp.raise_for_status()
        data = resp.json()
        if data.get("status") == "success":
            sources = data.get("sources") or {}

            return {
                k: float(v or 0.0)
                for k, v in (data.get("distances") or {}).items()
                if sources.get(k) == "ors"
            }
    except Exception as e:
        frappe.log_error(f"Crystal API batch route-distance call failed: {e}", "Nexus Sales Route Distance Batch")

    return {}

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

    ordered_coords = [[float(v.latitude), float(v.longitude)] for v in visits]
    if len(ordered_coords) >= 2:
        total_km, route_geometry, distance_source = _fetch_ors_route_distance(
            ordered_coords, include_geometry=True
        )
    else:
        total_km, route_geometry, distance_source = 0.0, None, "single_point"

    order_totals = {}
    return_totals = {}
    emp = frappe.db.get_value("Employee", {"user_id": sales_person_email}, "name")
    sp_name = frappe.db.get_value("Sales Person", {"employee": emp}, "name") if emp else None

    customer_names = list({v.customer for v in visits if v.customer})
    if sp_name and customer_names:
        format_custs = ','.join(['%s'] * len(customer_names))

        order_rows = frappe.db.sql(f"""
            SELECT customer, SUM(grand_total) as total FROM (
                SELECT DISTINCT so.name, so.customer, so.grand_total
                FROM `tabSales Order` so
                INNER JOIN `tabSales Team` st
                    ON st.parent = so.name AND st.parenttype = 'Sales Order'
                WHERE so.docstatus != 2
                AND so.transaction_date = %s
                AND st.sales_person = %s
                AND so.customer IN ({format_custs})
            ) distinct_orders
            GROUP BY customer
        """, tuple([route_date, sp_name] + customer_names), as_dict=True)
        order_totals = {r.customer: float(r.total or 0.0) for r in order_rows}

        return_rows = frappe.db.sql(f"""
            SELECT customer, SUM(grand_total) as total FROM (
                SELECT DISTINCT si.name, si.customer, si.grand_total
                FROM `tabSales Invoice` si
                INNER JOIN `tabSales Team` st
                    ON st.parent = si.name AND st.parenttype = 'Sales Invoice'
                WHERE si.docstatus = 1 AND si.is_return = 1
                AND si.posting_date = %s
                AND st.sales_person = %s
                AND si.customer IN ({format_custs})
            ) distinct_returns
            GROUP BY customer
        """, tuple([route_date, sp_name] + customer_names), as_dict=True)
        return_totals = {r.customer: abs(float(r.total or 0.0)) for r in return_rows}

        for cust in set(order_totals.keys()) | set(return_totals.keys()):
            gross = order_totals.get(cust, 0.0)
            returned = return_totals.get(cust, 0.0)
            order_totals[cust] = max(0.0, gross - returned)

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
        "order_totals": order_totals,
        "route_geometry": route_geometry,
        "distance_source": distance_source
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
            /* 🚨 NULL is explicitly bucketed as Off-Site here (never
               silently excluded from both counts). NULL means the
               customer had no resolvable target coordinates at check-in —
               that is, by definition, not verifiably on-site. */
            SUM(CASE
                    WHEN v.distance_from_target_meters IS NULL
                         OR v.distance_from_target_meters > 100
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

    data = frappe.db.sql(query, filters, as_dict=True)

    distance_map = _compute_distance_recorded(start_datetime, end_datetime, sales_person)
    for row in data:
        row["distance_recorded_km"] = distance_map.get(row.get("email"), 0.0)

    return data

