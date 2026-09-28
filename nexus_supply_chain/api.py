# apps/nexus_supply_chain/nexus_supply_chain/api.py

import frappe
import requests
import json
import math
from datetime import datetime
from html import escape as _html_escape
from frappe.utils import today, add_days, add_months, get_first_day, get_last_day, get_datetime, flt, getdate, cint

def parse_combined_coords(combined, fallback_lat=None, fallback_lng=None):
    """
    Python mirror of App.tsx's parseCombinedCoords. custom_combined_coordinates
    (a Data field storing "lat,lng" as an exact string) is the primary source;
    custom_latitude/custom_longitude (legacy Float fields, lower precision)
    are the fallback for pre-combined records. Returns (lat, lng) or None.
    """
    if combined:
        try:
            combined_str = str(combined).strip()
            if ',' in combined_str:
                parts = combined_str.split(',')
                if len(parts) >= 2:
                    lat = float(parts[0].strip())
                    lng = float(parts[1].strip())
                    if -90.0 <= lat <= 90.0 and -180.0 <= lng <= 180.0 and not (lat == 0.0 and lng == 0.0):
                        return (lat, lng)
        except (ValueError, TypeError):
            pass

    if fallback_lat not in (None, '') and fallback_lng not in (None, ''):
        try:
            lat = float(fallback_lat)
            lng = float(fallback_lng)
            if -90.0 <= lat <= 90.0 and -180.0 <= lng <= 180.0 and not (lat == 0.0 and lng == 0.0):
                return (lat, lng)
        except (ValueError, TypeError):
            pass

    return None

# ============================================================================
# 🚨 VISIT LOCATION CORRECTION ENGINE (server-authoritative)
# Single source of truth for recomputing a visit's distance after the
# customer's location is corrected mid-visit. Used by
# update_customer_coordinates (the app's path), the hardened
# register_sales_check_in_correction, and the one-off backfill. The distance
# is ALWAYS recomputed here from the check-in point stored on the visit
# (latitude/longitude written by register_sales_check_in) against the
# customer's new coordinates. A client-supplied distance is never trusted.
# ============================================================================
NEXUS_ON_SITE_THRESHOLD_METERS = 100.0
NEXUS_LOCATION_SOURCES = ("GPS Snap", "Maps Link")
NEXUS_GPS_SNAP_LINK_LABEL = "Generated from Sales Native GPS"

# 🚨 VISIT PARTY MODEL — which Nexus Sales Visit link field holds the visited
# party for each visit_type. Every visit helper reads this map instead of
# branching on customer/lead, so a future visit type is one entry here.
NEXUS_VISIT_PARTY_FIELDS = {"Customer": "customer", "Lead": "lead"}
NEXUS_LEAD_CONVERTED_STATUS = "Converted"


def haversine_meters(lat1, lng1, lat2, lng2):
    """
    Great-circle distance in meters. Same earth radius and formula as
    register_sales_check_in and App.tsx's getDistance, so every distance in
    the system is computed identically.
    """
    lat1, lng1, lat2, lng2 = float(lat1), float(lng1), float(lat2), float(lng2)
    dlat = math.radians(lat2 - lat1)
    dlng = math.radians(lng2 - lng1)
    a = (math.sin(dlat / 2) ** 2
         + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlng / 2) ** 2)
    return 6371000.0 * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))

def compute_is_on_site(target_coordinates, distance):
    """
    THE on-site rule. Used by the Nexus Sales Visit controller, the location
    correction engine and the patch — never duplicated elsewhere.
    On-site = the target had valid coordinates AND distance <= threshold.
    """
    if not parse_combined_coords(target_coordinates):
        return 0
    if distance is None or distance == "":
        return 0
    return 1 if flt(distance) <= NEXUS_ON_SITE_THRESHOLD_METERS else 0


def _get_party_target_coords(party_type, party):
    """
    The pin a visit's distance is measured against, identical for Customer
    and Lead: custom_combined_coordinates first (exact string), then
    custom_latitude/custom_longitude. Columns are checked first, so a doctype
    missing one of the GeoLocation custom fields degrades to "no coordinates"
    (visit recorded Off-Site) instead of crashing the check-in.
    """
    if not party or party_type not in NEXUS_VISIT_PARTY_FIELDS:
        return None
    fields = [f for f in ("custom_combined_coordinates", "custom_latitude", "custom_longitude")
              if frappe.db.has_column(party_type, f)]
    if not fields:
        return None
    row = frappe.db.get_value(party_type, party, fields, as_dict=True) or {}
    return parse_combined_coords(
        row.get("custom_combined_coordinates"),
        row.get("custom_latitude"),
        row.get("custom_longitude"),
    )


def get_lead_status_options(include_converted=False):
    """
    Lead statuses read live from the Lead doctype definition, so options added
    or renamed in Customize Form reach the app with no code change.
    "Converted" is excluded by default: ERPNext sets it itself when the office
    creates a Customer from the Lead, and the app must never set it.
    """
    field = frappe.get_meta("Lead").get_field("status")
    options = [o.strip() for o in (field.options or "").split("\n") if o.strip()] if field else []
    if not include_converted:
        options = [o for o in options if o != NEXUS_LEAD_CONVERTED_STATUS]
    return options


def _lead_access_error(lead_owner, session_user):
    """
    Server-side scope check for lead actions. Allowed when the Lead Owner is
    the caller, or anyone inside the caller's Sales Person hierarchy (so a
    manager can act on a subordinate's lead). Returns None when allowed,
    otherwise a human-readable reason.
    """
    owner = (lead_owner or "").strip().lower()
    me = (session_user or "").strip().lower()
    if not owner:
        return "This lead has no Lead Owner, so it can't be visited from the app. Ask the office to assign it."
    if owner == me:
        return None
    if owner in get_authorized_sales_emails(session_user):
        return None
    return "This lead is assigned to another sales rep outside your team."


def _get_open_visits_today(session_user):
    """
    The caller's still-open visits that were checked in TODAY. Visits left
    open on earlier days (app killed, no check-out) are deliberately ignored
    so a stale record can never lock a rep out of checking in.
    """
    return frappe.db.sql("""
        SELECT name, visit_type, customer, lead, party_name, check_in_time
        FROM `tabNexus Sales Visit`
        WHERE sales_person = %s
        AND (check_out_time IS NULL OR check_out_time = '')
        AND DATE(check_in_time) = %s
        ORDER BY creation DESC
    """, (session_user, today()), as_dict=True)



# 🚨 LEAD SYNC — the Lead fields the app uses. Also the list of fields whose
# change triggers an app refresh (trigger_lead_refresh), so the two can
# never drift apart.
NEXUS_LEAD_SYNC_FIELDS = (
    "lead_name", "company_name", "status", "mobile_no", "phone", "whatsapp_no",
    "email_id", "territory", "source", "lead_owner",
    "custom_google_maps_link", "custom_latitude", "custom_longitude", "custom_combined_coordinates",
)


def get_scoped_leads(target_email):
    """
    Leads visible to target_email: Lead Owner is target_email or anyone in
    their Sales Person hierarchy (same Shape A scope as customers), status
    not Converted. Each row carries owning_sales_person_name so a manager's
    Leads list can show the same rep label the Customer list shows.
    Columns are checked first, so a missing optional/custom Lead field is
    simply omitted instead of breaking the sync.
    """
    owner_emails = {e.strip().lower() for e in get_authorized_sales_emails(target_email) if e}
    if target_email:
        owner_emails.add(target_email.strip().lower())
    if not owner_emails:
        return []

    owners = tuple(owner_emails)
    format_owners = ','.join(['%s'] * len(owners))

    columns = ["l.name"] + [f"l.`{f}`" for f in NEXUS_LEAD_SYNC_FIELDS if frappe.db.has_column("Lead", f)]
    last_visit_select = (
        "(SELECT MAX(v.check_in_time) FROM `tabNexus Sales Visit` v WHERE v.lead = l.name) AS last_visited_at"
        if frappe.db.has_column("Nexus Sales Visit", "lead") else "NULL AS last_visited_at"
    )

    rows = frappe.db.sql(f"""
        SELECT {', '.join(columns)},
               l.creation AS lead_creation_date,
               {last_visit_select},
               u.full_name AS lead_owner_full_name
        FROM `tabLead` l
        LEFT JOIN `tabUser` u ON u.name = l.lead_owner
        WHERE LOWER(l.lead_owner) IN ({format_owners})
        AND IFNULL(l.status, '') != %s
        ORDER BY l.modified DESC
    """, owners + (NEXUS_LEAD_CONVERTED_STATUS,), as_dict=True)

    if not rows:
        return []

    # Owner email -> Sales Person name (both Employee-linked and the legacy
    # "email stored directly in Sales Person.employee" pattern used elsewhere).
    sp_name_map = {}
    for r in frappe.db.sql(f"""
        SELECT LOWER(e.user_id) AS email, MIN(sp.sales_person_name) AS sp_name
        FROM `tabSales Person` sp
        JOIN `tabEmployee` e ON sp.employee = e.name
        WHERE LOWER(e.user_id) IN ({format_owners})
        GROUP BY LOWER(e.user_id)
    """, owners, as_dict=True):
        sp_name_map[r.email] = r.sp_name
    for r in frappe.db.sql(f"""
        SELECT LOWER(employee) AS email, MIN(sales_person_name) AS sp_name
        FROM `tabSales Person`
        WHERE LOWER(employee) IN ({format_owners})
        GROUP BY LOWER(employee)
    """, owners, as_dict=True):
        sp_name_map.setdefault(r.email, r.sp_name)

    for row in rows:
        owner = (row.get("lead_owner") or "").strip().lower()
        row["lead_id"] = row["name"]
        row["owning_sales_person_name"] = (
            sp_name_map.get(owner) or row.get("lead_owner_full_name") or row.get("lead_owner")
        )
        row.pop("lead_owner_full_name", None)

    return rows


def _create_sales_visit(party_type, party, lat, lng, target_coords, extra_fields=None):
    """
    THE check-in routine for every visit type. Records the check-in point,
    the distance to the target pin, the pin itself (target_coordinates) and
    the permanent original distance. is_on_site is then set by the Nexus
    Sales Visit controller on insert, via compute_is_on_site. Nothing here
    decides on-site status itself.

    A missing target pin or an unusable check-in point both leave the
    distance empty, which the controller records as Off-Site.
    """
    distance = None
    checkin_point = parse_combined_coords(None, lat, lng)
    if target_coords and checkin_point:
        distance = round(haversine_meters(
            checkin_point[0], checkin_point[1], target_coords[0], target_coords[1]
        ), 2)

    doc = frappe.new_doc("Nexus Sales Visit")
    doc.sales_person = frappe.session.user
    doc.visit_type = party_type
    doc.set(NEXUS_VISIT_PARTY_FIELDS[party_type], party)
    doc.check_in_time = frappe.utils.now_datetime()
    doc.latitude = str(lat)
    doc.longitude = str(lng)
    doc.distance_from_target_meters = distance
    if target_coords:
        doc.target_coordinates = f"{target_coords[0]},{target_coords[1]}"
    # 🚨 AUDIT: the check-in distance is also kept as the permanent
    # "original" value. A later location correction recomputes
    # distance_from_target_meters but never touches this field.
    if distance is not None and frappe.db.has_column("Nexus Sales Visit", "original_distance_from_target_meters"):
        doc.original_distance_from_target_meters = distance
    for fieldname, value in (extra_fields or {}).items():
        doc.set(fieldname, value)

    doc.insert(ignore_permissions=True)
    return doc


def _get_visit_for_correction(visit_name):
    """
    Loads the fields needed to evaluate/apply a correction, row-locked
    (for_update) so two concurrent corrections on the same visit serialize
    instead of racing on the original-distance capture. Newer fields are
    only selected if they exist, so this never breaks on a pre-migrate site.
    """
    fields = ["name", "sales_person", "customer", "latitude", "longitude",
              "check_out_time", "distance_from_target_meters"]
    for optional in ("visit_type", "lead", "original_distance_from_target_meters", "location_corrected"):
        if frappe.db.has_column("Nexus Sales Visit", optional):
            fields.append(optional)
    return frappe.db.get_value("Nexus Sales Visit", visit_name, fields, as_dict=True, for_update=True)


def _resolve_open_visit_for_correction(party_type, party, session_user, visit_id=None):
    """
    Finds the visit a location correction applies to, for a Customer or a
    Lead. Returns (visit_row, None) on success or (None, reason) otherwise.

    Resolution order:
      1. visit_id, if supplied and it exists.
      2. Otherwise the CALLER'S most recent still-open visit for this party
         (covers the window where the app hasn't received visit_id yet).

    Guards: the visit must belong to the calling session, be for this exact
    party, and still be open. A closed visit's distance is a settled record.
    """
    party_field = NEXUS_VISIT_PARTY_FIELDS.get(party_type)
    if not party_field:
        return None, "Unsupported visit type, so no visit distance was changed."

    label = party_type.lower()
    session_norm = (session_user or "").strip().lower()
    visit = None

    if visit_id and frappe.db.exists("Nexus Sales Visit", visit_id):
        visit = _get_visit_for_correction(visit_id)
    else:
        # party_field comes from NEXUS_VISIT_PARTY_FIELDS (never user input),
        # so interpolating the column name is safe.
        rows = frappe.db.sql(f"""
            SELECT name FROM `tabNexus Sales Visit`
            WHERE sales_person = %s AND `{party_field}` = %s
            AND (check_out_time IS NULL OR check_out_time = '')
            ORDER BY creation DESC LIMIT 1
        """, (session_user, party), as_dict=True)
        if rows:
            visit = _get_visit_for_correction(rows[0].name)

    if not visit:
        return None, f"No open visit was found for this {label}, so no visit distance was changed."
    if (visit.sales_person or "").strip().lower() != session_norm:
        return None, "This visit belongs to another sales rep, so its distance was not changed."
    if visit.get(party_field) != party:
        return None, f"This visit is for a different {label}, so its distance was not changed."
    if visit.check_out_time:
        return None, "This visit is already checked out, so its distance was not changed."
    return visit, None


def _apply_visit_location_correction(visit, target_coords, location_source=None):
    """
    Recomputes the visit's distance from its stored check-in point to
    target_coords and writes it together with the audit trail:
      - target_coordinates / is_on_site (via compute_is_on_site)
      - original_distance_from_target_meters: the pre-correction distance,
        captured ONLY on the first correction so repeated corrections can
        never erase the true check-in value.
      - location_corrected / location_corrected_at / location_correction_source.
    Works identically for Customer and Lead visits.
    Does NOT commit; the caller owns the transaction.
    """
    base = {
        "visit_updated": False,
        "visit_id": visit.name,
        "visit_distance_m": None,
        "is_on_site": None,
        "original_distance_m": None,
        "previous_distance_m": visit.get("distance_from_target_meters"),
        "visit_message": None,
    }

    checkin_point = parse_combined_coords(None, visit.get("latitude"), visit.get("longitude"))
    if not checkin_point:
        base["visit_message"] = "This visit has no valid check-in GPS point, so its distance could not be recomputed."
        return base
    if not target_coords:
        base["visit_message"] = "The target has no valid coordinates, so the visit distance could not be recomputed."
        return base

    new_distance = round(haversine_meters(checkin_point[0], checkin_point[1], target_coords[0], target_coords[1]), 2)
    previous_distance = visit.get("distance_from_target_meters")
    already_corrected = cint(visit.get("location_corrected"))
    target_str = f"{target_coords[0]},{target_coords[1]}"
    on_site = compute_is_on_site(target_str, new_distance)

    update = {"distance_from_target_meters": new_distance}
    if frappe.db.has_column("Nexus Sales Visit", "target_coordinates"):
        update["target_coordinates"] = target_str
    if frappe.db.has_column("Nexus Sales Visit", "is_on_site"):
        update["is_on_site"] = on_site

    original = visit.get("original_distance_from_target_meters")
    if (frappe.db.has_column("Nexus Sales Visit", "original_distance_from_target_meters")
            and not already_corrected and previous_distance is not None):
        update["original_distance_from_target_meters"] = flt(previous_distance)
        original = flt(previous_distance)

    if frappe.db.has_column("Nexus Sales Visit", "location_corrected"):
        update["location_corrected"] = 1
    if frappe.db.has_column("Nexus Sales Visit", "location_corrected_at"):
        update["location_corrected_at"] = frappe.utils.now_datetime()
    if location_source in NEXUS_LOCATION_SOURCES and frappe.db.has_column("Nexus Sales Visit", "location_correction_source"):
        update["location_correction_source"] = location_source

    frappe.db.set_value("Nexus Sales Visit", visit.name, update, update_modified=False)

    base.update({
        "visit_updated": True,
        "visit_distance_m": new_distance,
        "is_on_site": bool(on_site),
        "original_distance_m": flt(original) if original is not None else None,
    })
    return base

class NexusVersionOutdatedError(frappe.ValidationError):
    # Frappe maps exception classes to HTTP status via this attribute —
    # same mechanism frappe.PermissionError (403) / DoesNotExistError (404)
    # use internally. Setting it here means frappe.throw(exc=...) below
    # actually returns a real 426, not a generic 417/500.
    http_status_code = 426


def _version_tuple(v):
    """
    Converts a version string like '1.1.1' into (1, 1, 1) for comparison.
    Non-numeric or malformed segments fall back to 0 rather than raising,
    so a corrupted/odd version string never crashes the check — it just
    compares as lower than any real version.
    """
    if not v:
        return (0,)
    parts = []
    for p in str(v).strip().split('.'):
        try:
            parts.append(int(p))
        except (TypeError, ValueError):
            parts.append(0)
    return tuple(parts)


def enforce_minimum_app_version():
    """
    before_request hook. No longer exits early on Guest — the login call
    itself runs as Guest (the session hasn't authenticated yet), so gating
    login requires checking the header regardless of session state. Safe
    to do broadly: this only ever acts when X-App-Version is present, and
    only the Nexus Sales app ever sends that header, so ordinary guest
    traffic (public pages, anonymous API calls) is unaffected.
    """
    client_version = frappe.request.headers.get("X-App-Version")
    if not client_version:
        return

    try:
        settings = frappe.get_cached_doc("Nexus App Version")
        min_version = getattr(settings, "minimum_version", None)
    except Exception as e:
        frappe.log_error(title="Nexus Version Gate Lookup Failed", message=str(e))
        return

    if not min_version:
        return

    if _version_tuple(client_version) < _version_tuple(min_version):
        frappe.throw(
            msg="This version of Nexus Sales is no longer supported. Please update to continue.",
            exc=NexusVersionOutdatedError,
            title="Update Required"
        )

def queue_customer_geocoding(doc, method=None):

    if getattr(frappe.flags, "in_import", False):
        return

    link = doc.get("custom_google_maps_link")
    if not link:
        return

    is_new = doc.is_new()
    link_changed = doc.has_value_changed("custom_google_maps_link")

    try:
        lat = float(doc.custom_latitude or 0.0)
        lng = float(doc.custom_longitude or 0.0)
        missing_coords = (lat == 0.0 and lng == 0.0)
    except (TypeError, ValueError):
        missing_coords = True

    if not (is_new or link_changed or missing_coords):
        return

    frappe.enqueue(
        "nexus_supply_chain.api.execute_external_geocode_call",
        doc_name=doc.name,
        link=link,
        queue="short",
        timeout=300,
        enqueue_after_commit=True
    )

def execute_external_geocode_call(doc_name, link):

    try:
        fastapi_url = "https://crystal-api.crystalapps.dev/extract-coordinates"

        response = requests.post(fastapi_url, json={"url": link}, timeout=15)

        if response.status_code == 200:
            data = response.json()
            if data.get("status") == "success":
                lat = float(data.get("lat"))
                lng = float(data.get("lng"))
                combined = data.get("combined_coordinates")

                update_dict = {
                    "custom_latitude": lat,
                    "custom_longitude": lng
                }
                if combined:
                    update_dict["custom_combined_coordinates"] = combined

                frappe.db.set_value("Customer", doc_name, update_dict, update_modified=False)
                frappe.db.commit() # Essential in background jobs

                frappe.publish_realtime('doc_update', message={'doctype': 'Customer', 'name': doc_name})

                frappe.logger().info(f"[Nexus Geocode] {doc_name} synced successfully via background worker.")
            else:
                frappe.log_error(title="FastAPI Geocode Failed", message=data.get("message"))
        else:
            frappe.log_error(title="FastAPI Unreachable", message=f"Status: {response.status_code}")

    except Exception as e:
        frappe.log_error(message=str(e), title="Frappe Background Geocode Error")

