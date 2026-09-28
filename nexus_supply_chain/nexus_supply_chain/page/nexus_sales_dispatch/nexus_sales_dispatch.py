import frappe
import math
import requests
from frappe.utils import getdate, today, add_days, cint, flt

# Crystal API base URL for road-distance (ORS driving-car) route computation.
CRYSTAL_API_BASE_URL = "https://crystal-api.crystalapps.dev"

# 🚨 Shared secret for server-to-server-only Crystal API endpoints. Sourced
# from site_config.json ("crystal_api_internal_secret") — NEVER hardcoded
# here, since this file ships through the app's public/shared GitHub repo.
# If unset, this resolves to None and Crystal API rejects with 401 — fails
# closed, not open, so a forgotten setup step can't leave the endpoint exposed.
CRYSTAL_API_INTERNAL_SECRET = frappe.conf.get("crystal_api_internal_secret")


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


def _compute_distance_recorded(start_datetime, end_datetime, sales_person_filter_name=None):
    """
    Computes each rep's total ROAD distance traveled (km) for the selected
    attendance period, via ORS driving-car directions over each rep's own
    ordered check-in coordinates — computed PER CALENDAR DAY, then summed
    across the pulled range.

    Grouping by (rep, day) — not by rep alone across the whole range — is
    deliberate: chaining a rep's last stop on Monday straight into their
    first stop on Tuesday would invent a phantom overnight "leg" that never
    happened. Each day's checkpoints are sent to Crystal API as their own
    ordered route; the per-day distances are then summed per rep.
    """
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
    """
    Calls Crystal API's /telemetry/sales-route-distance with an ORDERED
    list of [lat, lng] checkpoints (check-in order — never re-sequenced).
    Returns (distance_km, geometry_or_None, source_str). On any failure
    (network or ORS-side), returns (0.0, None, "error") rather than
    silently substituting a haversine approximation — this distance
    feeds an accountability report, and an unmarked approximation is
    worse than a visible "unavailable" state.
    """
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
    """
    groups: list of {"key": str, "coordinates": [[lat, lng], ...]}
    One HTTP round trip covering every rep-day group in the pulled
    Attendance range. On failure (whole batch, or an individual group),
    those keys are simply absent from the returned map — callers treat a
    missing key as "distance unavailable," never as 0 km actually traveled.
    """
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
            # Only keep keys ORS actually resolved — drop any "error" entries
            # so they read as "unavailable" upstream, not as a real 0 km day.
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
            IFNULL(v.visit_type, 'Customer') AS visit_type,
            v.customer,
            v.lead,
            v.party_name,
            c.customer_name,
            l.lead_name,
            l.company_name AS lead_company_name,
            v.lead_status_before,
            v.lead_status_after,
            v.closed_by_conversion,
            v.check_in_time,
            v.check_out_time,
            v.latitude,
            v.longitude
        FROM `tabNexus Sales Visit` v
        LEFT JOIN `tabCustomer` c ON c.name = v.customer
        LEFT JOIN `tabLead` l ON l.name = v.lead
        WHERE v.sales_person = %(email)s
        AND DATE(v.check_in_time) = %(route_date)s
        AND v.latitude IS NOT NULL AND v.latitude != ''
        AND v.longitude IS NOT NULL AND v.longitude != ''
        ORDER BY v.check_in_time ASC
    """, {"email": sales_person_email, "route_date": route_date}, as_dict=True)

    if not visits:
        return {"status": "success", "checkpoints": [], "total_km": 0.0, "order_totals": {}}

    # 🚨 UPDATED: Road distance via Crystal API -> ORS driving-car directions,
    # over checkpoints IN CHECK-IN ORDER (never re-sequenced — this answers
    # "how far did the rep actually go between real stops," not a route
    # optimization question). Falls back to haversine if ORS is unreachable.
    ordered_coords = [[float(v.latitude), float(v.longitude)] for v in visits]
    if len(ordered_coords) >= 2:
        total_km, route_geometry, distance_source = _fetch_ors_route_distance(
            ordered_coords, include_geometry=True
        )
    else:
        total_km, route_geometry, distance_source = 0.0, None, "single_point"

    # 🚨 ALIGNED ATTRIBUTION MODEL: same DISTINCT-subquery pattern used by
    # get_customer_scoped_financial_totals for gross_invoiced/returns —
    # wrap each document set in "SELECT DISTINCT name, ..." before
    # aggregating, so a Sales Order or credit note that happens to carry
    # more than one matching Sales Team row (shared/house accounts) is
    # counted exactly once, never once-per-matching-row. This route view,
    # the dashboard, and get_sales_attendance now all share this identical
    # shape rather than three subtly different query patterns that could
    # silently drift apart over time.
    #
    # order_value is now NET of same-day returns (orders - returns) for
    # that checked-in customer, closing the gap where a return processed
    # the same day as a visit was previously invisible on the route view —
    # the JSON key stays `order_value` so the existing JS popup/summary
    # rendering picks this up with zero changes on that side.
    order_totals = {}
    return_totals = {}
    emp = frappe.db.get_value("Employee", {"user_id": sales_person_email}, "name")
    sp_name = frappe.db.get_value("Sales Person", {"employee": emp}, "name") if emp else None

    customer_names = list({v.customer for v in visits if v.customer and v.visit_type != "Lead"})
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
        is_lead = v.visit_type == "Lead"
        if is_lead:
            display_name = v.party_name or v.lead_name or v.lead_company_name or v.lead
        else:
            display_name = v.customer_name or v.party_name or v.customer

        checkpoints.append({
            "visit_id": v.name,
            "visit_type": v.visit_type,
            "customer": v.customer,
            "lead": v.lead,
            # customer_name kept as the display key so older page builds
            # still render a name for every stop, including lead stops.
            "customer_name": display_name,
            "party_name": display_name,
            "lead_status_before": v.lead_status_before if is_lead else None,
            "lead_status_after": v.lead_status_after if is_lead else None,
            "closed_by_conversion": cint(v.closed_by_conversion) if is_lead else 0,
            "check_in_time": str(v.check_in_time) if v.check_in_time else None,
            "check_out_time": str(v.check_out_time) if v.check_out_time else None,
            "lat": float(v.latitude),
            "lng": float(v.longitude),
            # Lead stops have no orders; only customer stops carry a value.
            "order_value": 0.0 if is_lead else order_totals.get(v.customer, 0.0)
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

            /* ── Visit counts ──
               On-Site / Off-Site are simple counts of the stored is_on_site
               field (set at check-in by compute_is_on_site, recomputed after
               every location correction, backfilled for history). Customer
               and Lead visits are counted identically, no party join needed. */
            COUNT(v.name)                                     AS total_visits,
            SUM(CASE WHEN IFNULL(v.visit_type, 'Customer') = 'Customer' THEN 1 ELSE 0 END) AS customer_visits,
            SUM(CASE WHEN v.visit_type = 'Lead' THEN 1 ELSE 0 END)                         AS lead_visits,
            SUM(CASE WHEN IFNULL(v.is_on_site, 0) = 1 THEN 1 ELSE 0 END)                   AS onsite_visits,
            SUM(CASE WHEN IFNULL(v.is_on_site, 0) = 1 THEN 0 ELSE 1 END)                   AS offsite_visits,

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

    # 🚨 DISTANCE RECORDED — merged in after the main aggregate query, using
    # the exact same haversine-over-consecutive-checkins logic the Route
    # Card uses, just computed across the whole pulled date range instead
    # of a single day.
    distance_map = _compute_distance_recorded(start_datetime, end_datetime, sales_person)
    for row in data:
        row["distance_recorded_km"] = distance_map.get(row.get("email"), 0.0)
        # 🚨 On-Site Ratio = this rep's on-site visits ÷ their total visits.
        # The totals row is computed from grand totals in the JS, never by
        # averaging these per-rep percentages.
        total = cint(row.get("total_visits"))
        row["onsite_ratio"] = round(cint(row.get("onsite_visits")) * 100.0 / total, 1) if total else 0.0

    return data