def process_bulk_geocoding_queue():
    """
    Scheduled cron job (runs every 10 minutes).
    Finds up to 20 customers who have a Google Maps link but no coordinates.
    Processes them one by one with a random sleep to evade bot detection.
    """
    import time
    import random

    targets = frappe.db.sql("""
        SELECT name, custom_google_maps_link
        FROM `tabCustomer`
        WHERE custom_google_maps_link IS NOT NULL
        AND custom_google_maps_link != ''
        AND (custom_latitude = 0.0 OR custom_latitude IS NULL OR custom_latitude = '')
        LIMIT 20
    """, as_dict=True)

    if not targets:
        return

    frappe.logger().info(f"[Nexus Geocode] Slow-Drip Batcher starting for {len(targets)} customers.")

    fastapi_url = "https://crystal-api.crystalapps.dev/extract-coordinates"
    successful_updates = 0

    for target in targets:
        doc_name = target.name
        link = target.custom_google_maps_link

        try:
            response = requests.post(fastapi_url, json={"url": link}, timeout=15)

            if response.status_code == 200:
                data = response.json()
                if data.get("status") == "success":
                    lat = float(data.get("lat"))
                    lng = float(data.get("lng"))
                    combined = data.get("combined_coordinates")

                    update_dict = {
                        "custom_latitude": lat,
                        "custom_longitude": lng
                    }
                    if combined:
                        update_dict["custom_combined_coordinates"] = combined

                    frappe.db.set_value("Customer", doc_name, update_dict, update_modified=False)
                    frappe.db.commit()
                    successful_updates += 1

        except Exception as e:
            frappe.log_error(message=str(e), title=f"Slow-Drip Geocode Error: {doc_name}")

        time.sleep(random.uniform(4.0, 7.0))

    if successful_updates > 0:
        frappe.cache().set_value('nexus_needs_sync', True)
        frappe.logger().info(f"[Nexus Geocode] Slow-Drip Batcher finished. Synced {successful_updates} customers.")

@frappe.whitelist()
def check_mobile_app_access():
    """
    Strictly checks the user's native ERPNext Role Profile against the allowed
    roles in Nexus App Settings. No hardcoded admin bypasses.
    """
    if frappe.session.user == "Guest":
        frappe.local.response["http_status_code"] = 401
        return {"status": "denied", "message": "Please log in first."}

    try:
        settings = frappe.get_doc("Nexus App Settings")

        table_rows = settings.get("allowed_roles", [])
        allowed_roles = [str(row.role).strip() for row in table_rows if row.role]

    except Exception as e:
        return {"status": "denied", "message": f"Server Error: {str(e)}"}

    user_roles = frappe.get_roles(frappe.session.user)
    clean_user_roles = [str(r).strip() for r in user_roles]

    has_access = any(role in allowed_roles for role in clean_user_roles)

    if has_access:
        return {"status": "success", "message": "Access Granted"}
    else:
        debug_msg = f"Denied.\nUser has: {clean_user_roles}\nServer allows: {allowed_roles}"

        frappe.local.login_manager.logout()
        frappe.db.commit()

        frappe.local.response["http_status_code"] = 403
        return {"status": "denied", "message": debug_msg}

@frappe.whitelist(allow_guest=True)
def get_user_profile():
    """
    Called by the React Native app immediately after native login.
    Returns the user's details, roles, CSRF token, and the SID to authorize proxy requests.
    """
    if frappe.session.user == "Guest":
        frappe.local.response["http_status_code"] = 401
        return {"status": "failed", "message": "Unauthorized"}

    user_doc = frappe.get_doc("User", frappe.session.user)
    roles = frappe.get_roles(frappe.session.user)

    return {
        "status": "success",
        "message": {
            "full_name": user_doc.full_name,
            "email": user_doc.email,
            "roles": roles,
            "csrf_token": frappe.sessions.get_csrf_token(),
            "sid": frappe.session.sid
        }
    }

@frappe.whitelist()
def get_nexus_live_inventory():
    reservations = frappe.db.sql("""
        SELECT ri.item_code, ri.sales_order, SUM(ri.reserved_qty) as reserved_qty
        FROM `tabNexus Inventory Reservation Item` ri
        JOIN `tabNexus Inventory Reservation` r ON ri.parent = r.name
        WHERE r.reservation_status IN ('Active', 'Waiting for Stock') AND r.docstatus = 1
        GROUP BY ri.item_code, ri.sales_order
    """, as_dict=True)

    if not reservations:
        return []

    active_items = list(set([r['item_code'] for r in reservations if r.get('item_code')]))
    active_sos = list(set([r['sales_order'] for r in reservations if r.get('sales_order')]))

    if not active_items or not active_sos:
        return []

    format_items = ','.join(['%s'] * len(active_items))
    format_sos = ','.join(['%s'] * len(active_sos))

    sales_orders = frappe.db.sql(f"""
        SELECT so.name as sales_order, soi.item_code, soi.qty
        FROM `tabSales Order` so
        JOIN `tabSales Order Item` soi ON so.name = soi.parent
        WHERE so.status = 'To Deliver and Bill' AND so.docstatus = 1
        AND so.name IN ({format_sos})
        AND soi.item_code IN ({format_items})
    """, tuple(active_sos + active_items), as_dict=True)

    items = frappe.db.sql(f"""
        SELECT name as item_code, item_name
        FROM `tabItem`
        WHERE name IN ({format_items})
    """, tuple(active_items), as_dict=True)

    stock = frappe.db.sql(f"""
        SELECT item_code, SUM(actual_qty) as actual_qty
        FROM `tabBin`
        WHERE warehouse = 'Finished Goods - CAL' AND item_code IN ({format_items})
        GROUP BY item_code
    """, tuple(active_items), as_dict=True)

    payload = {
        "items": items,
        "stock": stock,
        "sales_orders": sales_orders,
        "reservations": reservations
    }

    fastapi_url = "https://crystal-api.crystalapps.dev/api/v1/live-inventory"

    try:
        response = requests.post(fastapi_url, json=payload, timeout=15)
        response.raise_for_status()
        return response.json().get("data", [])
    except Exception as e:
        frappe.log_error(message=str(e), title="Nexus Live Inventory Sync Failed")
        return []


@frappe.whitelist()
def get_nexus_production_data():
    sales_orders = frappe.db.sql("""
        SELECT so.name as sales_order, soi.item_code, soi.qty
        FROM `tabSales Order` so
        JOIN `tabSales Order Item` soi ON so.name = soi.parent
        WHERE so.status = 'To Deliver and Bill' AND so.docstatus = 1
    """, as_dict=True)

    reservations = frappe.db.sql("""
        SELECT ri.item_code, ri.sales_order, SUM(ri.reserved_qty) as reserved_qty
        FROM `tabNexus Inventory Reservation Item` ri
        JOIN `tabNexus Inventory Reservation` r ON ri.parent = r.name
        WHERE r.reservation_status IN ('Active', 'Waiting for Stock') AND r.docstatus = 1
        GROUP BY ri.item_code, ri.sales_order
    """, as_dict=True)

    mrl_breaches = frappe.db.sql("""
        SELECT i.name as item_code
        FROM `tabItem` i
        LEFT JOIN `tabBin` b ON i.name = b.item_code AND b.warehouse = 'Finished Goods - CAL'
        WHERE i.custom_linked_bip IS NOT NULL
        AND i.custom_minimum_reorder_level > 0
        AND IFNULL(b.actual_qty, 0) <= i.custom_minimum_reorder_level
    """, as_dict=True)

    active_items = list(set(
        [so['item_code'] for so in sales_orders] +
        [r['item_code'] for r in reservations] +
        [m['item_code'] for m in mrl_breaches]
    ))

    if not active_items: return []

    format_items = ','.join(['%s'] * len(active_items))
    tuple_items = tuple(active_items)

    fgs = frappe.db.sql(f"""
        SELECT
            i.name as item_code,
            i.item_name,
            i.custom_linked_bip,
            i.custom_minimum_reorder_level as mrl,
            i.custom_maximum_shelf_capacity as max_shelf,
            i.weight_per_unit,
            (SELECT bi.item_code
             FROM `tabBOM Item` bi
             JOIN `tabBOM` b ON bi.parent = b.name
             JOIN `tabItem` pack_item ON bi.item_code = pack_item.name
             WHERE b.item = i.name AND b.is_default = 1 AND b.docstatus = 1
             AND pack_item.item_group = 'Packaging Materials' LIMIT 1) as pack_code
        FROM `tabItem` i
        WHERE i.name IN ({format_items}) AND i.custom_linked_bip IS NOT NULL
    """, tuple_items, as_dict=True)

    active_bips = list(set([fg['custom_linked_bip'] for fg in fgs if fg.get('custom_linked_bip')]))
    if not active_bips: return []
    format_bips = ','.join(['%s'] * len(active_bips))
    tuple_bips = tuple(active_bips)

    bips = frappe.db.sql(f"""
        SELECT name as bip_code, item_name as bip_name, custom_minimum_production_level as min_batch
        FROM `tabItem`
        WHERE custom_is_bip = 1 AND name IN ({format_bips})
    """, tuple_bips, as_dict=True)

    stock = frappe.db.sql(f"""
        SELECT item_code, SUM(actual_qty) as actual_qty
        FROM `tabBin`
        WHERE warehouse = 'Finished Goods - CAL' AND item_code IN ({format_items})
        GROUP BY item_code
    """, tuple_items, as_dict=True)

    payload = {"bips": bips, "fgs": fgs, "stock": stock, "sales_orders": sales_orders, "reservations": reservations}
    fastapi_url = "https://crystal-api.crystalapps.dev/api/v1/production-cards"

    try:
        response = requests.post(fastapi_url, json=payload, timeout=15)
        response.raise_for_status()
        return response.json().get("data", [])
    except Exception as e:
        frappe.log_error(message=str(e), title="Nexus Production Sync Failed")
        return []

@frappe.whitelist(allow_guest=False)
def sync_manifest_from_app(manifest_name, trip_status=None, stops=None):
    doc = frappe.get_doc("Vehicle Delivery Manifest", manifest_name)

    if trip_status:
        doc.trip_status = trip_status

    if stops:
        if isinstance(stops, str):
            stops = json.loads(stops)

        for app_stop in stops:
            target_sales_order = None
            for d in doc.stops:
                if d.name == app_stop.get("name"):
                    d.delivery_status = app_stop.get("delivery_status")
                    d.driver_notes = app_stop.get("driver_notes")
                    target_sales_order = d.sales_order
                    break

            returned_items = app_stop.get("returned_items")
            if returned_items and isinstance(returned_items, list) and target_sales_order:
                for item in returned_items:
                    doc.append("returned_items", {
                        "sales_order": target_sales_order,
                        "item_code": item.get("item_code"),
                        "returned_qty": item.get("qty_returned"),
                        "reason": app_stop.get("primary_reason_for_return")
                    })

    has_pending_stops = any(d.delivery_status == 'Pending' for d in doc.stops)

    if not has_pending_stops and doc.trip_status == 'Dispatched':
        doc.trip_status = 'Returning'
        vehicle_transit_name = frappe.db.get_value("Vehicle In Transit", {"current_driver": doc.driver}, "name")
        if vehicle_transit_name:
            frappe.db.set_value("Vehicle In Transit", vehicle_transit_name, "current_status", "Returning")

    if doc.trip_status == 'Completed':
        vehicle_transit_name = frappe.db.get_value("Vehicle In Transit", {"current_driver": doc.driver}, "name")
        if vehicle_transit_name:
            frappe.db.set_value("Vehicle In Transit", vehicle_transit_name, "current_status", "Idle")

    doc.flags.ignore_validate_update_after_submit = True
    doc.save(ignore_permissions=True)

    return {"status": "success", "message": "Manifest synced securely."}

@frappe.whitelist()
def get_my_active_manifests_and_context():
    driver_email = frappe.session.user

    manifest_records = frappe.get_all(
        "Vehicle Delivery Manifest",
        filters=[
            ["driver", "=", driver_email],
            ["trip_status", "in", ["Ready", "Dispatched", "Completed", "Returning"]]
        ],
        fields=["name", "vehicle", "trip_status", "route_geojson", "cumulative_additional_fuel_cost"]
    )

    manifests = []
    for record in manifest_records:
        doc = frappe.get_doc("Vehicle Delivery Manifest", record.name)
        manifest_dict = doc.as_dict()

        for stop in manifest_dict.get("stops", []):
            if stop.get("customer"):
                try:
                    coords = frappe.db.get_value(
                        "Customer",
                        stop.get("customer"),
                        ["custom_latitude", "custom_longitude"],
                        as_dict=True
                    )
                    if coords:
                        stop["custom_latitude"] = coords.get("custom_latitude") or stop.get("latitude")
                        stop["custom_longitude"] = coords.get("custom_longitude") or stop.get("longitude")
                    else:
                        stop["custom_latitude"] = stop.get("latitude")
                        stop["custom_longitude"] = stop.get("longitude")
                except Exception:
                    stop["custom_latitude"] = stop.get("latitude")
                    stop["custom_longitude"] = stop.get("longitude")

            stop["items"] = []
            if stop.get("sales_order"):
                try:
                    so_items = frappe.get_all(
                        "Sales Order Item",
                        filters={"parent": stop.get("sales_order")},
                        fields=["item_code", "item_name", "qty as max_qty"]
                    )
                    stop["items"] = so_items
                except Exception:
                    pass

        manifests.append(manifest_dict)

    vehicle = frappe.db.get_value("Vehicle In Transit", {"current_driver": driver_email}, "name") or "Idle"

    active_manifest = None
    if manifests:
        dispatched = [m.name for m in manifests if m.trip_status == "Dispatched"]
        returning = [m.name for m in manifests if m.trip_status == "Returning"]
        ready = [m.name for m in manifests if m.trip_status == "Ready"]
        completed = [m.name for m in manifests if m.trip_status == "Completed"]

        if dispatched:
            active_manifest = dispatched[0]
        elif returning:
            active_manifest = returning[0]
        elif ready:
            active_manifest = ready[0]
        elif completed:
            active_manifest = completed[0]

    return {
        "status": "success",
        "message": {
            "manifests": manifests,
            "context": {
                "vehicle": vehicle,
                "active_manifest_id": active_manifest or "No_Active_Manifest"
            }
        }
    }

@frappe.whitelist()
def log_driver_additional_fuel(manifest_id, amount):

    try:
        if not frappe.db.exists("Vehicle Delivery Manifest", manifest_id):
            return {"status": "error", "message": "Manifest not found."}

        try:
            fuel_amount_to_add = float(amount)
            if fuel_amount_to_add <= 0:
                return {"status": "error", "message": "Fuel amount must be greater than zero."}
        except (ValueError, TypeError):
            return {"status": "error", "message": "Invalid fuel amount format."}

        current_fuel = frappe.db.get_value("Vehicle Delivery Manifest", manifest_id, "cumulative_additional_fuel_cost") or 0.0
        current_profit = frappe.db.get_value("Vehicle Delivery Manifest", manifest_id, "profit_loss") or 0.0

        load_plan_id = frappe.db.get_value("Vehicle Delivery Manifest", manifest_id, "load_plan")
        total_order_value = frappe.db.get_value("Nexus Load Plan", load_plan_id, "total_amount") if load_plan_id else 0.0

        new_cumulative_fuel = float(current_fuel) + fuel_amount_to_add

        new_profit_loss = float(current_profit) - fuel_amount_to_add

        new_net_margin = (new_profit_loss / float(total_order_value) * 100) if total_order_value and float(total_order_value) > 0 else 0.0

        new_profitability_status = "Profitable" if new_profit_loss >= 0 else "Loss"

        update_dict = {
            "cumulative_additional_fuel_cost": new_cumulative_fuel,
            "profit_loss": new_profit_loss,
            "net_margin": new_net_margin,
            "profitability_status": new_profitability_status
        }

        frappe.db.set_value("Vehicle Delivery Manifest", manifest_id, update_dict, update_modified=False)
        frappe.db.commit()

        frappe.publish_realtime('doc_update', message={'doctype': 'Vehicle Delivery Manifest', 'name': manifest_id})

        return {
            "status": "success",
            "message": "Fuel expense logged successfully.",
            "new_cumulative_total": new_cumulative_fuel
        }

    except Exception as e:
        frappe.db.rollback()
        frappe.log_error(title="Refuel Logging Error", message=str(e))
        return {"status": "error", "message": f"Server Error: {str(e)}"}

@frappe.whitelist()
def save_fcm_token(fcm_token):
    user = frappe.session.user
    if user == "Guest":
        frappe.local.response["http_status_code"] = 401
        return {"status": "failed", "message": "Unauthorized"}

    try:
        existing_device = frappe.db.get_value("Nexus FCM Device", {"user": user, "fcm_token": fcm_token}, "name")
        if not existing_device:
            doc = frappe.new_doc("Nexus FCM Device")
            doc.user = user
            doc.fcm_token = fcm_token
            doc.insert(ignore_permissions=True)
            frappe.db.commit()

        return {"status": "success", "message": "Device registered for push notifications."}
    except Exception as e:
        frappe.log_error("FCM Token Save Error", str(e))
        return {"status": "failed", "message": "Failed to save token. Check server logs."}

@frappe.whitelist()
def get_driver_context():
    driver_email = frappe.session.user
    vehicle = frappe.db.get_value("Vehicle In Transit", {"current_driver": driver_email}, "name")

    if not vehicle:
        return {"status": "failed", "message": "No vehicle assigned to this driver."}

    manifest = frappe.db.get_value("Vehicle Delivery Manifest", {
        "vehicle": vehicle,
        "trip_status": ["in", ["Ready", "Dispatched"]]
    }, "name")

    return {
        "status": "success",
        "vehicle": vehicle,
        "manifest_id": manifest or "No_Active_Manifest"
    }

def get_root_sales_person(user_email):
    employee_name = frappe.db.get_value("Employee", {"user_id": user_email}, "name")
    if employee_name:
        sales_person = frappe.db.get_value("Sales Person", {"employee": employee_name}, "name")
        if sales_person: return sales_person

    fallback_sp = frappe.db.get_value("Sales Person", {"employee": user_email}, "name")
    if fallback_sp: return fallback_sp
    return None

def get_authorized_sales_persons(user_email):
    root_sp = get_root_sales_person(user_email)
    if not root_sp: return []

    sp_doc = frappe.db.get_value("Sales Person", root_sp, ["lft", "rgt"], as_dict=True)
    if not sp_doc: return []

    authorized_sps = frappe.db.sql("""
        SELECT name FROM `tabSales Person`
        WHERE lft >= %s AND rgt <= %s
    """, (sp_doc.lft, sp_doc.rgt), as_dict=False)

    return [sp[0] for sp in authorized_sps] if authorized_sps else []


def get_authorized_sales_emails(user_email):
    """
    Maps the rep's Sales Person hierarchy (get_authorized_sales_persons —
    nested-set descendants including self) down to actual login emails.
    Nexus Sales Visit.sales_person stores the raw session email (see
    register_sales_check_in), NOT the Sales Person doctype name, so every
    visit-based query needs this email set rather than the SP-name set.
    """
    auth_sps = get_authorized_sales_persons(user_email)
    if not auth_sps:
        return []

    format_sps = ','.join(['%s'] * len(auth_sps))
    rows = frappe.db.sql(f"""
        SELECT employee FROM `tabSales Person` WHERE name IN ({format_sps})
    """, tuple(auth_sps), as_dict=True)

    emails = set()
    for r in rows:
        if not r.employee:
            continue
        if "@" in r.employee:
            emails.add(r.employee.lower())
        else:
            resolved = frappe.db.get_value("Employee", r.employee, "user_id")
            if resolved:
                emails.add(resolved.lower())
    return list(emails)

def get_emails_for_sales_persons(sp_names):
    """
    Direct SP-name(s) -> login email(s) resolver, with NO nested-set
    expansion — used only when narrowing to exactly one validated team
    member (e.g. Team Sales Activity's per-rep drilldown), as opposed to
    get_authorized_sales_emails which always expands to the full Shape A
    hierarchy under a user.
    """
    if not sp_names:
        return []
    format_sps = ','.join(['%s'] * len(sp_names))
    rows = frappe.db.sql(f"""
        SELECT employee FROM `tabSales Person` WHERE name IN ({format_sps})
    """, tuple(sp_names), as_dict=True)

    emails = set()
    for r in rows:
        if not r.employee:
            continue
        if "@" in r.employee:
            emails.add(r.employee.lower())
        else:
            resolved = frappe.db.get_value("Employee", r.employee, "user_id")
            if resolved:
                emails.add(resolved.lower())
    return list(emails)

def get_direct_customer_ids(sales_person_name):
    """
    Shape B: customers directly assigned to this exact Sales Person node —
    sales_person = owner_name, no lft/rgt range at all. Used only where a
    personal-vs-team split needs to be displayed. "Subordinates only" is
    deliberately never computed as its own query — it is always Shape A
    (get_authorized_sales_persons, recursive/self-inclusive) minus Shape B
    (this function, direct-only) at the UI layer, so neither shape ever
    needs to know or special-case whether the owner is a leaf or a group.
    """
    if not sales_person_name:
        return []
    rows = frappe.db.sql("""
        SELECT DISTINCT parent FROM `tabSales Team`
        WHERE parenttype = 'Customer' AND sales_person = %s
    """, (sales_person_name,), as_dict=False)
    return [r[0] for r in rows] if rows else []


@frappe.whitelist()
def get_manager_team_roster():
    """
    Resolves whether the calling session is a manager (is_group = 1 on
    their own Sales Person node, via the existing get_root_sales_person)
    and, if so, returns the roster of everyone under them (self excluded
    from this list — self remains included in Shape A elsewhere for
    aggregate scoping purposes).
    """
    root_sp = get_root_sales_person(frappe.session.user)
    if not root_sp:
        return {"status": "error", "message": "No Sales Person profile linked to your account."}

    sp_doc = frappe.db.get_value(
        "Sales Person", root_sp,
        ["is_group", "sales_person_name", "custom_sales_target", "custom_collections_target"],
        as_dict=True
    )
    if not sp_doc:
        return {"status": "error", "message": "Sales Person record not found."}

    is_manager = bool(sp_doc.is_group)

    auth_sps = get_authorized_sales_persons(frappe.session.user)
    team_sps = [sp for sp in auth_sps if sp != root_sp]

    roster = []
    if team_sps:
        format_sps = ','.join(['%s'] * len(team_sps))
        roster = frappe.db.sql(f"""
            SELECT sp.name as sales_person, sp.sales_person_name, sp.employee,
                   sp.custom_sales_target as sales_target,
                   sp.custom_collections_target as collection_target,
                   e.user_id as email
            FROM `tabSales Person` sp
            LEFT JOIN `tabEmployee` e ON sp.employee = e.name
            WHERE sp.name IN ({format_sps})
            ORDER BY sp.sales_person_name ASC
        """, tuple(team_sps), as_dict=True)

    return {
        "status": "success",
        "is_manager": is_manager,
        "root_sales_person": root_sp,
        "root_sales_person_name": sp_doc.sales_person_name,
        "team": roster
    }

@frappe.whitelist()
def get_team_target_breakdown():
    """
    One row per team member, each computed via Shape B (direct customer
    assignment only, get_direct_customer_ids — no lft/rgt range) so a
    manager's Team Targets tab shows exactly what each individual rep
    personally sold/collected this month, in the same grouped shape
    (target / gross_invoiced / returns / collected / outstanding / overdue)
    the FinancialBlock component already renders everywhere else. Uses the
    same shared get_customer_scoped_financial_totals helper as the
    dashboard aggregate and personal-block computations — never a
    duplicated math path.

    Only returns data if the calling session is actually a manager
    (is_group = 1 on their own node) — a plain rep has no team to break
    down.
    """
    root_sp = get_root_sales_person(frappe.session.user)
    if not root_sp:
        return {"status": "error", "message": "No Sales Person profile linked to your account."}

    is_group = frappe.db.get_value("Sales Person", root_sp, "is_group")
    if not is_group:
        return {"status": "error", "message": "This view is only available to managers."}

    auth_sps = get_authorized_sales_persons(frappe.session.user)
    team_sps = [sp for sp in auth_sps if sp != root_sp]
    if not team_sps:
        return {"status": "success", "data": []}

    format_team_sps = ','.join(['%s'] * len(team_sps))
    members = frappe.db.sql(f"""
        SELECT sp.name as sales_person, sp.sales_person_name,
               sp.custom_sales_target as sales_target,
               sp.custom_collections_target as collection_target,
               e.user_id as email
        FROM `tabSales Person` sp
        LEFT JOIN `tabEmployee` e ON sp.employee = e.name
        WHERE sp.name IN ({format_team_sps})
        ORDER BY sp.sales_person_name ASC
    """, tuple(team_sps), as_dict=True)

    start_of_month = get_first_day(today())
    end_of_month = get_last_day(today())

    breakdown = []
    for m in members:
        member_customer_ids = get_direct_customer_ids(m.sales_person)
        member_financials = get_customer_scoped_financial_totals(member_customer_ids, start_of_month, end_of_month, sales_person_ids=[m.sales_person])

        breakdown.append({
            "sales_person": m.sales_person,
            "sales_person_name": m.sales_person_name,
            "email": m.email,
            "sales_block": {
                "target": flt(m.sales_target),
                "gross_invoiced": member_financials["gross_invoiced"],
                "returns": member_financials["returns"],
                "net_invoiced": member_financials["net_invoiced"]
            },
            "collections_block": {
                "target": flt(m.collection_target),
                "collected": member_financials["collections"],
                "outstanding": member_financials["outstanding"],
                "overdue": member_financials["overdue"]
            }
        })

    return {"status": "success", "data": breakdown}

def resolve_authorized_target_email(session_user, requested_email):
    """
    Validates that a client-supplied "drill down as this email" header
    (sales-rep-email) is something the calling session is actually
    authorized to view, rather than trusting it blindly. Previously, any
    authenticated rep could set this header to an arbitrary email and pull
    that person's customers, orders, PDCs, activity, or item-wise analysis —
    nothing checked the relationship between the session identity and the
    requested identity.

    A manager is authorized to request a specific subordinate's email
    because their own nested-set range (lft/rgt) covers that subordinate's
    Sales Person node — the same range check already used everywhere else
    in this file (get_authorized_sales_persons, _add_sp_and_ancestors, etc).
    Anyone requesting an email outside their own range is silently
    redirected back to their own session identity instead, and the mismatch
    is logged for visibility.
    """
    if not requested_email:
        return session_user

    normalized_requested = requested_email.strip().lower()
    normalized_session = (session_user or "").strip().lower()

    if normalized_requested == normalized_session:
        return session_user

    session_root_sp = get_root_sales_person(session_user)
    requested_root_sp = get_root_sales_person(requested_email)

    if not session_root_sp or not requested_root_sp:
        frappe.log_error(
            title="Nexus Auth: Unresolvable Sales Person Drilldown",
            message=f"Session user {session_user} requested email {requested_email}, but one or both could not be resolved to a Sales Person."
        )
        return session_user

    session_sp_doc = frappe.db.get_value("Sales Person", session_root_sp, ["lft", "rgt"], as_dict=True)
    requested_sp_doc = frappe.db.get_value("Sales Person", requested_root_sp, ["lft", "rgt"], as_dict=True)

    if not session_sp_doc or not requested_sp_doc:
        frappe.log_error(
            title="Nexus Auth: Missing lft/rgt on Sales Person Drilldown",
            message=f"Session user {session_user} requested email {requested_email}, but lft/rgt could not be loaded."
        )
        return session_user

    is_authorized = (
        session_sp_doc.lft <= requested_sp_doc.lft
        and session_sp_doc.rgt >= requested_sp_doc.rgt
    )

    if is_authorized:
        return requested_email

    frappe.log_error(
        title="Nexus Auth: Unauthorized Drilldown Attempt",
        message=f"Session user {session_user} requested email {requested_email}, which is outside their authorized Sales Person hierarchy. Falling back to session identity."
    )
    return session_user

@frappe.whitelist()
def get_sales_dashboard_data():
    user = frappe.session.user
    auth_sps = get_authorized_sales_persons(user)

    if not auth_sps:
        return {"status": "error", "message": "No Sales Person hierarchy linked to your account."}

    cache_key = f"nexus_sales_dashboard_{user}_{today()}"
    cached_data = frappe.cache().get_value(cache_key)

    if cached_data:
        return {"status": "success", "source": "cache", "data": cached_data}

    start_of_month = get_first_day(today())
    end_of_month = get_last_day(today())

    format_sps = ','.join(['%s'] * len(auth_sps))
    tuple_sps = tuple(auth_sps)

    targets = frappe.db.sql(f"""
        SELECT SUM(custom_sales_target) as sales_target, SUM(custom_collections_target) as collection_target
        FROM `tabSales Person` WHERE name IN ({format_sps})
    """,  tuple_sps, as_dict=True)[0]

    sales_target = targets.get("sales_target") or 0.0
    collection_target = targets.get("collection_target") or 0.0

    assigned_customers = frappe.db.sql(f"""
        SELECT DISTINCT parent FROM `tabSales Team`
        WHERE parenttype = 'Customer' AND sales_person IN ({format_sps})
    """, tuple_sps, as_dict=False)

    customer_list = [c[0] for c in assigned_customers] if assigned_customers else []

    if not customer_list:
        empty_payload = {
            "targets": {"sales": sales_target, "collection": collection_target},
            "sales_total": 0,
            "collection_total": 0,
            "sales_graph": [],
            "collections_graph": []
        }
        frappe.cache().set_value(cache_key, empty_payload, expires_in_sec=1800)
        return {"status": "success", "source": "db", "data": empty_payload}

    format_customers = ','.join(['%s'] * len(customer_list))

    sales_data = frappe.db.sql(f"""
        SELECT DAY(posting_date) as day, SUM(grand_total) as value
        FROM `tabSales Invoice`
        WHERE docstatus = 1 AND posting_date BETWEEN %s AND %s
        AND customer IN ({format_customers})
        GROUP BY DAY(posting_date)
        ORDER BY DAY(posting_date)
    """, tuple([start_of_month, end_of_month] + customer_list), as_dict=True)

    total_sales_made = sum([s['value'] for s in sales_data])

    collection_data = frappe.db.sql(f"""
        SELECT DAY(posting_date) as day, SUM(paid_amount) as value
        FROM `tabPayment Entry`
        WHERE docstatus = 1 AND payment_type = 'Receive' AND posting_date BETWEEN %s AND %s
        AND party_type = 'Customer' AND party IN ({format_customers})
        GROUP BY DAY(posting_date)
        ORDER BY DAY(posting_date)
    """, tuple([start_of_month, end_of_month] + customer_list), as_dict=True)

    total_collections_made = sum([c['value'] for c in collection_data])

    payload = {
        "targets": {"sales": sales_target, "collection": collection_target},
        "sales_total": total_sales_made, "collection_total": total_collections_made,
        "sales_graph": sales_data, "collections_graph": collection_data
    }

    frappe.cache().set_value(cache_key, payload, expires_in_sec=1800)
    return {"status": "success", "source": "db", "data": payload}

def get_customer_scoped_financial_totals(customer_ids, start_date, end_date, sales_person_ids=None):
    """
    Single source of truth for the financial numbers surfaced on the rep and
    manager dashboards.

    🚨 INVOICE-LEVEL ATTRIBUTION (gross_invoiced / returns / net_invoiced):
    Gross invoiced revenue and returns are now attributed via the Sales
    Invoice's OWN Sales Team child table — i.e. "who was actually tagged on
    this specific document" — rather than "which customer does this belong
    to, and who owns that customer today". This matches the standalone SQL
    reconciliation report Finance already uses, and closes the discrepancy
    where a customer reassigned between reps mid-year caused the app's
    customer-based total to diverge from the document-based total.

    Collections and Outstanding/Overdue remain customer-scoped (party-based
    GL/Payment Entry data has no natural "document owner" the way a Sales
    Invoice does), so those are unaffected and still key off customer_ids.

    sales_person_ids: the Sales Person node(s) to attribute gross_invoiced/
    returns to — e.g. the caller's full Shape A hierarchy for a team
    aggregate, or a single rep name for a Shape B "direct only" view.
    Required for the new invoice-level queries; if omitted, falls back to
    the legacy customer-based query so any caller not yet updated still
    works exactly as before.
    """
    result = {
        "gross_invoiced": 0.0,
        "returns": 0.0,
        "collections": 0.0,
        "outstanding": 0.0,
        "overdue": 0.0,
        "net_invoiced": 0.0
    }

    if not customer_ids:
        return result

    # 🚨 Bank/GL account tags used to detect bounced & rebanked cheques.
    BANK_AGAINST_ACCOUNTS = (
        '213503 - I&M Bank Ltd - CAL',
        '213501 - Equity Bank -Accra Road - CAL',
        '502410 - Miscellaneous Income - CAL, 213503 - I&M Bank Ltd - CAL'
    )

    as_of = today()
    gl_params = {
        "customer_list": tuple(customer_ids),
        "from_date": start_date,
        "to_date": end_date,
        "as_of": as_of,
        "against_accounts": BANK_AGAINST_ACCOUNTS
    }

    # 1 & 2. GROSS INVOICED & RETURNS — invoice-level attribution via the
    # Sales Invoice's own Sales Team child table. Wrapped in a DISTINCT
    # subquery before summing — an invoice with more than one matching
    # Sales Team row (shared/house account with multiple reps) would
    # otherwise get its grand_total summed once per matching row instead
    # of once per invoice, exactly like the total_orders aggregate above.
    if sales_person_ids:
        format_sps = ','.join(['%s'] * len(sales_person_ids))
        sp_tuple = tuple(sales_person_ids)

        invoiced_data = frappe.db.sql(f"""
            SELECT SUM(grand_total) as value FROM (
                SELECT DISTINCT si.name, si.grand_total
                FROM `tabSales Invoice` si
                INNER JOIN `tabSales Team` st
                    ON st.parent = si.name AND st.parenttype = 'Sales Invoice'
                WHERE si.docstatus = 1 AND si.is_return = 0
                AND si.posting_date BETWEEN %s AND %s
                AND st.sales_person IN ({format_sps})
            ) distinct_invoices
        """, tuple([start_date, end_date] + list(sp_tuple)), as_dict=True)
        result["gross_invoiced"] = flt(invoiced_data[0]['value']) if invoiced_data and invoiced_data[0]['value'] else 0.0

        returns_data = frappe.db.sql(f"""
            SELECT SUM(grand_total) as value FROM (
                SELECT DISTINCT si.name, si.grand_total
                FROM `tabSales Invoice` si
                INNER JOIN `tabSales Team` st
                    ON st.parent = si.name AND st.parenttype = 'Sales Invoice'
                WHERE si.docstatus = 1 AND si.is_return = 1
                AND si.posting_date BETWEEN %s AND %s
                AND st.sales_person IN ({format_sps})
            ) distinct_invoices
        """, tuple([start_date, end_date] + list(sp_tuple)), as_dict=True)
        raw_returns = flt(returns_data[0]['value']) if returns_data and returns_data[0]['value'] else 0.0
        result["returns"] = abs(raw_returns)
    else:
        # Legacy fallback — customer-based attribution, retained only for
        # any caller that hasn't been updated to pass sales_person_ids yet.
        invoiced_data = frappe.db.sql("""
            SELECT SUM(grand_total) as value
            FROM `tabSales Invoice`
            WHERE docstatus = 1 AND is_return = 0
            AND posting_date BETWEEN %(from_date)s AND %(to_date)s
            AND customer IN %(customer_list)s
        """, gl_params, as_dict=True)
        result["gross_invoiced"] = flt(invoiced_data[0]['value']) if invoiced_data and invoiced_data[0]['value'] else 0.0

        returns_data = frappe.db.sql("""
            SELECT SUM(grand_total) as value
            FROM `tabSales Invoice`
            WHERE docstatus = 1 AND is_return = 1
            AND posting_date BETWEEN %(from_date)s AND %(to_date)s
            AND customer IN %(customer_list)s
        """, gl_params, as_dict=True)
        raw_returns = flt(returns_data[0]['value']) if returns_data and returns_data[0]['value'] else 0.0
        result["returns"] = abs(raw_returns)

    # 3. COLLECTIONS — UNCHANGED. Still customer-scoped: Payment Entries +
    # Bounced Cheques (subtracted) + Rebanked Cheques (re-added).
    try:
        collections_data = frappe.db.sql("""
            WITH bounced_only AS (
                SELECT
                    gl.voucher_no,
                    (gl.debit)*-1 AS amount,
                    ROW_NUMBER() OVER (PARTITION BY gl.voucher_no ORDER BY gl.posting_date, gl.name) AS rn
                FROM `tabGL Entry` AS gl
                WHERE gl.voucher_type = 'Journal Entry'
                    AND gl.party_type = 'Customer'
                    AND gl.party IN %(customer_list)s
                    AND gl.against IN %(against_accounts)s
                    AND gl.posting_date BETWEEN %(from_date)s AND %(to_date)s
                    AND gl.debit <> 0
            )
            SELECT SUM(amount) as total FROM (
                SELECT pe.paid_amount AS amount
                FROM `tabPayment Entry` pe
                WHERE pe.docstatus = 1 AND pe.payment_type = 'Receive'
                    AND pe.posting_date BETWEEN %(from_date)s AND %(to_date)s
                    AND pe.party_type = 'Customer'
                    AND pe.party IN %(customer_list)s
                UNION ALL
                SELECT amount FROM bounced_only WHERE rn = 1
                UNION ALL
                SELECT gl.credit AS amount
                FROM `tabGL Entry` AS gl
                WHERE gl.voucher_type = 'Journal Entry'
                    AND gl.party_type = 'Customer'
                    AND gl.party IN %(customer_list)s
                    AND gl.against IN %(against_accounts)s
                    AND gl.posting_date BETWEEN %(from_date)s AND %(to_date)s
                    AND gl.credit <> 0
            ) combined
        """, gl_params, as_dict=True)
        result["collections"] = flt(collections_data[0]['total']) if collections_data and collections_data[0]['total'] else 0.0
    except Exception as e:
        frappe.log_error(title="Collections Query Fallback", message=str(e))
        simple_collections = frappe.db.sql("""
            SELECT SUM(paid_amount) as value FROM `tabPayment Entry`
            WHERE docstatus = 1 AND payment_type = 'Receive'
            AND posting_date BETWEEN %(from_date)s AND %(to_date)s
            AND party_type = 'Customer' AND party IN %(customer_list)s
        """, gl_params, as_dict=True)
        result["collections"] = flt(simple_collections[0]['value']) if simple_collections and simple_collections[0]['value'] else 0.0

    # 4. OUTSTANDING & OVERDUE — UNCHANGED. Still GL-based/customer-scoped,
    # matching the "Outstanding Debts" report.
    gl_totals = frappe.db.sql("""
        SELECT gl.party as customer, SUM(gl.debit - gl.credit) as total_outstanding
        FROM `tabGL Entry` gl
        WHERE gl.party_type = 'Customer' AND gl.party IN %(customer_list)s
            AND gl.posting_date <= %(as_of)s AND gl.is_cancelled = 0
        GROUP BY gl.party
        HAVING total_outstanding != 0
    """, gl_params, as_dict=True)
    result["outstanding"] = sum(flt(r.total_outstanding) for r in gl_totals if flt(r.total_outstanding) > 0)

    overdue_data = frappe.db.sql("""
        SELECT outstanding_amount
        FROM `tabSales Invoice`
        WHERE docstatus = 1 AND outstanding_amount > 0 AND due_date < %(as_of)s
        AND customer IN %(customer_list)s
    """, gl_params, as_dict=True)
    result["overdue"] = sum(flt(r.outstanding_amount) for r in overdue_data)

    result["net_invoiced"] = result["gross_invoiced"] - result["returns"]

    return result

@frappe.whitelist()
def get_sales_context():
    """
    Fetches the App's core operational data in ONE call for FastAPI to hash.
    Optimized via Nested Sets to pull all customers for the rep's authorized branch.
    Now includes Order Recovery Engine, Debt Snapshot, 0-Lag Dashboard Stats, and Dropdown Metadata.
    """

    requested_email = frappe.request.headers.get("sales-rep-email")
    target_email = resolve_authorized_target_email(frappe.session.user, requested_email)

    auth_sps = get_authorized_sales_persons(target_email)
    if not auth_sps:
        return {"status": "error", "message": "No assigned sales profile hierarchy."}

    format_sps = ','.join(['%s'] * len(auth_sps))
    tuple_sps = tuple(auth_sps)

    # 🚨 BATCH 8: resolve the caller's own Sales Person node (is_group,
    # personal targets) — needed to derive isManager/personal-vs-team blocks
    # further down. This is deliberately Shape A's root, not a new lookup.
    root_sp = get_root_sales_person(target_email)
    root_sp_doc = frappe.db.get_value(
        "Sales Person", root_sp,
        ["is_group", "sales_person_name", "custom_sales_target", "custom_collections_target"],
        as_dict=True
    ) if root_sp else None
    is_manager = bool(root_sp_doc.is_group) if root_sp_doc else False

    # 🚨 BATCH 9: owning_sales_person / owning_sales_person_name — lets a
    # manager's Customer List render a rep-name banner on each card. A
    # customer could in principle match more than one row in the authorized
    # Sales Team set (e.g. shared/house accounts with multiple reps) —
    # MIN() picks one deterministically rather than duplicating the customer
    # row or silently concatenating names, since GROUP BY c.name is already
    # required here to dedupe the JOIN.
    customers = frappe.db.sql(f"""
        SELECT
            c.name as name,
            c.customer_name,
            c.default_price_list,
            c.payment_terms,
            c.mobile_no,
            c.custom_phone_number,
            c.custom_location,
            c.custom_latitude,
            c.custom_longitude,
            c.custom_google_maps_link,
            c.custom_combined_coordinates,
            c.creation as customer_creation_date,
            c.name as customer_id,
            (SELECT MAX(posting_date) FROM `tabSales Invoice` WHERE customer = c.name AND docstatus = 1) as last_invoiced_date,
            MIN(st.sales_person) as owning_sales_person,
            MIN(sp.sales_person_name) as owning_sales_person_name
        FROM `tabCustomer` c
        JOIN `tabSales Team` st ON c.name = st.parent AND st.parenttype = 'Customer'
        LEFT JOIN `tabSales Person` sp ON sp.name = st.sales_person
        WHERE st.sales_person IN ({format_sps}) AND c.disabled = 0
        GROUP BY c.name
    """, tuple_sps, as_dict=True)

    customer_ids = [c['name'] for c in customers]

    # 🚨 LEADS — same visibility scope as customers (target_email's
    # hierarchy). Isolated: a lead-side failure is logged and returns an
    # empty list, so customers/orders/dashboard still sync normally.
    try:
        leads = get_scoped_leads(target_email)
        lead_status_options = get_lead_status_options()
    except Exception as e:
        frappe.log_error(title="Nexus Lead Sync Failed", message=f"{target_email}: {e}")
        leads, lead_status_options = [], []

    items = frappe.db.sql("""
        SELECT i.name as name, i.item_code, i.item_name
        FROM `tabItem` i
        JOIN `tabItem Group` ig ON i.item_group = ig.name
        WHERE i.disabled = 0
        AND ig.lft >= (SELECT lft FROM `tabItem Group` WHERE name = 'Finished Goods')
        AND ig.rgt <= (SELECT rgt FROM `tabItem Group` WHERE name = 'Finished Goods')
    """, as_dict=True)

    # 🚨 BATCH 4: Only pull prices from price lists that are both ENABLED
    # and marked as SELLING. Previously this pulled every row in
    # `tabItem Price` unconditionally — including disabled or buying-only
    # price lists — so a retired/buying price list's stale rate could still
    # surface in the app's item.prices map. This is the single future-proof
    # fix: neither OrderWizardScreen's floor-price lookup nor ItemsScreen's
    # "Global Price Lists" listing need any app-side filtering logic, since
    # both simply read whatever price_list keys arrive in item.prices —
    # toggling enabled/selling on a Price List in ERPNext now propagates
    # automatically on the next sync with zero app-side changes.
    prices = frappe.db.sql("""
        SELECT ip.item_code, ip.price_list, ip.price_list_rate
        FROM `tabItem Price` ip
        INNER JOIN `tabPrice List` pl ON pl.name = ip.price_list
        WHERE pl.enabled = 1 AND pl.selling = 1
    """, as_dict=True)

    bins = frappe.db.sql("""
        SELECT item_code, SUM(actual_qty) as actual_qty
        FROM `tabBin`
        WHERE warehouse = 'Finished Goods - CAL'
        GROUP BY item_code
    """, as_dict=True)

    try:
        regions = frappe.db.sql("""SELECT name FROM `tabDelivery Region`""", as_dict=True)
    except Exception:
        regions = [{"name": "Default Center"}]

    try:
        customer_groups = frappe.db.sql("""SELECT name FROM `tabCustomer Group`""", as_dict=True)
    except Exception:
        customer_groups = [{"name": "Commercial"}]

    try:
        territories = frappe.db.sql("""SELECT name FROM `tabTerritory`""", as_dict=True)
    except Exception:
        territories = [{"name": "All Territories"}]

    try:
        price_lists = frappe.db.sql("""SELECT name FROM `tabPrice List` WHERE selling = 1""", as_dict=True)
    except Exception:
        price_lists = [{"name": "Standard Selling"}]

    try:
        payment_terms_templates = frappe.db.sql("""SELECT name FROM `tabPayment Terms Template`""", as_dict=True)
    except Exception:
        payment_terms_templates = [{"name": "Standard Cash"}]

    try:
        currencies = frappe.db.sql("""SELECT name FROM `tabCurrency` WHERE enabled = 1""", as_dict=True)
    except Exception:
        currencies = [{"name": "KES"}]

    try:
        tax_categories = frappe.db.sql("""SELECT name FROM `tabTax Category`""", as_dict=True)
    except Exception:
        tax_categories = []

    # 🚨 SALES-PERSON-SCOPED (was owner-scoped) — consistent with Batch 3's
    # total_orders count and the Activity/Analysis tabs. A team lead now sees
    # every order attributed via the order's own Sales Team, not just orders
    # they personally tapped "Confirm Order" on — matching exactly how
    # trigger_cache_eviction_and_notify already attributes a Sales Order to
    # reps for cache invalidation purposes.
    #
    # order_creation_datetime is the real ERP `creation` timestamp (not the
    # device's local clock at submission time) — the Uploaded Orders screen
    # prefers this over the locally-stamped `date` once the vault has synced.
    thirty_days_ago = add_days(today(), -30)

    has_rejection_field = frappe.db.has_column("Sales Order", "custom_finance_rejection_reason")
    rejection_select = "so.custom_finance_rejection_reason as rejection_reason," if has_rejection_field else "NULL as rejection_reason,"

    recent_orders = frappe.db.sql(f"""
        SELECT DISTINCT
               so.name as id, so.customer_name as customer, so.custom_delivery_region as region,
               so.grand_total as total, so.status as status, so.transaction_date as date,
               so.creation as order_creation_datetime,
               {rejection_select}
               so.owner as created_by
        FROM `tabSales Order` so
        JOIN `tabSales Team` st ON st.parent = so.name AND st.parenttype = 'Sales Order'
        WHERE so.docstatus < 2
        AND so.transaction_date >= %(thirty_days_ago)s
        AND st.sales_person IN %(sp_list)s
        ORDER BY so.creation DESC
    """, {"thirty_days_ago": thirty_days_ago, "sp_list": tuple_sps}, as_dict=True)

    if recent_orders:
        order_names = [o.id for o in recent_orders]
        format_orders = ','.join(['%s'] * len(order_names))
        order_items = frappe.db.sql(f"""
            SELECT parent, item_code, item_name, qty, rate
            FROM `tabSales Order Item`
            WHERE parent IN ({format_orders})
        """, tuple(order_names), as_dict=True)

        invoices = frappe.db.sql(f"""
            SELECT si.sales_order, s.name as invoice_id, s.outstanding_amount, s.grand_total
            FROM `tabSales Invoice Item` si
            JOIN `tabSales Invoice` s ON si.parent = s.name
            WHERE si.sales_order IN ({format_orders}) AND s.docstatus = 1
        """, tuple(order_names), as_dict=True)

        inv_map = {}
        for inv in invoices:
            if inv.sales_order not in inv_map:
                inv_map[inv.sales_order] = {'grand': 0, 'out': 0, 'invoices': []}
            inv_map[inv.sales_order]['grand'] += inv.grand_total
            inv_map[inv.sales_order]['out'] += inv.outstanding_amount
            inv_map[inv.sales_order]['invoices'].append(inv.invoice_id)

        item_map = {}
        for it in order_items:
            item_map.setdefault(it.parent, []).append(it)

        for o in recent_orders:
            o['items'] = item_map.get(o.id, [])
            o['totalQty'] = sum(i['qty'] for i in o['items'])

            inv_data = inv_map.get(o.id)
            if inv_data:
                if inv_data['out'] <= 0:
                    o['payment_status'] = "Paid"
                elif inv_data['out'] < inv_data['grand']:
                    o['payment_status'] = "Partially Paid"
                else:
                    o['payment_status'] = "Unpaid"

                o['invoice_id'] = inv_data['invoices'][0] if inv_data['invoices'] else None
            else:
                o['payment_status'] = "Unpaid"
                o['invoice_id'] = None
    else:
        recent_orders = []

    debt_snapshot = []
    if customer_ids:
        # 🚨 owning_sales_person_name — reused from the customers list
        # already fetched above (Batch 9's owning_sales_person_name), no
        # extra SQL needed. Lets Outstandings/Overdues show the assigned
        # rep per customer, same as the Customer List does for managers.
        customer_owner_map = {c['name']: c.get('owning_sales_person_name') for c in customers}

        format_custs = ','.join(['%s'] * len(customer_ids))
        unpaid_invoices = frappe.db.sql(f"""
            SELECT name as invoice_id, customer as customer_id, customer_name, posting_date, due_date, grand_total, outstanding_amount
            FROM `tabSales Invoice`
            WHERE docstatus = 1 AND outstanding_amount > 0 AND customer IN ({format_custs})
            ORDER BY due_date ASC
        """, tuple(customer_ids), as_dict=True)

        if unpaid_invoices:
            inv_names = [inv.invoice_id for inv in unpaid_invoices]
            format_invs = ','.join(['%s'] * len(inv_names))
            inv_items = frappe.db.sql(f"""
                SELECT parent, item_code, item_name, qty, rate, amount
                FROM `tabSales Invoice Item`
                WHERE parent IN ({format_invs})
            """, tuple(inv_names), as_dict=True)

            inv_item_map = {}
            for it in inv_items:
                inv_item_map.setdefault(it.parent, []).append(it)

            for inv in unpaid_invoices:
                inv['items'] = inv_item_map.get(inv.invoice_id, [])
                inv['owning_sales_person_name'] = customer_owner_map.get(inv.customer_id)

            debt_snapshot = unpaid_invoices

    start_of_month = get_first_day(today())
    end_of_month = get_last_day(today())

    targets = frappe.db.sql(f"""
        SELECT SUM(custom_sales_target) as sales_target, SUM(custom_collections_target) as collection_target
        FROM `tabSales Person` WHERE name IN ({format_sps})
    """, tuple_sps, as_dict=True)[0]

    sales_target = targets.get("sales_target") or 0.0
    collection_target = targets.get("collection_target") or 0.0

    # 🚨 BATCH 2: TOTAL ORDERS — MTD count + value, Draft + Submitted, scoped by
    # the Sales Order's OWN Sales Team child table (not "customer belongs to
    # rep"). This is deliberately consistent with how Uploaded Orders /
    # dispatch attribute an order to a rep, and lets a rep see orders on
    # shared/house accounts where they're one of several reps on the order
    # itself. Wrapped in a DISTINCT subquery before aggregating — an order
    # with more than one matching Sales Team row would otherwise get its
    # grand_total summed once per matching row instead of once per order.
    order_agg = frappe.db.sql("""
        SELECT COUNT(*) as cnt, SUM(grand_total) as total_value
        FROM (
            SELECT DISTINCT so.name, so.grand_total
            FROM `tabSales Order` so
            JOIN `tabSales Team` st ON st.parent = so.name AND st.parenttype = 'Sales Order'
            WHERE so.docstatus IN (0, 1)
            AND so.transaction_date BETWEEN %(from_date)s AND %(to_date)s
            AND st.sales_person IN %(sp_list)s
        ) distinct_orders
    """, {"sp_list": tuple_sps, "from_date": start_of_month, "to_date": end_of_month}, as_dict=True)
    total_orders = order_agg[0]['cnt'] if order_agg and order_agg[0]['cnt'] else 0
    total_orders_value = flt(order_agg[0]['total_value']) if order_agg and order_agg[0]['total_value'] else 0.0

    # 🚨 BATCH 2: All invoiced/returns/collections/outstanding/overdue math now
    # lives in one shared helper — see get_customer_scoped_financial_totals.
    financial_totals = get_customer_scoped_financial_totals(customer_ids, start_of_month, end_of_month, sales_person_ids=auth_sps)

    # 🚨 BATCH 8: Shape A (team, recursive/self-inclusive) is exactly what's
    # already computed above via auth_sps/customer_ids — no new query needed
    # for the team aggregate. Manager-only additions below: team roster and
    # Shape B (personal, direct-only) for the personal-vs-team split.
    team_roster = []
    personal_sales_block = None
    personal_collections_block = None
    show_personal_block = False

    if is_manager:
        team_sps = [sp for sp in auth_sps if sp != root_sp]
        if team_sps:
            format_team_sps = ','.join(['%s'] * len(team_sps))
            team_roster = frappe.db.sql(f"""
                SELECT sp.name as sales_person, sp.sales_person_name, sp.employee,
                       e.user_id as email
                FROM `tabSales Person` sp
                LEFT JOIN `tabEmployee` e ON sp.employee = e.name
                WHERE sp.name IN ({format_team_sps})
                ORDER BY sp.sales_person_name ASC
            """, tuple(team_sps), as_dict=True)

        personal_customer_ids = get_direct_customer_ids(root_sp)
        personal_target = flt(root_sp_doc.custom_sales_target) if root_sp_doc else 0.0
        personal_collection_target = flt(root_sp_doc.custom_collections_target) if root_sp_doc else 0.0
        personal_financials = get_customer_scoped_financial_totals(personal_customer_ids, start_of_month, end_of_month, sales_person_ids=[root_sp])

        personal_sales_block = {
            "target": personal_target,
            "gross_invoiced": personal_financials["gross_invoiced"],
            "returns": personal_financials["returns"],
            "net_invoiced": personal_financials["net_invoiced"]
        }
        personal_collections_block = {
            "target": personal_collection_target,
            "collected": personal_financials["collections"],
            "outstanding": personal_financials["outstanding"],
            "overdue": personal_financials["overdue"]
        }

        personal_achievement = personal_financials["gross_invoiced"] + personal_financials["collections"]
        show_personal_block = (personal_target > 0 or personal_collection_target > 0 or personal_achievement > 0)

    dashboard_stats = {
        # Flat keys retained for backwards compatibility with any consumer
        # still reading them directly. total_invoiced_orders now correctly
        # EXCLUDES credit notes — that revenue leakage is exposed separately
        # via total_returns / sales_block.returns instead of netting silently.
        "sales_target": float(sales_target),
        "collection_target": float(collection_target),
        "total_orders": int(total_orders),
        "total_orders_value": float(total_orders_value),
        "total_invoiced_orders": float(financial_totals["gross_invoiced"]),
        "total_returns": float(financial_totals["returns"]),
        "total_collections": float(financial_totals["collections"]),
        "total_outstanding": float(financial_totals["outstanding"]),
        "total_overdue": float(financial_totals["overdue"]),

        # Nested blocks — the shape the FinancialBlock component reads from.
        # For a manager these represent the TEAM aggregate (Shape A); for a
        # plain rep they're identical to their own numbers since Shape A
        # collapses to self when there are no descendants.
        "sales_block": {
            "target": float(sales_target),
            "gross_invoiced": float(financial_totals["gross_invoiced"]),
            "returns": float(financial_totals["returns"]),
            "net_invoiced": float(financial_totals["net_invoiced"])
        },
        "collections_block": {
            "target": float(collection_target),
            "collected": float(financial_totals["collections"]),
            "outstanding": float(financial_totals["outstanding"]),
            "overdue": float(financial_totals["overdue"])
        },

        # 🚨 BATCH 8: manager foundation — roster, Shape B personal blocks,
        # and the display hint for whether the personal block has anything
        # worth showing (hide a "0 of 0" bar for a pure coordinator).
        "is_manager": is_manager,
        "team_roster": team_roster,
        "show_personal_block": show_personal_block,
        "personal_sales_block": personal_sales_block,
        "personal_collections_block": personal_collections_block,

        # 🚨 root_sales_person / root_sales_person_name: the caller's own
        # Sales Person node, reused from the exact resolution already done
        # above (root_sp/root_sp_doc) — no extra query. Lets the app offer
        # a "Me" filter option alongside team members in per-rep pickers
        # (e.g. Sales Order Analysis) without a separate round trip.
        "root_sales_person": root_sp,
        "root_sales_person_name": root_sp_doc.sales_person_name if root_sp_doc else None,

        # 🚨 Total non-Converted leads in scope — derived from the SAME list
        # the app's Leads tab shows, so the card and the list always agree.
        "total_active_leads": len(leads)
    }

    return {
        "status": "success",
        "data": {
            "customers": customers,
            "items": items,
            "prices": prices,
            "bins": bins,
            "regions": regions,
            "customer_groups": customer_groups,
            "territories": territories,
            "price_lists": price_lists,
            "payment_terms_templates": payment_terms_templates,
            "currencies": currencies,
            "tax_categories": tax_categories,
            "order_history": recent_orders,
            "debt_snapshot": debt_snapshot,
            "dashboard_stats": dashboard_stats,
            "leads": leads,
            "lead_status_options": lead_status_options
        }
    }

@frappe.whitelist()
def get_invoice_details_for_order(order_id):
    """
    On-Demand fetch for the Differential Viewer.
    Pulls strictly the invoiced items associated with a specific Sales Order intent.
    """
    items = frappe.db.sql("""
        SELECT si.item_code, si.item_name, si.qty, si.rate, si.amount
        FROM `tabSales Invoice Item` si
        JOIN `tabSales Invoice` s ON si.parent = s.name
        WHERE si.sales_order = %s AND s.docstatus = 1
    """, (order_id,), as_dict=True)

    return {"status": "success", "data": items}

@frappe.whitelist()
def get_customer_financial_brief(customer_id):
    """
    Production-ready single-customer financial snapshot for the mobile
    Check-In window. Replaces the old FastAPI-side approach that pulled the
    customer's ENTIRE Sales Invoice list over the generic REST resource API
    (`/api/resource/Sales Invoice`, hardcoded limit_page_length=5000) and
    re-aggregated 4 numbers in Python on every single check-in — fragile
    because it silently truncates at the page limit, depends on stringified
    filter-JSON staying stable, and pays for a full row payload just to sum
    4 numbers.

    Outstanding here is computed from GL Entry (the same source of truth used
    by the dashboard aggregate and the "Outstanding Debts" report), NOT from
    Sales Invoice.outstanding_amount, which can lag behind Journal Entry
    adjustments (bounced/rebanked cheques, write-offs, etc). This guarantees
    the number shown here always agrees with the Outstandings/Overdues tabs.
    """
    if not customer_id:
        return {"status": "error", "message": "customer_id is required."}
    if not frappe.db.exists("Customer", customer_id):
        return {"status": "error", "message": "Customer not found."}

    today_date = today()
    start_of_year = datetime(getdate(today_date).year, 1, 1).strftime('%Y-%m-%d')
    start_of_month = get_first_day(today_date)
    end_of_month = get_last_day(today_date)

    sales_row = frappe.db.sql("""
        SELECT
            SUM(CASE WHEN posting_date >= %(start_of_year)s THEN grand_total ELSE 0 END) as ytd,
            SUM(CASE WHEN posting_date BETWEEN %(start_of_month)s AND %(end_of_month)s THEN grand_total ELSE 0 END) as mtd
        FROM `tabSales Invoice`
        WHERE docstatus = 1 AND customer = %(customer_id)s
    """, {
        "start_of_year": start_of_year,
        "start_of_month": start_of_month,
        "end_of_month": end_of_month,
        "customer_id": customer_id
    }, as_dict=True)
    row = sales_row[0] if sales_row else {}

    # GL-based outstanding — matches the dashboard aggregate & Outstanding Debts report
    gl_row = frappe.db.sql("""
        SELECT SUM(gl.debit - gl.credit) as total_outstanding
        FROM `tabGL Entry` gl
        WHERE gl.party_type = 'Customer' AND gl.party = %(customer_id)s
            AND gl.posting_date <= %(today_date)s AND gl.is_cancelled = 0
    """, {"customer_id": customer_id, "today_date": today_date}, as_dict=True)
    outstanding = flt(gl_row[0].total_outstanding) if gl_row and gl_row[0].total_outstanding else 0.0
    outstanding = outstanding if outstanding > 0 else 0.0

    overdue_row = frappe.db.sql("""
        SELECT SUM(outstanding_amount) as overdue
        FROM `tabSales Invoice`
        WHERE docstatus = 1 AND outstanding_amount > 0 AND due_date < %(today_date)s
        AND customer = %(customer_id)s
    """, {"customer_id": customer_id, "today_date": today_date}, as_dict=True)
    overdue = flt(overdue_row[0].overdue) if overdue_row and overdue_row[0].overdue else 0.0

    return {
        "status": "success",
        "data": {
            "ytd": flt(row.get("ytd")),
            "mtd": flt(row.get("mtd")),
            "outstanding": round(outstanding, 2),
            "overdue": round(overdue, 2)
        }
    }

@frappe.whitelist()
def get_pdc_breakdown():
    """
    Returns per-customer Post-Dated Cheque / promised-payment breakdown,
    scoped to the calling rep's authorized customer set, split into aging
    buckets by how far in the future the promised payment date sits:
    0-30, 31-60, 61-90, 91-120, and 121+ days out.

    Source: Payment Entry with docstatus IN (0, 1) — both drafted promises
    and submitted-but-future-dated entries — matching the exact definition
    used by get_customer_pdc_amount() in the Outstanding Debts report, so
    the PDC tab and that report never disagree.
    """
    requested_email = frappe.request.headers.get("sales-rep-email")
    target_email = resolve_authorized_target_email(frappe.session.user, requested_email)
    auth_sps = get_authorized_sales_persons(target_email)
    if not auth_sps:
        return {"status": "error", "message": "No assigned sales profile hierarchy."}

    format_sps = ','.join(['%s'] * len(auth_sps))
    tuple_sps = tuple(auth_sps)

    # 🚨 owning_sales_person_name — same MIN()-over-GROUP-BY pattern used in
    # get_sales_context's customers query (Batch 9), since a customer could
    # in principle match more than one row in the authorized Sales Team set
    # (shared/house accounts with multiple reps) and DISTINCT alone can't be
    # trusted once a joined, non-key column is added to the select list.
    customer_rows = frappe.db.sql(f"""
        SELECT
            c.name as customer_id,
            c.customer_name,
            MIN(sp.sales_person_name) as owning_sales_person_name
        FROM `tabCustomer` c
        JOIN `tabSales Team` st ON c.name = st.parent AND st.parenttype = 'Customer'
        LEFT JOIN `tabSales Person` sp ON sp.name = st.sales_person
        WHERE st.sales_person IN ({format_sps}) AND c.disabled = 0
        GROUP BY c.name
    """, tuple_sps, as_dict=True)

    if not customer_rows:
        return {"status": "success", "data": []}

    customer_ids = [c.customer_id for c in customer_rows]
    name_map = {c.customer_id: c.customer_name for c in customer_rows}
    owner_map = {c.customer_id: c.owning_sales_person_name for c in customer_rows}
    format_custs = ','.join(['%s'] * len(customer_ids))
    today_date = today()

    pdc_rows = frappe.db.sql(f"""
        SELECT
            party as customer_id,
            name as payment_entry,
            reference_date,
            posting_date,
            paid_amount,
            reference_no,
            DATEDIFF(reference_date, %s) as days_out
        FROM `tabPayment Entry`
        WHERE party_type = 'Customer'
        AND party IN ({format_custs})
        AND docstatus IN (0, 1)
        AND payment_type = 'Receive'
        AND (reference_date >= %s OR posting_date >= %s)
        ORDER BY reference_date ASC
    """, tuple([today_date] + customer_ids + [today_date, today_date]), as_dict=True)

    grouped = {}
    for row in pdc_rows:
        cid = row.customer_id
        if cid not in grouped:
            grouped[cid] = {
                "customer_id": cid,
                "customer_name": name_map.get(cid, cid),
                "owning_sales_person_name": owner_map.get(cid),
                "total_amount": 0.0,
                "bucket_0_30": 0.0,
                "bucket_31_60": 0.0,
                "bucket_61_90": 0.0,
                "bucket_91_120": 0.0,
                "bucket_121_plus": 0.0,
                "entries": []
            }

        days_out = row.days_out if row.days_out is not None else 0
        amount = flt(row.paid_amount)
        grouped[cid]["total_amount"] += amount

        if days_out <= 30:
            grouped[cid]["bucket_0_30"] += amount
        elif days_out <= 60:
            grouped[cid]["bucket_31_60"] += amount
        elif days_out <= 90:
            grouped[cid]["bucket_61_90"] += amount
        elif days_out <= 120:
            grouped[cid]["bucket_91_120"] += amount
        else:
            grouped[cid]["bucket_121_plus"] += amount

        grouped[cid]["entries"].append({
            "payment_entry": row.payment_entry,
            "reference_date": str(row.reference_date) if row.reference_date else None,
            "posting_date": str(row.posting_date) if row.posting_date else None,
            "amount": amount,
            "reference_no": row.reference_no,
            "days_out": days_out
        })

    data = sorted(grouped.values(), key=lambda g: -g["total_amount"])
    return {"status": "success", "data": data}

@frappe.whitelist()
def get_activity_stats(from_date=None, to_date=None, filter_sales_person=None):
    """
    Visit-level activity breakdown for the calling rep's hierarchy (self +
    subordinates), sourced from Nexus Sales Visit.

    On-Site is read from the stored is_on_site field — the same value the
    attendance report counts — set at check-in by compute_is_on_site,
    recomputed after every location correction and backfilled for history.
    Customer and Lead visits are both included in every total, with
    customer_visits / lead_visits breakdowns alongside.

    Defaults to the current calendar month if no explicit range is given.

    filter_sales_person narrows to exactly one validated team member (must
    be inside the caller's own Shape A set, otherwise silently ignored).
    """
    requested_email = frappe.request.headers.get("sales-rep-email")
    target_email = resolve_authorized_target_email(frappe.session.user, requested_email)

    auth_sps = get_authorized_sales_persons(target_email)
    is_filtered = bool(filter_sales_person and filter_sales_person in auth_sps)

    if is_filtered:
        auth_emails = get_emails_for_sales_persons([filter_sales_person])
    else:
        auth_emails = get_authorized_sales_emails(target_email)

    if not auth_emails:
        return {"status": "error", "message": "No assigned sales profile hierarchy."}

    range_from = from_date or get_first_day(today())
    range_to = to_date or get_last_day(today())

    format_emails = ','.join(['%s'] * len(auth_emails))

    visit_select = """
        SELECT
            v.name, v.sales_person,
            IFNULL(v.visit_type, 'Customer') AS visit_type,
            IFNULL(v.is_on_site, 0) AS is_on_site,
            v.check_in_time, v.check_out_time, v.duration_minutes,
            IFNULL(v.location_corrected, 0) AS location_corrected,
            v.location_correction_source
        FROM `tabNexus Sales Visit` v
    """

    visits = frappe.db.sql(f"""
        {visit_select}
        WHERE v.sales_person IN ({format_emails})
        AND DATE(v.check_in_time) BETWEEN %s AND %s
    """, tuple(auth_emails) + (range_from, range_to), as_dict=True)

    today_visits_raw = frappe.db.sql(f"""
        {visit_select}
        WHERE v.sales_person IN ({format_emails})
        AND DATE(v.check_in_time) = %s
    """, tuple(auth_emails) + (today(),), as_dict=True)

    def _summarize(rows):
        s = {
            "total": len(rows), "customer": 0, "lead": 0,
            "on_site": 0, "off_site": 0,
            "corrected_on_site": 0, "snap_corrected_on_site": 0,
        }
        for v in rows:
            if v.visit_type == "Lead":
                s["lead"] += 1
            else:
                s["customer"] += 1
            if cint(v.is_on_site):
                s["on_site"] += 1
                # On-Site only because the location was corrected during the
                # visit, and the GPS-snap subset (0 m by construction).
                if cint(v.location_corrected):
                    s["corrected_on_site"] += 1
                    if v.location_correction_source == "GPS Snap":
                        s["snap_corrected_on_site"] += 1
            else:
                s["off_site"] += 1
        s["ratio"] = round(s["on_site"] * 100.0 / s["total"], 1) if s["total"] else 0.0
        return s

    period = _summarize(visits)
    today_s = _summarize(today_visits_raw)

    completed_durations = [flt(v.duration_minutes) for v in visits if v.check_out_time and v.duration_minutes]
    avg_duration = round(sum(completed_durations) / len(completed_durations), 1) if completed_durations else 0.0
    completed_visits = len(completed_durations)
    open_visits = period["total"] - completed_visits

    # TODAY'S ORDERS — unchanged: Sales Orders (Draft + Submitted) scoped by
    # the order's own Sales Team, narrowed the same way as the visits.
    scoped_sps_for_orders = [filter_sales_person] if is_filtered else auth_sps
    today_orders_count = 0
    today_orders_value = 0.0
    if scoped_sps_for_orders:
        format_sps_orders = ','.join(['%s'] * len(scoped_sps_for_orders))
        order_agg = frappe.db.sql(f"""
            SELECT COUNT(*) as cnt, SUM(grand_total) as total_value
            FROM (
                SELECT DISTINCT so.name, so.grand_total
                FROM `tabSales Order` so
                JOIN `tabSales Team` st ON st.parent = so.name AND st.parenttype = 'Sales Order'
                WHERE so.docstatus IN (0, 1)
                AND so.transaction_date = %s
                AND st.sales_person IN ({format_sps_orders})
            ) distinct_orders
        """, tuple([today()] + scoped_sps_for_orders), as_dict=True)
        today_orders_count = order_agg[0]['cnt'] if order_agg and order_agg[0]['cnt'] else 0
        today_orders_value = flt(order_agg[0]['total_value']) if order_agg and order_agg[0]['total_value'] else 0.0

    # Per-rep breakdown (same stored is_on_site rule).
    per_rep_rows = {}
    for v in visits:
        per_rep_rows.setdefault(v.sales_person, []).append(v)
    per_rep = []
    for rep, rows in per_rep_rows.items():
        s = _summarize(rows)
        per_rep.append({
            "sales_person": rep,
            "total": s["total"],
            "customer_visits": s["customer"],
            "lead_visits": s["lead"],
            "on_site": s["on_site"],
            "off_site": s["off_site"],
            "on_site_ratio": s["ratio"],
        })

    return {
        "status": "success",
        "data": {
            "from_date": str(range_from),
            "to_date": str(range_to),
            "total_visits": period["total"],
            "customer_visits": period["customer"],
            "lead_visits": period["lead"],
            "on_site_count": period["on_site"],
            "off_site_count": period["off_site"],
            "on_site_ratio": period["ratio"],
            "completed_visits": completed_visits,
            "open_visits": open_visits,
            "avg_duration_minutes": avg_duration,
            "per_rep": per_rep,
            "today_visits": today_s["total"],
            "today_customer_visits": today_s["customer"],
            "today_lead_visits": today_s["lead"],
            "today_on_site": today_s["on_site"],
            "today_off_site": today_s["off_site"],
            "today_on_site_ratio": today_s["ratio"],
            "today_orders_count": today_orders_count,
            "today_orders_value": today_orders_value,
            "corrected_on_site_count": period["corrected_on_site"],
            "snap_corrected_on_site_count": period["snap_corrected_on_site"],
            "today_corrected_on_site": today_s["corrected_on_site"]
        }
    }

@frappe.whitelist()
def get_my_sales_order_analysis(from_date=None, to_date=None, filter_sales_person=None):
    """
    Item-wise sales register scoped to the calling rep's authorized customer
    set (self + subordinates), sourced from submitted Sales Invoices — the
    definitive revenue record, consistent with every other revenue figure
    in this file (dashboard total_invoiced_orders, financial briefs, etc).

    🚨 BATCH 3: Defaults to a rolling window of the 4 full calendar months
    immediately BEFORE the current month, rather than the current
    (still-in-progress) month. On any given login date, this is computed as:
      range_end   = the day before the 1st of the current month
      range_start = the 1st of the month 4 months before the current month
    e.g. logging in on 26 Jul 2026 -> range_start = 1 Mar 2026,
    range_end = 30 Jun 2026. Explicit from_date/to_date query params (if a
    future caller ever passes them) always take precedence over this default.

    🚨 BATCH 10: filter_sales_person lets a manager narrow the aggregate
    view down to exactly one team member's Sales Person node. Validated
    against the caller's own auth_sps (the same Shape A set used everywhere
    else) — a manager can only filter to a name that's genuinely inside
    their own hierarchy; anything else is silently ignored and the call
    falls back to the full-team aggregate, exactly like an invalid/omitted
    filter would. A plain rep passing this has no effect either way, since
    their own auth_sps is just themselves.
    """
    requested_email = frappe.request.headers.get("sales-rep-email")
    target_email = resolve_authorized_target_email(frappe.session.user, requested_email)
    auth_sps = get_authorized_sales_persons(target_email)
    if not auth_sps:
        return {"status": "error", "message": "No assigned sales profile hierarchy."}

    # 🚨 BATCH 10: narrow the scope to a single validated team member if requested.
    scoped_sps = auth_sps
    if filter_sales_person and filter_sales_person in auth_sps:
        scoped_sps = [filter_sales_person]

    format_sps = ','.join(['%s'] * len(scoped_sps))
    tuple_sps = tuple(scoped_sps)

    customer_rows = frappe.db.sql(f"""
        SELECT DISTINCT parent FROM `tabSales Team`
        WHERE parenttype = 'Customer' AND sales_person IN ({format_sps})
    """, tuple_sps, as_dict=False)
    customer_ids = [c[0] for c in customer_rows] if customer_rows else []

    if not customer_ids:
        return {"status": "success", "data": {"items": [], "totals": {
            "total_revenue": 0.0, "total_qty": 0.0, "invoice_count": 0,
            "distinct_items": 0, "distinct_customers": 0
        }}}

    # 🚨 BATCH 3: rolling prior-4-months default, replacing the old
    # current-month default (get_first_day(today()) / get_last_day(today())).
    current_month_start = get_first_day(today())
    default_range_end = add_days(current_month_start, -1)
    default_range_start = get_first_day(add_months(current_month_start, -4))

    range_from = from_date or default_range_start
    range_to = to_date or default_range_end

    format_custs = ','.join(['%s'] * len(customer_ids))
    params = tuple([range_from, range_to] + customer_ids)

    rows = frappe.db.sql(f"""
        SELECT
            sii.item_code,
            sii.item_name,
            sii.qty,
            sii.amount,
            si.name as invoice_id,
            si.customer
        FROM `tabSales Invoice Item` sii
        JOIN `tabSales Invoice` si ON sii.parent = si.name
        WHERE si.docstatus = 1
        AND si.posting_date BETWEEN %s AND %s
        AND si.customer IN ({format_custs})
    """, params, as_dict=True)

    item_map = {}
    invoice_ids = set()
    customer_set = set()

    for r in rows:
        invoice_ids.add(r.invoice_id)
        customer_set.add(r.customer)
        key = r.item_code
        if key not in item_map:
            item_map[key] = {
                "item_code": r.item_code,
                "item_name": r.item_name,
                "total_qty": 0.0,
                "total_amount": 0.0,
                "invoice_count": set(),
                "customer_count": set()
            }
        item_map[key]["total_qty"] += flt(r.qty)
        item_map[key]["total_amount"] += flt(r.amount)
        item_map[key]["invoice_count"].add(r.invoice_id)
        item_map[key]["customer_count"].add(r.customer)

    items_out = []
    for v in item_map.values():
        items_out.append({
            "item_code": v["item_code"],
            "item_name": v["item_name"],
            "total_qty": round(v["total_qty"], 2),
            "total_amount": round(v["total_amount"], 2),
            "invoice_count": len(v["invoice_count"]),
            "customer_count": len(v["customer_count"])
        })
    items_out.sort(key=lambda x: -x["total_amount"])

    total_revenue = sum(i["total_amount"] for i in items_out)
    total_qty = sum(i["total_qty"] for i in items_out)

    return {
        "status": "success",
        "data": {
            "from_date": str(range_from),
            "to_date": str(range_to),
            "filter_sales_person": scoped_sps[0] if len(scoped_sps) == 1 and scoped_sps != auth_sps else None,
            "items": items_out,
            "totals": {
                "total_revenue": round(total_revenue, 2),
                "total_qty": round(total_qty, 2),
                "invoice_count": len(invoice_ids),
                "distinct_items": len(items_out),
                "distinct_customers": len(customer_set)
            }
        }
    }

@frappe.whitelist()
def get_customer_analysis(filter_sales_person=None):
    """
    Top-20 customer revenue ranking for the calling rep's authorized set
    (self + subordinates), sourced from submitted non-return Sales Invoices —
    attributed via the invoice's OWN Sales Team child table (invoice-level
    attribution, identical pattern to get_my_returns), NOT via "who currently
    owns this customer" — so a customer reassigned mid-window still shows
    correctly under whichever rep actually closed each specific invoice.

    🚨 ROLLING WINDOW EXPLICITLY EXCLUDES THE CURRENT (IN-PROGRESS) MONTH.
    This is deliberately the 3 full calendar months immediately BEFORE the
    current one — not "last 3 months inclusive of today" — because this
    view exists to tell a rep/manager who to prioritize promoting *based on
    a closed, complete picture*, and the current month's still-accumulating
    numbers would bias that ranking toward whoever's been invoiced most
    recently rather than who's actually gone quiet. Computed as:
      current_month_start = the 1st of the current calendar month
      range_end            = the day BEFORE current_month_start
      range_start           = the 1st of the month 3 months before current_month_start
    e.g. logging in on 10 Apr 2026 -> range_start = 1 Jan 2026,
    range_end = 31 Mar 2026 (January, February, March — April excluded
    entirely). This is intentionally unaffected by which day of the current
    month the rep/manager logs in on, and by nature of being computed here
    (not per-caller), it applies identically to a plain rep viewing their
    own numbers, a manager viewing the full team aggregate, and a manager
    viewing a single filtered team member.

    filter_sales_person: same validated-against-auth_sps pattern as Batch
    10's order analysis filter — must be a name inside the caller's own
    Shape A hierarchy (get_authorized_sales_persons), otherwise silently
    ignored and the call falls back to the full-team aggregate. A plain
    rep passing this has no effect either way, since their own auth_sps
    is just themselves.
    """
    requested_email = frappe.request.headers.get("sales-rep-email")
    target_email = resolve_authorized_target_email(frappe.session.user, requested_email)
    auth_sps = get_authorized_sales_persons(target_email)
    if not auth_sps:
        return {"status": "error", "message": "No assigned sales profile hierarchy."}

    scoped_sps = auth_sps
    if filter_sales_person and filter_sales_person in auth_sps:
        scoped_sps = [filter_sales_person]

    format_sps = ','.join(['%s'] * len(scoped_sps))
    tuple_sps = tuple(scoped_sps)

    # 🚨 Rolling 3-full-months-BEFORE-current-month window — current month
    # is never included, regardless of what day of the month this runs on.
    current_month_start = get_first_day(today())
    range_end = add_days(current_month_start, -1)
    range_start = get_first_day(add_months(current_month_start, -3))

    rows = frappe.db.sql(f"""
        SELECT
            si.customer as customer_id,
            si.customer_name as customer_name,
            SUM(si.grand_total) as total
        FROM `tabSales Invoice` si
        WHERE si.docstatus = 1 AND si.is_return = 0
        AND si.posting_date BETWEEN %s AND %s
        AND si.name IN (
            SELECT DISTINCT st.parent FROM `tabSales Team` st
            WHERE st.parenttype = 'Sales Invoice' AND st.sales_person IN ({format_sps})
        )
        GROUP BY si.customer
        ORDER BY total DESC
        LIMIT 20
    """, tuple([range_start, range_end] + list(tuple_sps)), as_dict=True)

    customers_out = [
        {
            "customer_id": r.customer_id,
            "customer_name": r.customer_name,
            "total_invoiced": round(flt(r.total), 2)
        } for r in rows
    ]

    return {
        "status": "success",
        "data": {
            "from_date": str(range_start),
            "to_date": str(range_end),
            "filter_sales_person": scoped_sps[0] if len(scoped_sps) == 1 and scoped_sps != auth_sps else None,
            "customers": customers_out
        }
    }

@frappe.whitelist()
def get_my_returns(from_date=None, to_date=None, filter_sales_person=None):
    """
    Returns register — submitted credit-note Sales Invoices (is_return = 1)
    for the calling rep's authorized customer set (self + subordinates).

    🚨 INVOICE-LEVEL ATTRIBUTION: attributed via the credit note's OWN Sales
    Team child table, matching the same model now used in
    get_customer_scoped_financial_totals for the Dashboard's Returns
    subline — "who was tagged on this specific document", not "who
    currently owns the customer". Keeps this tab reconciling exactly with
    the Dashboard and with Finance's standalone SQL report.

    🚨 filter_sales_person mirrors Batch 10's validation: must be a name
    inside the caller's own Shape A hierarchy (get_authorized_sales_persons),
    otherwise silently ignored and the call falls back to the full-team scope.
    """
    requested_email = frappe.request.headers.get("sales-rep-email")
    target_email = resolve_authorized_target_email(frappe.session.user, requested_email)
    auth_sps = get_authorized_sales_persons(target_email)
    if not auth_sps:
        return {"status": "error", "message": "No assigned sales profile hierarchy."}

    scoped_sps = auth_sps
    if filter_sales_person and filter_sales_person in auth_sps:
        scoped_sps = [filter_sales_person]

    format_sps = ','.join(['%s'] * len(scoped_sps))
    tuple_sps = tuple(scoped_sps)

    range_from = from_date or get_first_day(today())
    range_to = to_date or today()

    has_reason_field = frappe.db.has_column("Sales Invoice", "custom_reason_for_return")
    reason_select = "si.custom_reason_for_return as reason_for_return," if has_reason_field else "NULL as reason_for_return,"

    # 🚨 Credit notes whose OWN Sales Team includes one of the scoped reps —
    # matched via IN(subquery) rather than a JOIN, so an invoice with
    # multiple matching Sales Team rows (shared/house account) is never
    # returned more than once here.
    returns = frappe.db.sql(f"""
        SELECT
            si.name as invoice_id,
            si.customer as customer_id,
            si.customer_name,
            si.posting_date,
            si.grand_total,
            {reason_select}
            si.name as _dummy_keep_alias
        FROM `tabSales Invoice` si
        WHERE si.docstatus = 1 AND si.is_return = 1
        AND si.posting_date BETWEEN %s AND %s
        AND si.name IN (
            SELECT DISTINCT st.parent FROM `tabSales Team` st
            WHERE st.parenttype = 'Sales Invoice' AND st.sales_person IN ({format_sps})
        )
        ORDER BY si.posting_date DESC
    """, tuple([range_from, range_to] + list(tuple_sps)), as_dict=True)

    if returns:
        inv_names = [r.invoice_id for r in returns]
        format_invs = ','.join(['%s'] * len(inv_names))

        # 🚨 owning_sales_person_name — now resolved from the credit note's
        # OWN Sales Team (matching the invoice-level attribution above),
        # not the customer's. MIN() picks deterministically if an invoice
        # somehow has more than one matching Sales Team row.
        owner_rows = frappe.db.sql(f"""
            SELECT st.parent as invoice_id, MIN(sp.sales_person_name) as owning_sales_person_name
            FROM `tabSales Team` st
            LEFT JOIN `tabSales Person` sp ON sp.name = st.sales_person
            WHERE st.parenttype = 'Sales Invoice' AND st.parent IN ({format_invs})
            GROUP BY st.parent
        """, tuple(inv_names), as_dict=True)
        owner_map = {o.invoice_id: o.owning_sales_person_name for o in owner_rows}

        items = frappe.db.sql(f"""
            SELECT parent, item_code, item_name, qty, rate, amount
            FROM `tabSales Invoice Item`
            WHERE parent IN ({format_invs})
        """, tuple(inv_names), as_dict=True)

        item_map = {}
        for it in items:
            item_map.setdefault(it.parent, []).append(it)

        for r in returns:
            # Credit notes store negative qty/amount/grand_total in ERPNext —
            # normalize to positive magnitudes for display purposes only.
            r['grand_total'] = abs(flt(r.grand_total))
            r['owning_sales_person_name'] = owner_map.get(r.invoice_id)
            raw_items = item_map.get(r.invoice_id, [])
            r['items'] = [
                {
                    "item_code": it.item_code,
                    "item_name": it.item_name,
                    "qty": abs(flt(it.qty)),
                    "rate": flt(it.rate),
                    "amount": abs(flt(it.amount))
                } for it in raw_items
            ]

    total_amount = sum(flt(r.grand_total) for r in returns)
    distinct_customers = len(set(r.customer_id for r in returns))

    return {
        "status": "success",
        "data": {
            "from_date": str(range_from),
            "to_date": str(range_to),
            "returns": returns,
            "totals": {
                "total_amount": round(total_amount, 2),
                "return_count": len(returns),
                "distinct_customers": distinct_customers
            }
        }
    }

@frappe.whitelist()
def submit_sales_order_from_app(payload):

    if isinstance(payload, str):
        payload = json.loads(payload)

    try:
        so = frappe.new_doc("Sales Order")
        so.customer = payload.get("customer")
        so.order_type = "Sales"
        so.transaction_date = today()
        so.delivery_date = add_days(today(), 1)

        if payload.get("delivery_region"):
            so.custom_delivery_region = payload.get("delivery_region")

        if payload.get("notes"):
            so.inter_company_reference = payload.get("notes")

        for item in payload.get("items", []):
            line_note = item.get("notes")
            so.append("items", {
                "item_code": item.get("item_code"),
                "qty": float(item.get("qty")),
                "rate": float(item.get("rate")),
                "description": line_note if line_note else payload.get("notes", "")
            })

        target_email = payload.get("sales_rep_email") or frappe.session.user
        sales_person = get_root_sales_person(target_email)

        if sales_person:
            so.append("sales_team", {
                "sales_person": sales_person,
                "allocated_percentage": 100.0
            })

        so.insert(ignore_permissions=True)

        return {
            "status": "success",
            "erp_order_id": so.name,
            "message": f"Order {so.name} successfully created."
        }

    except Exception as e:
        frappe.log_error(title="App Order Submission Failed", message=str(e))
        return {"status": "error", "message": f"Failed to create order: {str(e)}"}

@frappe.whitelist()
def edit_draft_sales_order(order_id, payload):
    """
    🚨 BATCH 4: Edits an existing Sales Order — but ONLY while it is still in
    Draft (docstatus == 0). Once confirmed (docstatus == 1, status has moved
    to "To Deliver and Bill" or beyond), this hard-fails rather than silently
    mutating a submitted document. Rebuilds the items child table from
    scratch (clear + re-append) rather than diffing line-by-line, since the
    app always sends the complete replacement cart, not a delta — identical
    pattern to how submit_sales_order_from_app builds the items table on
    creation.
    """
    if isinstance(payload, str):
        payload = json.loads(payload)

    if not order_id:
        return {"status": "error", "message": "order_id is required."}

    if not frappe.db.exists("Sales Order", order_id):
        return {"status": "error", "message": "Sales Order not found."}

    try:
        so = frappe.get_doc("Sales Order", order_id)

        # 🚨 HARD GATE: only Draft (docstatus 0) orders can be edited. Once
        # confirmed and moved to "To Deliver and Bill" (docstatus 1), the
        # app must never be able to silently rewrite line items on a
        # submitted document.
        if so.docstatus != 0:
            return {
                "status": "error",
                "message": f"This order can no longer be edited (status: {so.status}). Only Draft orders can be edited."
            }

        items = payload.get("items", [])
        if not items:
            return {"status": "error", "message": "Order must contain at least one item."}

        # Full rebuild of the items child table — the app always sends the
        # complete replacement cart, so clear + re-append is simpler and
        # safer than attempting a line-by-line diff/merge.
        so.set("items", [])
        for item in items:
            line_note = item.get("notes")
            so.append("items", {
                "item_code": item.get("item_code"),
                "qty": float(item.get("qty")),
                "rate": float(item.get("rate")),
                "description": line_note if line_note else (payload.get("notes") or ""),
            })

        if payload.get("delivery_region"):
            so.custom_delivery_region = payload.get("delivery_region")

        if payload.get("notes"):
            so.inter_company_reference = payload.get("notes")

        so.save(ignore_permissions=True)

        return {
            "status": "success",
            "erp_order_id": so.name,
            "message": f"Order {so.name} successfully updated."
        }

    except Exception as e:
        frappe.log_error(title="App Draft Order Edit Failed", message=str(e))
        return {"status": "error", "message": f"Failed to update order: {str(e)}"}

@frappe.whitelist()
def register_sales_check_in(customer, lat, lng):
    """
    Customer check-in. Signature and response keys unchanged for existing
    app builds; the visit itself is created by the shared _create_sales_visit
    routine, so customer and lead visits are recorded identically.
    `is_on_site` is an additive response key.
    """
    target = _get_party_target_coords("Customer", customer)
    doc = _create_sales_visit("Customer", customer, lat, lng, target)

    try:
        requests.post(
            "https://crystal-api.crystalapps.dev/telemetry/sales-check-in",
            json={"sales_rep": frappe.session.user, "customer": customer},
            timeout=2
        )
    except Exception:
        pass

    return {
        "status": "success",
        "message": "Check-In recorded successfully.",
        "distance_m": doc.distance_from_target_meters,
        "is_auto_offsite": doc.distance_from_target_meters is None,
        "is_on_site": bool(cint(doc.get("is_on_site"))),
        "visit_id": doc.name
    }

@frappe.whitelist()
def register_sales_check_out(customer):
    user_email = frappe.session.user

    visit_name = frappe.db.sql("""
        SELECT name, check_in_time
        FROM `tabNexus Sales Visit`
        WHERE sales_person = %s AND customer = %s AND (check_out_time IS NULL OR check_out_time = '')
        ORDER BY creation DESC LIMIT 1
    """, (user_email, customer), as_dict=True)

    if not visit_name:
        return {"status": "error", "message": "No active check-in found to close."}

    doc_name = visit_name[0].name
    check_in_time = visit_name[0].check_in_time
    check_out_time = frappe.utils.now_datetime()

    duration_minutes = 0.0
    if check_in_time:
        in_dt = get_datetime(check_in_time)
        out_dt = get_datetime(check_out_time)
        duration_minutes = round((out_dt - in_dt).total_seconds() / 60, 2)

    try:
        frappe.db.set_value("Nexus Sales Visit", doc_name, {
            "check_out_time": check_out_time,
            "duration_minutes": duration_minutes
        })
    except Exception as e:
        if "1020" in str(e) or "Record has changed" in str(e):
            frappe.db.rollback()
            pass
        else:
            return {"status": "error", "message": str(e)}

    return {"status": "success", "message": "Checked out successfully.", "duration_minutes": duration_minutes}

@frappe.whitelist()
def register_lead_check_in(lead, lat, lng):
    """
    Lead check-in. Mirrors the customer check-in exactly (same shared
    _create_sales_visit routine: check-in point, distance, target pin,
    original distance, is_on_site), plus lead_status_before.

    Refuses when: the lead doesn't exist, is Converted, is outside the
    caller's scope, or the caller has another visit open TODAY.
    Idempotent: if the caller is already checked into THIS lead today, the
    existing visit is returned (already_open=True) instead of a duplicate.
    Errors carry a machine-readable `code` for the app.
    """
    session_user = frappe.session.user

    if not lead or not frappe.db.exists("Lead", lead):
        return {"status": "error", "code": "LEAD_NOT_FOUND", "message": "This lead no longer exists."}

    lead_row = frappe.db.get_value(
        "Lead", lead, ["status", "lead_owner", "lead_name", "company_name"], as_dict=True
    )
    if lead_row.status == NEXUS_LEAD_CONVERTED_STATUS:
        return {
            "status": "error", "code": "LEAD_CONVERTED",
            "message": "This lead has already been converted to a customer. Find it in your Customer list."
        }

    access_error = _lead_access_error(lead_row.lead_owner, session_user)
    if access_error:
        return {"status": "error", "code": "LEAD_NOT_ASSIGNED", "message": access_error}

    open_visits = _get_open_visits_today(session_user)

    same_lead = next((v for v in open_visits if v.visit_type == "Lead" and v.lead == lead), None)
    if same_lead:
        existing = frappe.db.get_value(
            "Nexus Sales Visit", same_lead.name,
            ["distance_from_target_meters", "is_on_site", "lead_status_before", "party_name"],
            as_dict=True
        )
        return {
            "status": "success",
            "already_open": True,
            "message": "You are already checked in at this lead.",
            "visit_id": same_lead.name,
            "visit_type": "Lead",
            "party_name": existing.party_name,
            "lead_status": lead_row.status,
            "distance_m": existing.distance_from_target_meters,
            "is_on_site": bool(cint(existing.is_on_site)),
            "is_auto_offsite": existing.distance_from_target_meters is None,
        }

    if open_visits:
        conflict = open_visits[0]
        return {
            "status": "error",
            "code": "VISIT_ALREADY_OPEN",
            "message": f"You are still checked in at {conflict.party_name or conflict.customer or conflict.lead}. Check out there first.",
            "open_visit": {
                "visit_id": conflict.name,
                "visit_type": conflict.visit_type,
                "party": conflict.customer or conflict.lead,
                "party_name": conflict.party_name,
                "check_in_time": str(conflict.check_in_time) if conflict.check_in_time else None,
            },
        }

    target = _get_party_target_coords("Lead", lead)
    doc = _create_sales_visit(
        "Lead", lead, lat, lng, target,
        extra_fields={"lead_status_before": lead_row.status}
    )

    try:
        requests.post(
            "https://crystal-api.crystalapps.dev/telemetry/sales-check-in",
            json={
                "sales_rep": session_user,
                "customer": lead,
                "customer_name": f"Lead: {doc.party_name or lead}",
                "visit_type": "Lead",
            },
            timeout=2
        )
    except Exception:
        pass

    return {
        "status": "success",
        "already_open": False,
        "message": "Lead check-in recorded successfully.",
        "visit_id": doc.name,
        "visit_type": "Lead",
        "party_name": doc.party_name,
        "lead_status": lead_row.status,
        "distance_m": doc.distance_from_target_meters,
        "is_on_site": bool(cint(doc.get("is_on_site"))),
        "is_auto_offsite": doc.distance_from_target_meters is None,
    }


@frappe.whitelist()
def register_lead_check_out(visit_id, status=None, notes=None):
    """
    The app's single "Save & Check Out" action for a lead visit.

    ATOMIC: the Lead status change, the visit notes and the check-out are one
    database transaction. Any failure rolls ALL of it back and returns the
    reason (the app can then retry with status=None to check out without
    changing the status, so a rep is never stuck on site).

    - status: optional. "Converted" is always rejected (ERPNext sets it on
      conversion). Validated against the Lead doctype's own options.
    - The Lead is saved through ERPNext's normal save, so its validations
      run. ERPNext may keep a different status (e.g. an Opportunity exists);
      the status ACTUALLY stored is returned in lead_status.
    - If the office converted the lead meanwhile, the visit still closes,
      notes are kept, closed_by_conversion is ticked, lead_converted=True.
    - Idempotent: calling it again on an already-closed visit returns
      success with already_closed=True (safe network retries).
    """
    session_user = frappe.session.user

    if not visit_id or not frappe.db.exists("Nexus Sales Visit", visit_id):
        return {"status": "error", "code": "VISIT_NOT_FOUND", "message": "This visit record was not found."}

    visit = frappe.db.get_value(
        "Nexus Sales Visit", visit_id,
        ["name", "sales_person", "visit_type", "lead", "check_in_time", "check_out_time",
         "closed_by_conversion", "lead_status_after"],
        as_dict=True, for_update=True
    )

    if (visit.sales_person or "").strip().lower() != (session_user or "").strip().lower():
        return {"status": "error", "code": "NOT_YOUR_VISIT", "message": "You can only check out of your own visit."}
    if visit.visit_type != "Lead" or not visit.lead:
        return {"status": "error", "code": "NOT_A_LEAD_VISIT", "message": "This visit is not a lead visit."}

    if visit.check_out_time:
        return {
            "status": "success",
            "already_closed": True,
            "message": "This visit was already checked out.",
            "visit_id": visit.name,
            "lead": visit.lead,
            "lead_status": visit.lead_status_after,
            "lead_converted": bool(cint(visit.closed_by_conversion)),
            "check_out_time": str(visit.check_out_time),
        }

    requested_status = (status or "").strip() or None
    clean_notes = (notes or "").strip()

    if requested_status == NEXUS_LEAD_CONVERTED_STATUS:
        return {
            "status": "error", "code": "STATUS_NOT_ALLOWED",
            "message": "\"Converted\" is set automatically when the office creates a customer from this lead."
        }
    if requested_status and requested_status not in get_lead_status_options():
        return {"status": "error", "code": "INVALID_STATUS", "message": f"\"{requested_status}\" is not a valid lead status."}

    try:
        current_status = frappe.db.get_value("Lead", visit.lead, "status")
        lead_converted = current_status == NEXUS_LEAD_CONVERTED_STATUS
        final_status = current_status
        status_message = None

        if lead_converted:
            status_message = "This lead was converted to a customer by the office. Your notes were saved and the visit is closed."
        elif requested_status and requested_status != current_status:
            lead_doc = frappe.get_doc("Lead", visit.lead)
            lead_doc.status = requested_status
            lead_doc.save(ignore_permissions=True)
            final_status = frappe.db.get_value("Lead", visit.lead, "status")
            if final_status != requested_status:
                status_message = (
                    f"ERPNext kept this lead's status as \"{final_status}\" "
                    f"(it is linked to other records), so \"{requested_status}\" was not applied."
                )

        check_out_time = frappe.utils.now_datetime()
        duration_minutes = 0.0
        if visit.check_in_time:
            duration_minutes = round(
                (get_datetime(check_out_time) - get_datetime(visit.check_in_time)).total_seconds() / 60, 2
            )

        update = {
            "check_out_time": check_out_time,
            "duration_minutes": duration_minutes,
            "lead_status_after": final_status,
            "lead_visit_notes": clean_notes or None,
        }
        if lead_converted:
            update["closed_by_conversion"] = 1

        frappe.db.set_value("Nexus Sales Visit", visit.name, update, update_modified=False)
        frappe.db.commit()

        return {
            "status": "success",
            "already_closed": False,
            "message": status_message or "Visit saved and checked out.",
            "visit_id": visit.name,
            "lead": visit.lead,
            "lead_status": final_status,
            "requested_status": requested_status,
            "status_changed": final_status != current_status,
            "status_message": status_message,
            "lead_converted": lead_converted,
            "duration_minutes": duration_minutes,
            "check_out_time": str(check_out_time),
        }

    except Exception as e:
        frappe.db.rollback()
        frappe.clear_messages()
        frappe.log_error(
            title="Lead Check-Out Failed",
            message=f"visit={visit_id}, lead={visit.lead}, user={session_user}, status={requested_status}: {e}"
        )
        return {
            "status": "error",
            "code": "CHECKOUT_FAILED",
            "message": f"Nothing was saved. {str(e) or 'The lead could not be updated.'}",
            "can_retry_without_status": bool(requested_status),
        }


@frappe.whitelist()
def get_extended_sales_reports(report_type):

    auth_sps = get_authorized_sales_persons(frappe.session.user)
    if not auth_sps: return {"status": "error", "message": "No sales profile hierarchy."}

    format_sps = ','.join(['%s'] * len(auth_sps))
    tuple_sps = tuple(auth_sps)

    assigned_customers = frappe.db.sql(f"""
        SELECT parent FROM `tabSales Team` WHERE parenttype = 'Customer' AND sales_person IN ({format_sps})
    """, tuple_sps, as_dict=False)

    customer_list = [c[0] for c in assigned_customers] if assigned_customers else []
    if not customer_list: return {"status": "success", "data": []}

    format_customers = ','.join(['%s'] * len(customer_list))
    data = []

    if report_type == "Outstanding":
        start_of_year = datetime(today().year, 1, 1).strftime('%Y-%m-%d')
        data = frappe.db.sql(f"""
            SELECT name as invoice_id, customer as customer_id, customer_name, posting_date, grand_total, outstanding_amount, due_date
            FROM `tabSales Invoice`
            WHERE docstatus = 1 AND outstanding_amount > 0 AND posting_date >= %s
            AND customer IN ({format_customers})
            ORDER BY posting_date DESC
        """, tuple([start_of_year] + customer_list), as_dict=True)

    elif report_type == "Overdues":
        data = frappe.db.sql(f"""
            SELECT name as invoice_id, customer as customer_id, customer_name, posting_date, grand_total, outstanding_amount, due_date
            FROM `tabSales Invoice`
            WHERE docstatus = 1 AND outstanding_amount > 0 AND due_date < %s
            AND customer IN ({format_customers})
            ORDER BY due_date ASC
        """, tuple([today()] + customer_list), as_dict=True)

    elif report_type == "PDC":
        max_date = add_months(today(), 2)
        data = frappe.db.sql(f"""
            SELECT name as payment_entry, party as customer, party_name, reference_date, paid_amount, reference_no
            FROM `tabPayment Entry`
            WHERE docstatus = 1 AND payment_type = 'Receive' AND party_type = 'Customer'
            AND reference_date BETWEEN %s AND %s
            AND party IN ({format_customers})
            ORDER BY reference_date ASC
        """, tuple([today(), max_date] + customer_list), as_dict=True)
        return {"status": "success", "data": data}

    else:
        return {"status": "error", "message": "Invalid report type."}

    if data:
        inv_names = [d.invoice_id for d in data]
        format_invs = ','.join(['%s'] * len(inv_names))
        items = frappe.db.sql(f"""
            SELECT parent, item_code, item_name, qty, rate, amount
            FROM `tabSales Invoice Item`
            WHERE parent IN ({format_invs})
        """, tuple(inv_names), as_dict=True)

        item_map = {}
        for it in items:
            item_map.setdefault(it.parent, []).append(it)

        for d in data:
            d['items'] = item_map.get(d.invoice_id, [])

    return {"status": "success", "data": data}

def trigger_app_customer_refresh(doc, method=None):
    old_doc = doc.get_doc_before_save()
    if not old_doc: return

    monitored_fields = [
        'customer_name', 'default_price_list', 'payment_terms',
        'mobile_no', 'custom_phone_number', 'custom_location',
        'custom_latitude', 'custom_longitude', 'custom_combined_coordinates'
    ]
    vault_data_changed = any(doc.get(field) != old_doc.get(field) for field in monitored_fields)

    old_sales_persons = set([row.sales_person for row in old_doc.get("sales_team", []) if row.sales_person])
    new_sales_persons = set([row.sales_person for row in doc.get("sales_team", []) if row.sales_person])

    affected_sales_persons = old_sales_persons.symmetric_difference(new_sales_persons)

    if vault_data_changed:
        affected_sales_persons.update(new_sales_persons)

    if not affected_sales_persons:
        return

    affected_emails = set()
    format_affected = ','.join(['%s'] * len(affected_sales_persons))

    affected_coords = frappe.db.sql(f"""
        SELECT lft, rgt FROM `tabSales Person` WHERE name IN ({format_affected})
    """, tuple(affected_sales_persons), as_dict=True)

    if affected_coords:
        or_conditions = [f"(lft <= {c.lft} AND rgt >= {c.rgt})" for c in affected_coords]
        where_clause = " OR ".join(or_conditions)

        ancestor_sps = frappe.db.sql(f"""
            SELECT name, employee FROM `tabSales Person` WHERE {where_clause}
        """, as_dict=True)

        for sp in ancestor_sps:
            if sp.employee:
                user_email = frappe.db.get_value("Employee", sp.employee, "user_id")
                if user_email:
                    affected_emails.add(user_email)
                elif "@" in sp.employee:
                    affected_emails.add(sp.employee)

    if affected_emails:
        try:
            requests.post(
                "https://crystal-api.crystalapps.dev/telemetry/force-app-refresh",
                json={"emails": list(affected_emails), "command": "FORCE_REFRESH_CUSTOMERS"},
                timeout=3
            )
        except Exception:
            pass

def trigger_app_catalog_refresh(doc, method=None):
    if hasattr(doc, 'docstatus') and doc.docstatus == 0:
        return

    reps = frappe.db.sql("""
        SELECT e.user_id
        FROM `tabSales Person` sp
        JOIN `tabEmployee` e ON sp.employee = e.name
        WHERE e.user_id IS NOT NULL AND e.status = 'Active'
    """, as_dict=True)

    affected_emails = set([r.user_id for r in reps if r.user_id])

    fallback = frappe.db.sql("""
        SELECT employee FROM `tabSales Person` WHERE employee LIKE '%@%'
    """, as_dict=True)
    for r in fallback:
        affected_emails.add(r.employee)

    if affected_emails:
        try:
            requests.post(
                "https://crystal-api.crystalapps.dev/telemetry/force-app-refresh",
                json={"emails": list(affected_emails), "command": "FORCE_VAULT_SYNC"},
                timeout=3
            )
        except Exception as e:
            frappe.log_error(title="App Catalog Refresh Trigger Failed", message=str(e))


def trigger_financial_refresh(doc, method=None):
    increment_collection = 0.0
    party = None

    if doc.doctype == "Payment Entry":
        # party_type/party ARE real top-level fields on Payment Entry — safe to access directly here
        if doc.party_type != 'Customer' or not doc.party:
            return
        party = doc.party
        if doc.payment_type == 'Receive':
            if doc.docstatus == 1:
                increment_collection = float(doc.paid_amount or 0.0)
            elif doc.docstatus == 2:
                increment_collection = -float(doc.paid_amount or 0.0)

    elif doc.doctype == "Journal Entry":
        # Journal Entry has NO top-level party_type/party — only inside `accounts` child rows.
        # docstatus is already guaranteed 1 or 2 here (this only runs via on_submit/on_cancel).
        sign = 1 if doc.docstatus == 1 else -1
        for jea in doc.get("accounts", []):
            if jea.party_type == "Customer" and jea.party:
                party = jea.party  # last matching row wins if a JE somehow splits across >1 customer
                row_amount = float(jea.credit_in_account_currency or 0.0) - float(jea.debit_in_account_currency or 0.0)
                increment_collection += sign * row_amount
        if not party:
            return
    else:
        return

    if increment_collection == 0.0:
        return

    invoice_ids = []
    if hasattr(doc, 'references'):  # only Payment Entry has this child table; safely False for Journal Entry
        for ref in doc.references:
            if ref.reference_doctype == 'Sales Invoice' and ref.reference_name:
                invoice_ids.append(ref.reference_name)

    updated_orders = []
    if invoice_ids:
        format_invs = ','.join(['%s'] * len(invoice_ids))
        sos = frappe.db.sql(f"""
            SELECT DISTINCT sales_order
            FROM `tabSales Invoice Item`
            WHERE parent IN ({format_invs}) AND sales_order IS NOT NULL AND sales_order != ''
        """, tuple(invoice_ids), as_dict=True)

        if sos:
            so_names = [s.sales_order for s in sos]
            format_sos = ','.join(['%s'] * len(so_names))
            so_invs = frappe.db.sql(f"""
                SELECT si.sales_order, s.outstanding_amount, s.grand_total
                FROM `tabSales Invoice Item` si
                JOIN `tabSales Invoice` s ON si.parent = s.name
                WHERE si.sales_order IN ({format_sos}) AND s.docstatus = 1
            """, tuple(so_names), as_dict=True)

            so_map = {}
            for inv in so_invs:
                if inv.sales_order not in so_map:
                    so_map[inv.sales_order] = {'grand': 0, 'out': 0}
                so_map[inv.sales_order]['grand'] += inv.grand_total
                so_map[inv.sales_order]['out'] += inv.outstanding_amount

            for so_name in so_names:
                data = so_map.get(so_name)
                if data:
                    if data['out'] <= 0: p_status = "Paid"
                    elif data['out'] < data['grand']: p_status = "Partially Paid"
                    else: p_status = "Unpaid"
                    updated_orders.append({"id": so_name, "payment_status": p_status})

    sales_team = frappe.db.sql("""
        SELECT sales_person FROM `tabSales Team`
        WHERE parent = %s AND parenttype = 'Customer'
    """, (party,), as_dict=True)

    if not sales_team:
        return

    affected_sales_persons = set([row.sales_person for row in sales_team if row.sales_person])
    if not affected_sales_persons:
        return

    affected_emails = set()
    format_affected = ','.join(['%s'] * len(affected_sales_persons))

    affected_coords = frappe.db.sql(f"""
        SELECT lft, rgt FROM `tabSales Person` WHERE name IN ({format_affected})
    """, tuple(affected_sales_persons), as_dict=True)

    if affected_coords:
        or_conditions = [f"(lft <= {c.lft} AND rgt >= {c.rgt})" for c in affected_coords]
        where_clause = " OR ".join(or_conditions)

        ancestor_sps = frappe.db.sql(f"""
            SELECT name, employee FROM `tabSales Person` WHERE {where_clause}
        """, as_dict=True)

        for sp in ancestor_sps:
            if sp.employee:
                user_email = frappe.db.get_value("Employee", sp.employee, "user_id")
                if user_email:
                    affected_emails.add(user_email)
                elif "@" in sp.employee:
                    affected_emails.add(sp.employee)

    if affected_emails:
        try:
            requests.post(
                "https://crystal-api.crystalapps.dev/telemetry/force-app-refresh",
                json={
                    "emails": list(affected_emails),
                    "command": "PAYMENT_RECEIVED",
                    "customer_id": party,
                    "invoice_ids": invoice_ids,
                    "updated_orders": updated_orders,
                    "increment_collection": increment_collection
                },
                timeout=3
            )
        except Exception:
            pass


def trigger_order_status_update(doc, method=None):
    """
    Fires on every Sales Order save (on_update hook). Pushes a live
    status/payment/rejection update to every rep who can see this order —
    the order's own creator AND anyone whose Sales Person hierarchy covers
    the reps on the order's own Sales Team (the same attribution
    trigger_cache_eviction_and_notify already uses) — not just doc.owner.
    Without this widening, a team lead watching a subordinate's order from
    the newly sales-person-scoped Uploaded Orders list (Batch 4) would only
    get the live push if they personally created the order, and would
    otherwise have to wait for the next silent 30-minute vault sync to see
    a Finance rejection appear.
    """
    affected_emails = set()
    if doc.owner and "@" in doc.owner:
        affected_emails.add(doc.owner)

    for row in doc.get("sales_team", []):
        if row.sales_person:
            _add_sp_and_ancestors(row.sales_person, affected_emails)

    if not affected_emails:
        return

    payment_status = "Unpaid"
    invoice_id = None

    invoices = frappe.db.sql("""
        SELECT s.name as invoice_id, s.outstanding_amount, s.grand_total
        FROM `tabSales Invoice Item` si
        JOIN `tabSales Invoice` s ON si.parent = s.name
        WHERE si.sales_order = %s AND s.docstatus = 1
    """, (doc.name,), as_dict=True)

    if invoices:
        invoice_id = invoices[0].invoice_id
        total_grand = sum(i.get('grand_total', 0) for i in invoices)
        total_out = sum(i.get('outstanding_amount', 0) for i in invoices)
        if total_out <= 0:
            payment_status = "Paid"
        elif total_out < total_grand:
            payment_status = "Partially Paid"
        else:
            payment_status = "Unpaid"

    # 🚨 Finance rejection reason — defensive has_column check (same pattern
    # as get_sales_context) so this hook never breaks on a site that hasn't
    # run the one-time create_custom_field step yet.
    rejection_reason = None
    if frappe.db.has_column("Sales Order", "custom_finance_rejection_reason"):
        rejection_reason = doc.get("custom_finance_rejection_reason")

    try:
        requests.post(
            "https://crystal-api.crystalapps.dev/telemetry/force-app-refresh",
            json={
                "emails": list(affected_emails),
                "command": "UPDATE_ORDER_STATUS",
                "order_id": doc.name,
                "status": doc.status,
                "payment_status": payment_status,
                "invoice_id": invoice_id,
                "rejection_reason": rejection_reason
            },
            timeout=3
        )
    except Exception as e:
        frappe.log_error(title="App Order Status Trigger Failed", message=str(e))

def trigger_sales_person_update(doc, method=None):
    """
    🚨 NEW HOOK: Triggered on Sales Person update.
    Checks if targets changed, and forces a silent background vault sync for that specific rep.
    """
    old_doc = doc.get_doc_before_save()
    if not old_doc:
        return

    targets_changed = (
        doc.get("custom_sales_target") != old_doc.get("custom_sales_target") or
        doc.get("custom_collections_target") != old_doc.get("custom_collections_target")
    )

    if not targets_changed:
        return

    if not doc.employee:
        return

    user_email = frappe.db.get_value("Employee", doc.employee, "user_id")
    if not user_email:
        return

    try:
        requests.post(
            "https://crystal-api.crystalapps.dev/telemetry/force-app-refresh",
            json={
                "emails": [user_email],
                "command": "FORCE_VAULT_SYNC"
            },
            timeout=3
        )
    except Exception as e:
        frappe.log_error(title="App Sales Person Trigger Failed", message=str(e))

def _add_sp_and_ancestors(sales_person, affected_emails):
    sp_doc = frappe.db.get_value("Sales Person", sales_person, ["lft", "rgt"], as_dict=True)
    if not sp_doc: return
    ancestors = frappe.db.sql("SELECT employee FROM `tabSales Person` WHERE lft <= %s AND rgt >= %s", (sp_doc.lft, sp_doc.rgt), as_dict=True)
    for a in ancestors:
        if a.employee:
            user_email = frappe.db.get_value("Employee", a.employee, "user_id")
            if user_email: affected_emails.add(user_email)
            elif "@" in a.employee: affected_emails.add(a.employee)

def _get_all_sales_rep_emails():
    emails = set()
    reps = frappe.db.sql("""
        SELECT e.user_id FROM `tabSales Person` sp
        JOIN `tabEmployee` e ON sp.employee = e.name
        WHERE sp.enabled = 1 AND e.user_id IS NOT NULL AND e.user_id != ''
    """, as_dict=True)
    for r in reps:
        if r.user_id: emails.add(r.user_id)

    fallback = frappe.db.sql("""
        SELECT employee FROM `tabSales Person`
        WHERE enabled = 1 AND employee LIKE '%@%'
    """, as_dict=True)
    for f in fallback:
        emails.add(f.employee)
    return list(emails)

def trigger_cache_eviction_and_notify(doc, method=None):

    try:
        if getattr(frappe.flags, 'in_import', False):
            return

        if hasattr(doc, 'docstatus') and doc.docstatus == 0 and doc.doctype != "Customer":
            return

        # 🚨 BATCH 0 FIX: "Sales Person" moved into bulk_doctypes.
        # Sales Person is a NestedSet doctype (lft/rgt). Every save runs
        # Frappe's NestedSet.on_update -> update_nsm, which can renumber
        # lft/rgt for OTHER nodes in the same tree scope (siblings, not just
        # the saved doc) — most visibly when toggling `is_group`. The old
        # narrow branch below only evicted the cache for the single employee
        # tied to the saved doc, so every other rep whose lft/rgt silently
        # shifted kept a stale cached vault (up to the 5-min Redis TTL, or up
        # to 30 min on the mobile app's silent resync). Treating this as a
        # global debounce — same as the other bulk_doctypes — guarantees
        # every authorized-customer-range calculation gets recomputed fresh
        # on the next sync, regardless of whose range actually moved.
        # 🚨 BATCH 4: "Price List" added — toggling enabled/selling on a
        # Price List doctype (e.g. retiring an old list, or flipping a new
        # one live) must invalidate every rep's cached vault immediately,
        # exactly like an Item Price change already does. Without this, a
        # newly-enabled or newly-disabled price list wouldn't be reflected
        # in the app until the next unrelated cache-evicting event or the
        # 30-minute silent sync loop caught up.
        bulk_doctypes = ["Item", "Item Price", "Price List", "Stock Entry", "Stock Reconciliation", "Purchase Receipt", "Delivery Note", "Customer Group", "Territory", "Currency", "Tax Category", "Sales Person"]

        if doc.doctype in bulk_doctypes:
            if doc.doctype == "Item Price" and doc.price_list not in ["Nairobi Prices", "Other Regions"]:
                return
            frappe.cache().set_value('nexus_needs_sync', True)
            return

        affected_emails = set()

        if doc.doctype == "Customer":
            for row in doc.get("sales_team", []):
                if row.sales_person: _add_sp_and_ancestors(row.sales_person, affected_emails)
            if doc.name and frappe.db.exists("Customer", doc.name):
                old_team = frappe.db.get_all("Sales Team", filters={"parent": doc.name, "parenttype": "Customer"}, fields=["sales_person"])
                for old_row in old_team:
                    if old_row.get("sales_person"): _add_sp_and_ancestors(old_row["sales_person"], affected_emails)

        elif doc.doctype in ["Sales Order", "Sales Invoice", "Payment Entry"]:
            customer_field = doc.party if doc.doctype == "Payment Entry" else doc.customer
            if customer_field:
                sales_team = frappe.db.sql("SELECT sales_person FROM `tabSales Team` WHERE parent=%s AND parenttype='Customer'", (customer_field,), as_dict=True)
                for row in sales_team:
                    if row.sales_person: _add_sp_and_ancestors(row.sales_person, affected_emails)
            if doc.owner and "@" in doc.owner:
                affected_emails.add(doc.owner)

        if not affected_emails:
            return

        frappe.enqueue(
            "nexus_supply_chain.api.execute_fastapi_webhook",
            queue="short",
            affected_emails=list(affected_emails),
            doctype=doc.doctype,
            docname=doc.name,
            command="FORCE_VAULT_SYNC",
            enqueue_after_commit=True
        )

    except Exception as e:
        frappe.log_error(title="Nexus Cache Eviction Flag Failed", message=f"Doctype: {doc.doctype}, Error: {str(e)}")

def execute_fastapi_webhook(affected_emails, doctype, docname, command, extra=None):
    """
    Background job: resolves FCM tokens for the affected users and posts one
    cache-invalidation/notification to FastAPI. `extra` carries optional
    context the app can act on instantly (e.g. lead + lead_status); the core
    keys always win, so extra can never overwrite command/emails.
    """
    import requests
    import frappe

    try:
        fcm_tokens = {}
        if affected_emails:
            format_emails = ','.join(['%s'] * len(affected_emails))
            tokens_data = frappe.db.sql(f"""
                SELECT user, fcm_token
                FROM `tabNexus FCM Device`
                WHERE user IN ({format_emails})
            """, tuple(affected_emails), as_dict=True)

            for row in tokens_data:
                if row.user not in fcm_tokens:
                    fcm_tokens[row.user] = []
                fcm_tokens[row.user].append(row.fcm_token)

        body = dict(extra or {})
        body.update({
            "emails": affected_emails,
            "fcm_tokens": fcm_tokens,
            "doctype": doctype,
            "docname": docname,
            "command": command
        })

        requests.post(
            "https://crystal-api.crystalapps.dev/api/v1/cache/invalidate",
            json=body,
            timeout=5
        )
    except Exception as e:
        frappe.log_error(title="Cache Eviction API Failed", message=str(e))


def _add_user_and_managers(user_email, affected_emails):
    """Adds a user plus everyone above them in the Sales Person tree."""
    if not user_email or user_email in ("Administrator", "Guest"):
        return
    affected_emails.add(user_email)
    sp = get_root_sales_person(user_email)
    if sp:
        _add_sp_and_ancestors(sp, affected_emails)


def trigger_lead_refresh(doc, method=None):
    """
    Lead on_update / on_trash hook. Notifies the lead owner and their
    managers (plus the PREVIOUS owner when ownership moved, so the lead
    leaves their list) — but only when a field the app actually uses
    changed. Sent as FORCE_VAULT_SYNC, which current app builds already
    handle; `lead` / `lead_status` / `lead_event` let newer builds update the
    lead instantly. Queued after commit, so a failed save never notifies.
    """
    try:
        if getattr(frappe.flags, "in_import", False):
            frappe.cache().set_value('nexus_needs_sync', True)
            return

        is_delete = method == "on_trash"
        before = doc.get_doc_before_save()

        if not is_delete and before is not None:
            if not any(before.get(f) != doc.get(f) for f in NEXUS_LEAD_SYNC_FIELDS):
                return

        affected = set()
        _add_user_and_managers(doc.get("lead_owner"), affected)
        if before is not None and before.get("lead_owner") != doc.get("lead_owner"):
            _add_user_and_managers(before.get("lead_owner"), affected)

        if not affected:
            return

        frappe.enqueue(
            "nexus_supply_chain.api.execute_fastapi_webhook",
            queue="short",
            affected_emails=list(affected),
            doctype="Lead",
            docname=doc.name,
            command="FORCE_VAULT_SYNC",
            extra={
                "lead": doc.name,
                "lead_status": doc.get("status"),
                "lead_event": "deleted" if is_delete else "updated",
            },
            enqueue_after_commit=True
        )
    except Exception as e:
        frappe.log_error(title="Nexus Lead Refresh Trigger Failed", message=f"Lead {doc.name}: {e}")


def handle_lead_conversion(doc, method=None):
    """
    Customer on_update hook. Fires once, when a Customer is created from a
    Lead (lead_name newly set). ERPNext marks the Lead 'Converted' with a
    direct DB write that fires no Lead hooks, so conversion is detected
    here instead. It:
      1. closes every still-open visit on that lead (check-out time,
         duration, closed_by_conversion=1, lead_status_after='Converted');
      2. sends LEAD_CONVERTED to the lead owner, their managers and any rep
         who was checked in, so the app closes the lead window.
    Writes are part of the Customer's own transaction (rolled back with it);
    the notification is queued after commit.
    """
    lead = doc.get("lead_name")
    if not lead or not doc.has_value_changed("lead_name"):
        return

    try:
        lead_row = frappe.db.get_value("Lead", lead, ["lead_owner"], as_dict=True)
        if not lead_row:
            return

        affected = set()
        closed_visit_ids = []

        if frappe.db.has_column("Nexus Sales Visit", "lead"):
            open_visits = frappe.db.sql("""
                SELECT name, sales_person, check_in_time
                FROM `tabNexus Sales Visit`
                WHERE visit_type = 'Lead' AND lead = %s
                AND (check_out_time IS NULL OR check_out_time = '')
            """, (lead,), as_dict=True)

            now = frappe.utils.now_datetime()
            for v in open_visits:
                duration = 0.0
                if v.check_in_time:
                    duration = round((get_datetime(now) - get_datetime(v.check_in_time)).total_seconds() / 60, 2)
                frappe.db.set_value("Nexus Sales Visit", v.name, {
                    "check_out_time": now,
                    "duration_minutes": duration,
                    "closed_by_conversion": 1,
                    "lead_status_after": NEXUS_LEAD_CONVERTED_STATUS,
                }, update_modified=False)
                closed_visit_ids.append(v.name)
                _add_user_and_managers(v.sales_person, affected)

        _add_user_and_managers(lead_row.lead_owner, affected)
        if not affected:
            return

        frappe.enqueue(
            "nexus_supply_chain.api.execute_fastapi_webhook",
            queue="short",
            affected_emails=list(affected),
            doctype="Lead",
            docname=lead,
            command="LEAD_CONVERTED",
            extra={
                "lead": lead,
                "customer": doc.name,
                "customer_name": doc.get("customer_name"),
                "closed_visit_ids": closed_visit_ids,
            },
            enqueue_after_commit=True
        )
    except Exception as e:
        frappe.log_error(title="Nexus Lead Conversion Hook Failed", message=f"Customer {doc.name} / Lead {lead}: {e}")

@frappe.whitelist()
def create_mobile_customer(payload):

    if isinstance(payload, str):
        payload = json.loads(payload)

    mobile_no = payload.get("mobile_no")
    phone_number = payload.get("phone_number")
    location_text = payload.get("location_text")
    customer_name = payload.get("customer_name")

    if not customer_name:
        return {"status": "error", "message": "Customer name is required."}

    try:
        doc = frappe.new_doc("Customer")
        doc.customer_name = customer_name
        doc.customer_type = payload.get("customer_type", "Company")
        doc.customer_group = payload.get("customer_group", "Commercial")
        doc.territory = payload.get("territory", "All Territories")
        doc.default_price_list = payload.get("default_price_list", "Standard Selling")
        doc.default_currency = payload.get("billing_currency", "KES")
        doc.tax_id = payload.get("tax_id")
        doc.tax_category = payload.get("tax_category")
        doc.payment_terms = payload.get("payment_terms")

        # 🚨 Mobile Number and Phone Number are now DISTINCT ERPNext fields
        # (previously both were force-written from a single "phone_number"
        # input, which discarded whichever number the rep entered second).
        #   mobile_no      -> Customer.mobile_no       (native ERPNext field)
        #   phone_number   -> Customer.custom_phone_number (day-to-day field)
        if mobile_no:
            doc.mobile_no = mobile_no
        if phone_number:
            doc.custom_phone_number = phone_number

        if location_text:
            doc.custom_location = location_text

        lat = payload.get("latitude") or payload.get("lat")
        lng = payload.get("longitude") or payload.get("lng")

        if lat and lng:
            doc.custom_latitude = str(lat)
            doc.custom_longitude = str(lng)

        if payload.get("custom_combined_coordinates"):
            doc.custom_combined_coordinates = payload.get("custom_combined_coordinates")

        if payload.get("google_maps_link"):
            doc.custom_google_maps_link = payload.get("google_maps_link")

        sales_person = payload.get("sales_person") or get_root_sales_person(frappe.session.user)
        if sales_person:
            doc.append("sales_team", {
                "sales_person": sales_person,
                "allocated_percentage": 100
            })

        doc.insert(ignore_permissions=True)
        frappe.db.commit()

        customer_id = doc.name

        return {"status": "success", "customer_id": customer_id, "message": f"Customer {customer_id} created successfully."}

    except Exception as e:
        frappe.log_error(title="Mobile Customer Creation Failed", message=str(e))
        frappe.db.rollback()
        return {"status": "error", "message": f"Failed to create customer: {str(e)}"}

def _update_party_coordinates(party_type, party, latitude, longitude, custom_combined_coordinates=None,
                              google_maps_link=None, visit_id=None, location_source=None):
    """
    Shared implementation behind update_customer_coordinates and
    update_lead_coordinates. Direct db.set_value (bypasses doc.save() to
    avoid recursive webhook loops), then SERVER-AUTHORITATIVE re-evaluation
    of the caller's open visit for this party, then a timeline comment
    recording old pin -> new pin.

    The party update always succeeds independently of the visit
    re-evaluation. Any unexpected failure rolls back the whole request, so a
    half-written change is never committed.
    """
    try:
        if party_type not in NEXUS_VISIT_PARTY_FIELDS or not party or not frappe.db.exists(party_type, party):
            return {"status": "error", "message": f"{party_type} not found."}

        try:
            lat_float = float(latitude)
            lng_float = float(longitude)
        except (TypeError, ValueError) as e:
            frappe.log_error(
                title="Coord Update: Invalid Values",
                message=f"{party_type} {party} received non-numeric coords: lat={latitude}, lng={longitude} | {e}"
            )
            return {"status": "error", "message": f"Invalid coordinate values: {e}"}

        if not (-90.0 <= lat_float <= 90.0) or not (-180.0 <= lng_float <= 180.0):
            frappe.log_error(
                title="Coord Update: Out of Bounds",
                message=f"{party_type} {party}: lat={lat_float}, lng={lng_float} are outside geographic bounds."
            )
            return {"status": "error", "message": "Coordinates out of valid geographic range."}

        geo_fields = [f for f in ("custom_combined_coordinates", "custom_latitude",
                                  "custom_longitude", "custom_google_maps_link")
                      if frappe.db.has_column(party_type, f)]

        # 🚨 Capture the PREVIOUS location before overwriting. db.set_value
        # bypasses Version tracking, so the timeline comment is the only
        # place the old pin survives.
        previous = frappe.db.get_value(party_type, party, geo_fields, as_dict=True) if geo_fields else None
        previous = previous or frappe._dict()
        previous_coords = parse_combined_coords(
            previous.get("custom_combined_coordinates"),
            previous.get("custom_latitude"),
            previous.get("custom_longitude")
        )

        update_dict = {}
        if "custom_latitude" in geo_fields:
            update_dict["custom_latitude"] = lat_float
        if "custom_longitude" in geo_fields:
            update_dict["custom_longitude"] = lng_float
        if custom_combined_coordinates and "custom_combined_coordinates" in geo_fields:
            update_dict["custom_combined_coordinates"] = custom_combined_coordinates
        if google_maps_link and "custom_google_maps_link" in geo_fields:
            update_dict["custom_google_maps_link"] = google_maps_link

        if not update_dict:
            return {"status": "error", "message": f"The {party_type} doctype has no GeoLocation fields to update."}

        frappe.db.set_value(party_type, party, update_dict, update_modified=False)

        acting_user = frappe.form_dict.get("acting_user") or frappe.session.user
        normalized_source = location_source if location_source in NEXUS_LOCATION_SOURCES else None

        # 🚨 VISIT RE-EVALUATION — isolated so a failure here can never undo
        # the party location save above.
        visit_result = {
            "visit_updated": False,
            "visit_id": None,
            "visit_distance_m": None,
            "is_on_site": None,
            "original_distance_m": None,
            "previous_distance_m": None,
            "visit_message": None,
        }
        try:
            visit_row, reason = _resolve_open_visit_for_correction(party_type, party, frappe.session.user, visit_id)
            if visit_row:
                visit_result.update(
                    _apply_visit_location_correction(visit_row, (lat_float, lng_float), normalized_source)
                )
            else:
                visit_result["visit_message"] = reason
        except Exception as ve:
            frappe.log_error(
                title="Visit Distance Recompute Failed",
                message=f"{party_type} {party}, visit_id={visit_id}, user={frappe.session.user}: {ve}"
            )
            visit_result["visit_message"] = f"{party_type} location saved, but this visit's distance could not be recomputed."

        comment_lines = [
            f"📍 Location updated via <b>Nexus Sales App</b> by <b>{_html_escape(str(acting_user))}</b>",
            (f"Previous: <b>{previous_coords[0]}, {previous_coords[1]}</b>"
             if previous_coords else "Previous: <i>no coordinates recorded</i>"),
            f"New — Latitude: <b>{lat_float}</b> | Longitude: <b>{lng_float}</b>",
        ]
        if custom_combined_coordinates:
            comment_lines.append(f"Combined: <b>{_html_escape(str(custom_combined_coordinates))}</b>")
        if normalized_source:
            comment_lines.append(f"Method: <b>{normalized_source}</b>")
        if google_maps_link:
            comment_lines.append(f"Source link: {_html_escape(str(google_maps_link))}")
        if visit_result["visit_updated"]:
            prev_d = visit_result.get("previous_distance_m")
            prev_str = f"{flt(prev_d):,.0f} m" if prev_d is not None else "none"
            comment_lines.append(
                f"Visit <b>{visit_result['visit_id']}</b> distance recomputed: {prev_str} → "
                f"<b>{flt(visit_result['visit_distance_m']):,.0f} m</b> "
                f"({'On-Site' if visit_result['is_on_site'] else 'Off-Site'})"
            )

        frappe.get_doc({
            "doctype": "Comment",
            "comment_type": "Info",
            "reference_doctype": party_type,
            "reference_name": party,
            "content": "<br>".join(comment_lines),
            "comment_by": acting_user,
        }).insert(ignore_permissions=True)

        frappe.db.commit()

        frappe.logger().info(
            f"[Nexus Geocode] update_{party_type.lower()}_coordinates: {party} → "
            f"lat={lat_float}, lng={lng_float} by {acting_user} | "
            f"visit={visit_result.get('visit_id')} updated={visit_result.get('visit_updated')} "
            f"dist={visit_result.get('visit_distance_m')}"
        )

        return {
            "status": "success",
            "message": "Location updated successfully.",
            "party_type": party_type,
            "lat": lat_float,
            "lng": lng_float,
            "visit_updated": visit_result["visit_updated"],
            "visit_id": visit_result["visit_id"],
            "visit_distance_m": visit_result["visit_distance_m"],
            "is_on_site": visit_result["is_on_site"],
            "original_distance_m": visit_result["original_distance_m"],
            "visit_message": visit_result["visit_message"],
        }

    except Exception as e:
        frappe.db.rollback()
        frappe.log_error(title=f"{party_type} Location Update Failed", message=str(e))
        return {"status": "error", "message": str(e)}


@frappe.whitelist()
def update_customer_coordinates(customer, latitude, longitude, custom_combined_coordinates=None, google_maps_link=None, visit_id=None, location_source=None):
    """Customer location correction. Signature and response unchanged; see _update_party_coordinates."""
    return _update_party_coordinates(
        "Customer", customer, latitude, longitude,
        custom_combined_coordinates, google_maps_link, visit_id, location_source
    )


@frappe.whitelist()
def update_lead_coordinates(lead, latitude, longitude, custom_combined_coordinates=None, google_maps_link=None, visit_id=None, location_source=None):
    """
    Lead location correction (GPS Snap or Maps Link). Writes the Lead's four
    custom GeoLocation fields, adds an old pin -> new pin comment to the
    Lead's timeline, and recomputes the open lead visit's distance with the
    same audit trail as customers. Same scope rules as lead check-in;
    converted leads are refused (their pin now belongs to the customer).
    """
    if not lead or not frappe.db.exists("Lead", lead):
        return {"status": "error", "code": "LEAD_NOT_FOUND", "message": "This lead no longer exists."}

    lead_row = frappe.db.get_value("Lead", lead, ["status", "lead_owner"], as_dict=True)
    if lead_row.status == NEXUS_LEAD_CONVERTED_STATUS:
        return {
            "status": "error", "code": "LEAD_CONVERTED",
            "message": "This lead has been converted to a customer. Update the location on the customer instead."
        }
    access_error = _lead_access_error(lead_row.lead_owner, frappe.session.user)
    if access_error:
        return {"status": "error", "code": "LEAD_NOT_ASSIGNED", "message": access_error}

    return _update_party_coordinates(
        "Lead", lead, latitude, longitude,
        custom_combined_coordinates, google_maps_link, visit_id, location_source
    )

@frappe.whitelist()
def register_sales_check_in_correction(visit_id, distance_m=None, location_source=None):
    """
    🚨 HARDENED. Previously any logged-in user could overwrite ANY visit's
    distance with ANY number. Now:
      - distance_m is accepted only so older app builds don't fail on the
        call signature. It is IGNORED.
      - The distance is recomputed on the server from the visit's stored
        check-in point against the customer's CURRENT coordinates.
      - The visit must belong to the calling session and still be open.
    The app's normal path is update_customer_coordinates, which performs
    this same correction itself. Both share _apply_visit_location_correction.
    """
    try:
        if not visit_id or not frappe.db.exists("Nexus Sales Visit", visit_id):
            return {"status": "error", "message": "Active visit record not found."}

        visit = _get_visit_for_correction(visit_id)
        if (visit.sales_person or "").strip().lower() != (frappe.session.user or "").strip().lower():
            return {"status": "error", "message": "You can only correct your own visit."}
        if visit.check_out_time:
            return {"status": "error", "message": "This visit is already checked out and can no longer be corrected."}

        party_type = visit.get("visit_type") or "Customer"
        party_field = NEXUS_VISIT_PARTY_FIELDS.get(party_type, "customer")
        target = _get_party_target_coords(party_type, visit.get(party_field))
        if not target:
            label = party_type.lower()
            return {"status": "error", "message": f"The {label} has no coordinates yet. Update the {label}'s location first."}

        normalized_source = location_source if location_source in NEXUS_LOCATION_SOURCES else None
        result = _apply_visit_location_correction(visit, target, normalized_source)
        frappe.db.commit()

        if not result["visit_updated"]:
            return {"status": "error", "message": result.get("visit_message") or "Distance could not be recomputed.", **result}
        return {"status": "success", "message": "Distance recomputed on the server.", **result}

    except Exception as e:
        frappe.log_error(title="Distance Correction Failed", message=str(e))
        return {"status": "error", "message": str(e)}


@frappe.whitelist()
def backfill_snap_corrected_visits(dry_run=1, since=None):
    """
    🚨 ONE-OFF CLEANUP for visits recorded before the visit_id fix, when
    corrections were silently dropped (e.g. f856j0rhk7 / CUS-04582).

    Targets only the unambiguous case: the customer's CURRENT pin is a GPS
    snap that sits within 1 m of the visit's own check-in point, yet the
    visit still stores a distance > 100 m. That combination can only arise
    from a snap made during that visit whose correction was lost.

    Callable WITHOUT bench, from the ERPNext desk as a System Manager
    (browser DevTools console):
      Dry run:  frappe.call({method: 'nexus_supply_chain.api.backfill_snap_corrected_visits', args: {dry_run: 1}}).then(r => console.log(r.message))
      Apply:    frappe.call({method: 'nexus_supply_chain.api.backfill_snap_corrected_visits', args: {dry_run: 0}}).then(r => console.log(r.message))
    Optional arg: since: '2026-09-01' to limit by check-in date.
    Still works from bench too:
      bench --site <site> execute nexus_supply_chain.api.backfill_snap_corrected_visits --kwargs "{'dry_run': 1}"

    Results are RETURNED (not printed) so they're visible in the browser.
    location_corrected_at is stamped with the time the backfill runs; the
    real correction moment is in the Customer's timeline comment.
    """
    frappe.only_for("System Manager")
    dry_run = cint(dry_run)

    # A real (writing) run must be an explicit POST, never a GET that a
    # browser could prefetch or replay by accident. bench execute has no
    # request object, so it's unaffected.
    request = getattr(frappe.local, "request", None)
    if not dry_run and request is not None and request.method != "POST":
        frappe.throw("A real (non-dry) backfill run must be sent as a POST request.")

    not_yet_corrected = (
        "AND IFNULL(v.location_corrected, 0) = 0"
        if frappe.db.has_column("Nexus Sales Visit", "location_corrected") else ""
    )
    since_clause = "AND DATE(v.check_in_time) >= %(since)s" if since else ""

    rows = frappe.db.sql(f"""
        SELECT v.name, v.customer, v.check_in_time, v.latitude, v.longitude,
               v.distance_from_target_meters,
               c.custom_combined_coordinates, c.custom_latitude, c.custom_longitude,
               c.custom_google_maps_link
        FROM `tabNexus Sales Visit` v
        JOIN `tabCustomer` c ON c.name = v.customer
        WHERE v.distance_from_target_meters > %(threshold)s
        {not_yet_corrected}
        {since_clause}
    """, {"threshold": NEXUS_ON_SITE_THRESHOLD_METERS, "since": since}, as_dict=True)

    candidates = []
    for r in rows:
        if (r.custom_google_maps_link or "").strip() != NEXUS_GPS_SNAP_LINK_LABEL:
            continue
        checkin_point = parse_combined_coords(None, r.latitude, r.longitude)
        cust_point = parse_combined_coords(r.custom_combined_coordinates, r.custom_latitude, r.custom_longitude)
        if not checkin_point or not cust_point:
            continue
        if haversine_meters(checkin_point[0], checkin_point[1], cust_point[0], cust_point[1]) < 1.0:
            candidates.append({
                "visit_id": r.name,
                "customer": r.customer,
                "check_in_time": str(r.check_in_time) if r.check_in_time else None,
                "stored_distance_m": round(flt(r.distance_from_target_meters), 2),
                "recompute_against": f"{cust_point[0]},{cust_point[1]}",
                "_target": cust_point,
            })

    applied = []
    if not dry_run and candidates:
        for c in candidates:
            visit = _get_visit_for_correction(c["visit_id"])
            result = _apply_visit_location_correction(visit, c["_target"], "GPS Snap")
            applied.append({
                "visit_id": c["visit_id"],
                "updated": result["visit_updated"],
                "new_distance_m": result["visit_distance_m"],
                "original_distance_m": result["original_distance_m"],
                "message": result["visit_message"],
            })
        frappe.db.commit()

    for c in candidates:
        c.pop("_target", None)

    frappe.logger().info(
        f"[Nexus Backfill] qualifying={len(candidates)} dry_run={dry_run} "
        f"applied={len(applied)} by {frappe.session.user}"
    )

    return {
        "dry_run": dry_run,
        "qualifying": len(candidates),
        "candidates": candidates,
        "applied": applied,
    }

@frappe.whitelist()
def submit_visit_report(visit_id=None, customer_id=None, outcome=None, notes=None, next_follow_up_date=None, competitor_notes=None, collections_report=None):
    """
    Writes the rep's post-visit report onto the Nexus Sales Visit record
    created at check-in (see register_sales_check_in). Uses direct
    db.set_value — same "Strategy D" pattern as update_customer_coordinates —
    to avoid a full doc.save() cycle and any recursive on_change webhook loop
    on a doctype that already has heavy hooks wired to it elsewhere.

    🚨 RESOLUTION ORDER (visit_id is now a fast-path, not a hard requirement):
      1. If visit_id is supplied AND exists, use it directly.
      2. Otherwise, if customer_id is supplied, resolve the CALLING REP'S
         OWN most recent Nexus Sales Visit for that customer. This closes
         the race window where the app's optimistic check-in flow (visit_id
         is patched into state only after ERPNext's background response
         returns) hasn't yet delivered a visit_id by the time the rep taps
         Submit Report — previously this hard-failed with "doesn't have a
         trackable check-in record yet" even though a real, valid check-in
         had just been recorded seconds earlier.
      3. If neither resolves to a real record, fail clearly.

    🚨 SINGLE-SUBMISSION GUARD: once a visit already has visit_with_report=1,
    further submissions against that same visit are rejected outright rather
    than silently overwriting the rep's original report — a visit report is
    a point-in-time record, not an editable draft.

    Defensive has_column checks mean this never hard-fails on a site that
    hasn't run the one-time create_custom_field step yet — it just silently
    skips the fields that don't exist, so a stale mobile build talking to a
    fresh backend (or vice versa) never crashes the request.
    """
    target_visit_id = visit_id if (visit_id and frappe.db.exists("Nexus Sales Visit", visit_id)) else None

    if not target_visit_id:
        if not customer_id:
            return {"status": "error", "message": "Unable to identify this visit — please check in again and retry."}

        resolved = frappe.db.sql("""
            SELECT name FROM `tabNexus Sales Visit`
            WHERE sales_person = %s AND customer = %s
            ORDER BY creation DESC LIMIT 1
        """, (frappe.session.user, customer_id), as_dict=True)

        if not resolved:
            return {"status": "error", "message": "No check-in record found for this customer. Please check in again."}

        target_visit_id = resolved[0].name

    # Ownership check: a rep can only file a report against their own visit
    visit_owner = frappe.db.get_value("Nexus Sales Visit", target_visit_id, "sales_person")
    if visit_owner and visit_owner.lower() != frappe.session.user.lower():
        return {"status": "error", "message": "You can only submit a report for your own visit."}

    # 🚨 SINGLE-SUBMISSION GUARD — never overwrite an existing report.
    if frappe.db.has_column("Nexus Sales Visit", "visit_with_report"):
        already_reported = frappe.db.get_value("Nexus Sales Visit", target_visit_id, "visit_with_report")
        if already_reported:
            return {"status": "error", "message": "A report has already been submitted for this visit."}

    update_dict = {}
    has_real_content = False

    if outcome and frappe.db.has_column("Nexus Sales Visit", "visit_outcome"):
        update_dict["visit_outcome"] = outcome
        has_real_content = True
    if notes and frappe.db.has_column("Nexus Sales Visit", "visit_notes"):
        update_dict["visit_notes"] = notes
        has_real_content = True
    if next_follow_up_date and frappe.db.has_column("Nexus Sales Visit", "next_follow_up_date"):
        update_dict["next_follow_up_date"] = next_follow_up_date
        has_real_content = True
    if competitor_notes and frappe.db.has_column("Nexus Sales Visit", "competitor_notes"):
        update_dict["competitor_notes"] = competitor_notes
        has_real_content = True
    if collections_report and frappe.db.has_column("Nexus Sales Visit", "collections_report"):
        update_dict["collections_report"] = collections_report
        has_real_content = True

    if not has_real_content:
        return {"status": "error", "message": "Please fill in at least one field before submitting."}

    # 🚨 Auto-flag: visit_with_report is ALWAYS set when real content was submitted.
    if frappe.db.has_column("Nexus Sales Visit", "visit_with_report"):
        update_dict["visit_with_report"] = 1

    # 🚨 Auto-flag: is_collections_report is ONLY set when the collections flow was used.
    if collections_report and frappe.db.has_column("Nexus Sales Visit", "is_collections_report"):
        update_dict["is_collections_report"] = 1

    if frappe.db.has_column("Nexus Sales Visit", "report_submitted_at"):
        update_dict["report_submitted_at"] = frappe.utils.now_datetime()

    try:
        frappe.db.set_value("Nexus Sales Visit", target_visit_id, update_dict, update_modified=False)
        frappe.db.commit()
        return {"status": "success", "message": "Visit report submitted successfully."}
    except Exception as e:
        frappe.log_error(title="Visit Report Submission Failed", message=str(e))
        return {"status": "error", "message": str(e)}

def trigger_post_import_cache_eviction(doc, method=None):
    """
    🚨 BULK IMPORT SWEEPER: Fires once after a Frappe v15 Data Import completes.
    Simply sets the debounce flag to let the 1-minute orchestrator handle it safely.
    """
    try:
        if doc.status not in ["Success", "Partial Success"]:
            return

        target_doctypes = ["Customer", "Lead", "Item", "Item Price", "Customer Group", "Territory", "Currency", "Tax Category"]
        if doc.reference_doctype not in target_doctypes:
            return

        if not doc.has_value_changed("status"):
            return

        frappe.cache().set_value('nexus_needs_sync', True)
        frappe.log_error(title="Nexus Bulk Import Sweep", message=f"Successfully flagged debounce sync for {doc.reference_doctype} import.")

    except Exception as e:
        frappe.log_error(title="Nexus Post-Import Eviction Failed", message=f"Error: {str(e)}")

def publish_catalog_update(doc, method):
    frappe.publish_realtime('nexus_catalog_sync', message={'status': 'updated'})

def process_debounced_cache_eviction():
    """
    Scheduled Orchestrator: Runs every 1 minute.
    Reads the Redis debounce flag. If True, fires a single lightweight webhook to FastAPI.
    FastAPI will handle the heavy lifting of tree calculations and FCM pushes.
    """
    import requests
    import frappe

    try:
        if frappe.cache().get_value('nexus_needs_sync'):
            requests.post(
                "https://crystal-api.crystalapps.dev/api/v1/cache/invalidate",
                json={
                    "command": "GLOBAL_DEBOUNCED_SYNC",
                    "doctype": "System",
                    "docname": "Scheduled Sync"
                },
                timeout=5
            )

            # Reset the flag after successfully notifying FastAPI
            frappe.cache().set_value('nexus_needs_sync', False)
    except Exception as e:
        frappe.log_error(title="Scheduled Orchestrator Sync Failed", message=str(e))

@frappe.whitelist()
def get_active_companies_for_dispatch():
    """
    Fetches all companies with valid GPS coordinates to serve as the final
    yard/destination for returning drivers.
    """
    try:
        companies = frappe.db.sql("""
            SELECT name, custom_latitude, custom_longitude
            FROM `tabCompany`
            WHERE custom_latitude IS NOT NULL AND custom_longitude IS NOT NULL
            AND custom_latitude != '' AND custom_longitude != ''
        """, as_dict=True)

        return {"status": "success", "data": companies}
    except Exception as e:
        frappe.log_error(title="Company Coordinates Fetch Failed", message=str(e))
        return {"status": "error", "message": str(e)}
