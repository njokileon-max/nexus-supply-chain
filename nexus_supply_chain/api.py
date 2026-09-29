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
    ("lat,lng", exact text) is the primary source; custom_latitude /
    custom_longitude are the fallback. All three are Data (text) fields on
    Customer and Lead. Rows converted from the old Float columns may hold
    placeholders such as "0.000000000"; a 0,0 pair is treated as "no
    coordinates", so those placeholders never count as a real pin.
    Returns (lat, lng) as floats, or None.
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
# 🚨 COORDINATES AS TEXT
# custom_latitude, custom_longitude and custom_combined_coordinates are Data
# fields on Customer and Lead. Every write goes through build_coordinate_fields
# so the three fields always hold the SAME digits: latitude and longitude are
# split from the exact "lat,lng" text instead of being round-tripped through a
# float. Anything leaving the system as numbers (driver app, optimizer) is
# converted at that boundary with parse_combined_coords.
# ============================================================================
NEXUS_COORD_FIELDS = ("custom_latitude", "custom_longitude", "custom_combined_coordinates")


def _coord_text(value):

    if value is None or isinstance(value, bool):
        return ""
    if isinstance(value, str):
        return value.strip()
    try:
        return repr(float(value))
    except (TypeError, ValueError):
        return ""


def build_coordinate_fields(latitude=None, longitude=None, combined=None):

    lat_txt = lng_txt = ""
    if combined not in (None, ""):
        parts = [p.strip() for p in str(combined).split(",")]
        if len(parts) >= 2:
            lat_txt, lng_txt = parts[0], parts[1]

    if not parse_combined_coords(f"{lat_txt},{lng_txt}"):
        lat_txt, lng_txt = _coord_text(latitude), _coord_text(longitude)

    point = parse_combined_coords(f"{lat_txt},{lng_txt}")
    if not point:
        return None, None

    return {
        "custom_latitude": lat_txt,
        "custom_longitude": lng_txt,
        "custom_combined_coordinates": f"{lat_txt},{lng_txt}",
    }, point


def _existing_columns_only(doctype, fields):

    return {k: v for k, v in (fields or {}).items() if frappe.db.has_column(doctype, k)}

NEXUS_ON_SITE_THRESHOLD_METERS = 100.0
NEXUS_LOCATION_SOURCES = ("GPS Snap", "Maps Link")
NEXUS_GPS_SNAP_LINK_LABEL = "Generated from Sales Native GPS"

NEXUS_VISIT_PARTY_FIELDS = {"Customer": "customer", "Lead": "lead"}
NEXUS_LEAD_CONVERTED_STATUS = "Converted"


def haversine_meters(lat1, lng1, lat2, lng2):

    lat1, lng1, lat2, lng2 = float(lat1), float(lng1), float(lat2), float(lng2)
    dlat = math.radians(lat2 - lat1)
    dlng = math.radians(lng2 - lng1)
    a = (math.sin(dlat / 2) ** 2
         + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlng / 2) ** 2)
    return 6371000.0 * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))

def compute_is_on_site(target_coordinates, distance):

    if not parse_combined_coords(target_coordinates):
        return 0
    if distance is None or distance == "":
        return 0
    return 1 if flt(distance) <= NEXUS_ON_SITE_THRESHOLD_METERS else 0


def _get_party_target_coords(party_type, party):

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

    field = frappe.get_meta("Lead").get_field("status")
    options = [o.strip() for o in (field.options or "").split("\n") if o.strip()] if field else []
    if not include_converted:
        options = [o for o in options if o != NEXUS_LEAD_CONVERTED_STATUS]
    return options


def _lead_access_error(lead_owner, session_user):

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

    return frappe.db.sql("""
        SELECT name, visit_type, customer, lead, party_name, check_in_time
        FROM `tabNexus Sales Visit`
        WHERE sales_person = %s
        AND (check_out_time IS NULL OR check_out_time = '')
        AND DATE(check_in_time) = %s
        ORDER BY creation DESC
    """, (session_user, today()), as_dict=True)

NEXUS_LEAD_SYNC_FIELDS = (
    "lead_name", "company_name", "first_name", "last_name", "job_title",
    "status", "type", "mobile_no", "phone", "whatsapp_no", "email_id",
    "territory", "source", "lead_owner", "custom_location",
    "custom_google_maps_link", "custom_latitude", "custom_longitude", "custom_combined_coordinates",
)


def _sales_person_names_for_emails(emails):

    owners = tuple({(e or "").strip().lower() for e in (emails or []) if e})
    if not owners:
        return {}
    fmt = ','.join(['%s'] * len(owners))

    names = {}
    for r in frappe.db.sql(f"""
        SELECT LOWER(e.user_id) AS email, MIN(sp.sales_person_name) AS sp_name
        FROM `tabSales Person` sp
        JOIN `tabEmployee` e ON sp.employee = e.name
        WHERE LOWER(e.user_id) IN ({fmt})
        GROUP BY LOWER(e.user_id)
    """, owners, as_dict=True):
        names[r.email] = r.sp_name
    for r in frappe.db.sql(f"""
        SELECT LOWER(employee) AS email, MIN(sales_person_name) AS sp_name
        FROM `tabSales Person`
        WHERE LOWER(employee) IN ({fmt})
        GROUP BY LOWER(employee)
    """, owners, as_dict=True):
        names.setdefault(r.email, r.sp_name)
    return names


def _build_lead_rows(where_sql, params):

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
        WHERE {where_sql}
        ORDER BY l.modified DESC
    """, tuple(params), as_dict=True)

    if not rows:
        return []

    sp_name_map = _sales_person_names_for_emails([r.get("lead_owner") for r in rows])
    for row in rows:
        owner = (row.get("lead_owner") or "").strip().lower()
        row["lead_id"] = row["name"]
        row["owning_sales_person_name"] = (
            sp_name_map.get(owner) or row.get("lead_owner_full_name") or row.get("lead_owner")
        )
        row.pop("lead_owner_full_name", None)
    return rows


def get_scoped_leads(target_email):

    owner_emails = {e.strip().lower() for e in get_authorized_sales_emails(target_email) if e}
    if target_email:
        owner_emails.add(target_email.strip().lower())
    if not owner_emails:
        return []

    owners = tuple(owner_emails)
    fmt = ','.join(['%s'] * len(owners))
    return _build_lead_rows(
        f"LOWER(l.lead_owner) IN ({fmt}) AND IFNULL(l.status, '') != %s",
        owners + (NEXUS_LEAD_CONVERTED_STATUS,)
    )


def _get_lead_row(lead_name):
    rows = _build_lead_rows("l.name = %s", (lead_name,))
    return rows[0] if rows else None


NEXUS_LEAD_SYSTEM_STATUSES = ("Converted", "Opportunity", "Quotation", "Lost Quotation")
NEXUS_LEAD_DEFAULT_STATUS = "Lead"

NEXUS_LEAD_FORM_FIELDS = (
    "company_name", "first_name", "last_name", "job_title",
    "mobile_no", "whatsapp_no", "phone", "email_id",
    "territory", "source", "type", "status", "custom_location",
    "custom_google_maps_link", "custom_latitude", "custom_longitude", "custom_combined_coordinates",
)

NEXUS_LEAD_CREATE_RESULT_TTL_SEC = 10 * 60
NEXUS_LEAD_CREATE_LOCK_TTL_SEC = 30


def _select_options(doctype, fieldname):
    field = frappe.get_meta(doctype).get_field(fieldname)
    if not field or field.fieldtype != "Select":
        return []
    return [o.strip() for o in (field.options or "").split("\n") if o.strip()]


def get_lead_creatable_statuses():
    return [o for o in get_lead_status_options(include_converted=True) if o not in NEXUS_LEAD_SYSTEM_STATUSES]


def get_lead_assignable_owners(user_email):

    me = (user_email or "").strip().lower()
    emails = {e.strip().lower() for e in get_authorized_sales_emails(user_email) if e}
    if me:
        emails.add(me)
    if not emails:
        return []

    users = frappe.get_all(
        "User",
        filters={"name": ["in", list(emails)], "enabled": 1},
        fields=["name", "full_name"],
    )
    sp_names = _sales_person_names_for_emails(emails)

    owners = [{
        "email": u.name,
        "label": sp_names.get(u.name.lower()) or u.full_name or u.name,
        "is_self": u.name.lower() == me,
    } for u in users]
    owners.sort(key=lambda o: (not o["is_self"], (o["label"] or "").lower()))
    return owners


def get_lead_form_options(user_email):
    """Everything the app's Register New Lead form needs, read live."""
    meta = frappe.get_meta("Lead")
    creatable = get_lead_creatable_statuses()

    sources = []
    if meta.has_field("source"):
        try:
            sources = frappe.get_all("Lead Source", pluck="name", order_by="name asc")
        except Exception:
            sources = []

    if NEXUS_LEAD_DEFAULT_STATUS in creatable:
        default_status = NEXUS_LEAD_DEFAULT_STATUS
    else:
        default_status = creatable[0] if creatable else None

    return {
        "sources": sources,
        "lead_types": _select_options("Lead", "type") if meta.has_field("type") else [],
        "creatable_statuses": creatable,
        "default_status": default_status,
        "available_fields": [f for f in NEXUS_LEAD_FORM_FIELDS if meta.has_field(f)],
        "assignable_owners": get_lead_assignable_owners(user_email),
    }


def normalize_kenyan_mobile(raw):

    import re
    digits = re.sub(r"\D", "", str(raw or ""))
    if len(digits) == 10 and digits.startswith("0"):
        digits = "254" + digits[1:]
    elif len(digits) == 9 and digits[0] in "17":
        digits = "254" + digits
    return digits if re.fullmatch(r"254[17]\d{8}", digits) else None


def _phone_tail(value):
    import re
    digits = re.sub(r"\D", "", str(value or ""))
    return digits[-9:] if len(digits) >= 9 else None


def _phone_tail_sql(column):

    expr = f"IFNULL({column}, '')"
    for ch in (" ", "-", "+", "(", ")", "."):
        expr = f"REPLACE({expr}, '{ch}', '')"
    return f"RIGHT({expr}, 9)"


def _find_duplicate_lead(tails, email):
    """First not-yet-converted Lead sharing any phone number or the email."""
    conditions, params = [], []
    if tails:
        fmt = ','.join(['%s'] * len(tails))
        for col in ("mobile_no", "whatsapp_no", "phone"):
            if frappe.db.has_column("Lead", col):
                conditions.append(f"{_phone_tail_sql('l.`' + col + '`')} IN ({fmt})")
                params.extend(tails)
    if email and frappe.db.has_column("Lead", "email_id"):
        conditions.append("LOWER(l.email_id) = %s")
        params.append(email.lower())
    if not conditions:
        return None

    rows = frappe.db.sql(f"""
        SELECT l.name, l.lead_name, l.company_name, l.lead_owner
        FROM `tabLead` l
        WHERE IFNULL(l.status, '') != %s
        AND ({' OR '.join(conditions)})
        ORDER BY l.creation DESC
        LIMIT 1
    """, tuple([NEXUS_LEAD_CONVERTED_STATUS] + params), as_dict=True)
    return rows[0] if rows else None


def _find_matching_customer(tails):
    """First enabled Customer sharing any of the phone numbers."""
    if not tails:
        return None
    fmt = ','.join(['%s'] * len(tails))
    conditions, params = [], []
    for col in ("mobile_no", "custom_phone_number"):
        if frappe.db.has_column("Customer", col):
            conditions.append(f"{_phone_tail_sql('c.`' + col + '`')} IN ({fmt})")
            params.extend(tails)
    if not conditions:
        return None

    rows = frappe.db.sql(f"""
        SELECT c.name, c.customer_name
        FROM `tabCustomer` c
        WHERE IFNULL(c.disabled, 0) = 0
        AND ({' OR '.join(conditions)})
        LIMIT 1
    """, tuple(params), as_dict=True)
    return rows[0] if rows else None


def _resolve_lead_owner(requested_owner, session_user):

    requested = (requested_owner or "").strip()
    if not requested or requested.lower() == (session_user or "").strip().lower():
        return session_user, None

    allowed = {e.strip().lower() for e in get_authorized_sales_emails(session_user) if e}
    if requested.lower() not in allowed:
        return None, "You can only register leads for yourself or members of your team."

    user_id = frappe.db.get_value("User", {"name": requested, "enabled": 1}, "name")
    if not user_id:
        return None, "That team member doesn't have an active login, so the lead can't be assigned to them."
    return user_id, None


def _readable_error(exc):
    """Last ERPNext message (or the exception) as plain text, without HTML."""
    import re
    import html as _html

    message = ""
    try:
        log = getattr(frappe.local, "message_log", None) or []
        if log:
            last = log[-1]
            if isinstance(last, str):
                try:
                    last = json.loads(last)
                except Exception:
                    last = {"message": last}
            message = last.get("message") if isinstance(last, dict) else str(last)
    except Exception:
        message = ""

    message = message or str(exc) or "The lead could not be saved."
    message = re.sub(r"<[^>]+>", "", str(message))
    return _html.unescape(message).strip()


def _lead_error(code, message, **extra):
    result = {"status": "error", "code": code, "message": message}
    result.update(extra)
    return result


def _add_lead_registration_comment(lead, session_user, owner, location_source, coord_fields, link):
    lines = [f"🆕 Registered via <b>Nexus Sales App</b> by <b>{_html_escape(str(session_user))}</b>"]
    if (owner or "").lower() != (session_user or "").lower():
        lines.append(f"Assigned to: <b>{_html_escape(str(owner))}</b>")
    if coord_fields:
        lines.append(
            f"Location: <b>{_html_escape(coord_fields['custom_combined_coordinates'])}</b>"
            f"{f' ({location_source})' if location_source else ''}"
        )
    elif link:
        lines.append("Location: Google Maps link saved; coordinates will be extracted in the background.")
    else:
        lines.append("Location: <i>not captured at registration</i>")

    frappe.get_doc({
        "doctype": "Comment",
        "comment_type": "Info",
        "reference_doctype": "Lead",
        "reference_name": lead.name,
        "content": "<br>".join(lines),
        "comment_by": session_user,
    }).insert(ignore_permissions=True)


@frappe.whitelist(methods=["POST"])
def create_mobile_lead(payload):
    """
    Registers a Lead from the app.

    payload keys (all optional unless stated):
      client_request_id   one ID per submission; retries reuse it
      lead_owner          managers only: a team member's login email
      company_name / first_name   at least one REQUIRED
      last_name, job_title
      mobile_no           REQUIRED, Kenyan mobile (any common format)
      whatsapp_no         Kenyan mobile
      phone               any format (landlines allowed)
      email_id
      territory, source, type, status
      custom_location
      google_maps_link, latitude, longitude, custom_combined_coordinates, location_source
      allow_customer_match   1 = "Register Anyway" after a MATCHES_CUSTOMER warning

    Success: {"status": "success", "lead": <row as in sync>, ...}
    Error:   {"status": "error", "code": ..., "message": ..., ["field": ...]}
    """
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except Exception:
            return _lead_error("CREATE_FAILED", "The registration data could not be read.")
    payload = frappe._dict(payload or {})
    session_user = frappe.session.user

    def clean(key):
        value = payload.get(key)
        return value.strip() if isinstance(value, str) else value

    if not get_root_sales_person(session_user):
        return _lead_error("NO_SALES_PROFILE", "Your account has no Sales Person profile, so leads can't be registered.")

    request_id = str(payload.get("client_request_id") or "").strip()[:64]
    result_key = f"nexus_lead_create_result:{session_user.lower()}:{request_id}" if request_id else None
    if result_key:
        cached = frappe.cache().get_value(result_key)
        if cached:
            replay = dict(cached)
            replay["replayed"] = True
            return replay

    owner, owner_error = _resolve_lead_owner(clean("lead_owner"), session_user)
    if owner_error:
        return _lead_error("OWNER_NOT_ALLOWED", owner_error, field="lead_owner")

    company_name = clean("company_name") or ""
    first_name = clean("first_name") or ""
    if not company_name and not first_name:
        return _lead_error("NAME_REQUIRED", "Enter the shop/organization name or the contact's first name.", field="company_name")

    mobile_no = normalize_kenyan_mobile(clean("mobile_no"))
    if not mobile_no:
        return _lead_error("INVALID_MOBILE", "Enter a valid Kenyan mobile number, e.g. 0712 345 678.", field="mobile_no")

    whatsapp_raw = clean("whatsapp_no") or ""
    whatsapp_no = normalize_kenyan_mobile(whatsapp_raw) if whatsapp_raw else None
    if whatsapp_raw and not whatsapp_no:
        return _lead_error("INVALID_MOBILE", "The WhatsApp number isn't a valid Kenyan mobile number.", field="whatsapp_no")

    phone_raw = clean("phone") or ""
    phone = (normalize_kenyan_mobile(phone_raw) or phone_raw) if phone_raw else None

    email_id = (clean("email_id") or "").lower()
    if email_id:
        if not frappe.utils.validate_email_address(email_id, throw=False):
            return _lead_error("INVALID_EMAIL", "Enter a valid email address or leave it empty.", field="email_id")
        if email_id == (owner or "").lower():
            return _lead_error("INVALID_EMAIL", "The lead's email can't be the owner's own login email.", field="email_id")

    creatable = get_lead_creatable_statuses()
    status = clean("status") or (NEXUS_LEAD_DEFAULT_STATUS if NEXUS_LEAD_DEFAULT_STATUS in creatable else None)
    if status and status not in creatable:
        return _lead_error("INVALID_STATUS", f"\"{status}\" can't be used as a starting status.", field="status")

    territory = clean("territory")
    if territory and not frappe.db.exists("Territory", territory):
        return _lead_error("INVALID_FIELD", f"Territory \"{territory}\" doesn't exist.", field="territory")

    source = clean("source")
    if source and not frappe.db.exists("Lead Source", source):
        return _lead_error("INVALID_FIELD", f"Source \"{source}\" doesn't exist.", field="source")

    lead_type = clean("type")
    if lead_type and lead_type not in _select_options("Lead", "type"):
        return _lead_error("INVALID_FIELD", f"Lead Type \"{lead_type}\" isn't a valid option.", field="type")

    link = clean("google_maps_link") or ""
    coord_fields, _point = build_coordinate_fields(
        payload.get("latitude"), payload.get("longitude"), payload.get("custom_combined_coordinates")
    )
    location_source = clean("location_source")
    if location_source not in NEXUS_LOCATION_SOURCES:
        if link == NEXUS_GPS_SNAP_LINK_LABEL:
            location_source = "GPS Snap"
        elif coord_fields:
            location_source = "Maps Link"
        else:
            location_source = None

    tails = sorted({t for t in (_phone_tail(mobile_no), _phone_tail(whatsapp_no), _phone_tail(phone)) if t})
    lock_name = f"nexus_lead_create_lock:{_phone_tail(mobile_no)}"
    cache = frappe.cache()
    if not cache.set(cache.make_key(lock_name), 1, ex=NEXUS_LEAD_CREATE_LOCK_TTL_SEC, nx=True):
        return _lead_error(
            "REQUEST_IN_PROGRESS",
            "A registration for this mobile number is already being processed. Please wait a moment and try again."
        )

    try:
        duplicate = _find_duplicate_lead(tails, email_id)
        if duplicate:
            dup_owner = (duplicate.lead_owner or "").strip()
            owner_label = (
                _sales_person_names_for_emails([dup_owner]).get(dup_owner.lower())
                or frappe.db.get_value("User", dup_owner, "full_name")
                or dup_owner or "no one"
            )
            dup_name = duplicate.lead_name or duplicate.company_name or duplicate.name
            return _lead_error(
                "DUPLICATE_LEAD",
                f"Already registered as \"{dup_name}\", owned by {owner_label}.",
                duplicate={
                    "lead": duplicate.name,
                    "lead_name": dup_name,
                    "owner_name": owner_label,
                    "in_your_scope": _lead_access_error(dup_owner, session_user) is None,
                },
            )

        if not cint(payload.get("allow_customer_match")):
            customer = _find_matching_customer(tails)
            if customer:
                return _lead_error(
                    "MATCHES_CUSTOMER",
                    f"This number belongs to customer \"{customer.customer_name or customer.name}\".",
                    customer={"customer": customer.name, "customer_name": customer.customer_name},
                    can_override=True,
                )

        try:
            lead = frappe.new_doc("Lead")
            meta = lead.meta
            values = {
                "company_name": company_name,
                "first_name": first_name,
                "last_name": clean("last_name"),
                "job_title": clean("job_title"),
                "mobile_no": mobile_no,
                "whatsapp_no": whatsapp_no,
                "phone": phone,
                "email_id": email_id,
                "territory": territory,
                "source": source,
                "type": lead_type,
                "status": status,
                "custom_location": clean("custom_location"),
                "lead_owner": owner,
                "custom_google_maps_link": link,
            }
            values.update(coord_fields or {})
            for fieldname, value in values.items():
                if value not in (None, "") and meta.has_field(fieldname):
                    lead.set(fieldname, value)

            lead.insert(ignore_permissions=True)
            _add_lead_registration_comment(lead, session_user, owner, location_source, coord_fields, link)
            frappe.db.commit()
        except Exception as e:
            frappe.db.rollback()
            message = _readable_error(e)
            frappe.clear_messages()
            frappe.log_error(
                title="Mobile Lead Registration Failed",
                message=f"user={session_user}, owner={owner}, mobile={mobile_no}: {e}"
            )
            return _lead_error("CREATE_FAILED", message)
    finally:
        cache.delete_value(lock_name)

    row = _get_lead_row(lead.name)
    display_name = (row or {}).get("lead_name") or company_name or first_name
    assigned_to_self = (owner or "").lower() == (session_user or "").lower()

    result = {
        "status": "success",
        "message": f"\"{display_name}\" registered." if assigned_to_self
                   else f"\"{display_name}\" registered and assigned to {(row or {}).get('owning_sales_person_name') or owner}.",
        "lead": row,
        "lead_owner": owner,
        "assigned_to_self": assigned_to_self,
    }
    if result_key:
        frappe.cache().set_value(result_key, result, expires_in_sec=NEXUS_LEAD_CREATE_RESULT_TTL_SEC)
    return result

def _create_sales_visit(party_type, party, lat, lng, target_coords, extra_fields=None):

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

    if distance is not None and frappe.db.has_column("Nexus Sales Visit", "original_distance_from_target_meters"):
        doc.original_distance_from_target_meters = distance
    for fieldname, value in (extra_fields or {}).items():
        doc.set(fieldname, value)

    doc.insert(ignore_permissions=True)
    return doc


def _get_visit_for_correction(visit_name):

    fields = ["name", "sales_person", "customer", "latitude", "longitude",
              "check_out_time", "distance_from_target_meters"]
    for optional in ("visit_type", "lead", "original_distance_from_target_meters", "location_corrected"):
        if frappe.db.has_column("Nexus Sales Visit", optional):
            fields.append(optional)
    return frappe.db.get_value("Nexus Sales Visit", visit_name, fields, as_dict=True, for_update=True)


def _resolve_open_visit_for_correction(party_type, party, session_user, visit_id=None):

    party_field = NEXUS_VISIT_PARTY_FIELDS.get(party_type)
    if not party_field:
        return None, "Unsupported visit type, so no visit distance was changed."

    label = party_type.lower()
    session_norm = (session_user or "").strip().lower()
    visit = None

    if visit_id and frappe.db.exists("Nexus Sales Visit", visit_id):
        visit = _get_visit_for_correction(visit_id)
    else:

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

    http_status_code = 426


def _version_tuple(v):

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

# ============================================================================
# 🚨 BACKGROUND GEOCODING — Customer AND Lead
# A Google Maps link saved on a Customer or Lead (desk, import or app) is
# turned into coordinates in the background. One set of functions serves
# every party type in NEXUS_VISIT_PARTY_FIELDS.
#
#   queue_party_geocoding       doc hook: queues one job per save, only when
#                               the pin is missing or the link was replaced
#   execute_external_geocode_call  the job: resolves the link, writes the
#                               three text fields, comments, notifies the app
#   process_bulk_geocoding_queue   cron (10 min): sweeps records the hooks
#                               missed (imports, old data) on the long queue
#
# Links that fail are skipped for 24 h so a few bad links can never block
# the rest of the queue.
# ============================================================================
NEXUS_GEOCODE_URL = "https://crystal-api.crystalapps.dev/extract-coordinates"
NEXUS_GEOCODE_BATCH_SIZE = 20
NEXUS_GEOCODE_FAILURE_TTL_SEC = 24 * 60 * 60
NEXUS_GEOCODE_LOCK_KEY = "nexus_bulk_geocoding_lock"
NEXUS_GEOCODE_LOCK_TTL_SEC = 30 * 60


def _is_geocodable_link(link):

    text = (link or "").strip()
    if not text or text == NEXUS_GPS_SNAP_LINK_LABEL:
        return False
    lowered = text.lower()
    return lowered.startswith(("http://", "https://")) or any(
        marker in lowered for marker in ("goo.gl", "google.", "maps.app")
    )


def _geocode_failure_key(party_type, name, link):
    import hashlib
    digest = hashlib.md5((link or "").strip().encode("utf-8")).hexdigest()
    return f"nexus_geocode_failed:{party_type}:{name}:{digest}"


def _geocode_recently_failed(party_type, name, link):
    return bool(frappe.cache().get_value(_geocode_failure_key(party_type, name, link)))


def _mark_geocode_failed(party_type, name, link):
    frappe.cache().set_value(
        _geocode_failure_key(party_type, name, link), 1,
        expires_in_sec=NEXUS_GEOCODE_FAILURE_TTL_SEC
    )


def _geocode_link(link):
    response = requests.post(NEXUS_GEOCODE_URL, json={"url": (link or "").strip()}, timeout=15)
    if response.status_code != 200:
        return None, f"Geocoder HTTP {response.status_code}"
    data = response.json() or {}
    if data.get("status") != "success":
        return None, data.get("message") or "No coordinates found in the link."
    fields, _point = build_coordinate_fields(
        data.get("lat"), data.get("lng"), data.get("combined_coordinates")
    )
    if not fields:
        return None, "The geocoder returned invalid coordinates."
    return fields, None


def _apply_geocoded_coordinates(party_type, name, fields, link):

    update = _existing_columns_only(party_type, fields)
    if not update:
        return False

    frappe.db.set_value(party_type, name, update, update_modified=False)

    frappe.get_doc({
        "doctype": "Comment",
        "comment_type": "Info",
        "reference_doctype": party_type,
        "reference_name": name,
        "content": "<br>".join([
            "📍 Coordinates extracted from the Google Maps link by the <b>Nexus geocoder</b>",
            f"Latitude: <b>{_html_escape(fields['custom_latitude'])}</b> | "
            f"Longitude: <b>{_html_escape(fields['custom_longitude'])}</b>",
            f"Source link: {_html_escape((link or '').strip())}",
        ]),
    }).insert(ignore_permissions=True)
    return True


def _notify_party_location_changed(party_type, name):

    affected = set()
    extra = None

    if party_type == "Customer":
        team = frappe.db.sql("""
            SELECT sales_person FROM `tabSales Team`
            WHERE parent = %s AND parenttype = 'Customer'
        """, (name,), as_dict=True)
        for row in team:
            if row.sales_person:
                _add_sp_and_ancestors(row.sales_person, affected)
    elif party_type == "Lead":
        lead_row = frappe.db.get_value("Lead", name, ["lead_owner", "status"], as_dict=True) or {}
        _add_user_and_managers(lead_row.get("lead_owner"), affected)
        extra = {"lead": name, "lead_status": lead_row.get("status"), "lead_event": "updated"}

    if affected:
        execute_fastapi_webhook(list(affected), party_type, name, "FORCE_VAULT_SYNC", extra=extra)


def queue_party_geocoding(doc, method=None):

    if getattr(frappe.flags, "in_import", False):
        return
    if doc.doctype not in NEXUS_VISIT_PARTY_FIELDS:
        return
    if doc.flags.get("nexus_geocode_queued"):
        return
    if doc.doctype == "Lead" and doc.get("status") == NEXUS_LEAD_CONVERTED_STATUS:
        return

    link = (doc.get("custom_google_maps_link") or "").strip()
    if not _is_geocodable_link(link):
        return

    has_coords = parse_combined_coords(
        doc.get("custom_combined_coordinates"),
        doc.get("custom_latitude"),
        doc.get("custom_longitude"),
    ) is not None
    link_changed = doc.has_value_changed("custom_google_maps_link")
    coords_changed = any(
        doc.has_value_changed(f) for f in NEXUS_COORD_FIELDS if doc.meta.has_field(f)
    )

    if has_coords and not (link_changed and not coords_changed):
        return
    if _geocode_recently_failed(doc.doctype, doc.name, link):
        return

    doc.flags.nexus_geocode_queued = True
    frappe.enqueue(
        "nexus_supply_chain.api.execute_external_geocode_call",
        queue="short",
        timeout=300,
        enqueue_after_commit=True,
        doc_name=doc.name,
        link=link,
        party_type=doc.doctype,
    )


def queue_customer_geocoding(doc, method=None):
    """Kept so any hook or code still using the old name keeps working."""
    return queue_party_geocoding(doc, method)


def execute_external_geocode_call(doc_name, link, party_type="Customer"):

    try:
        if party_type not in NEXUS_VISIT_PARTY_FIELDS or not frappe.db.exists(party_type, doc_name):
            return

        current_link = (frappe.db.get_value(party_type, doc_name, "custom_google_maps_link") or "").strip()
        if current_link != (link or "").strip():
            return

        fields, error = _geocode_link(link)
        if not fields:
            _mark_geocode_failed(party_type, doc_name, link)
            frappe.log_error(
                title="Nexus Geocode Failed",
                message=f"{party_type} {doc_name}: {error} | link={link}"
            )
            return

        if _apply_geocoded_coordinates(party_type, doc_name, fields, link):
            frappe.db.commit()  # essential in background jobs
            frappe.publish_realtime("doc_update", message={"doctype": party_type, "name": doc_name})
            _notify_party_location_changed(party_type, doc_name)
            frappe.logger().info(f"[Nexus Geocode] {party_type} {doc_name} geocoded.")

    except Exception as e:
        frappe.db.rollback()
        frappe.log_error(title="Nexus Background Geocode Error", message=f"{party_type} {doc_name}: {e}")


def _collect_geocoding_targets(limit):

    targets = []
    for party_type in NEXUS_VISIT_PARTY_FIELDS:
        if len(targets) >= limit:
            break
        required = ("custom_google_maps_link",) + NEXUS_COORD_FIELDS
        if not all(frappe.db.has_column(party_type, f) for f in required):
            continue

        extra_where = ""
        params = {"snap": NEXUS_GPS_SNAP_LINK_LABEL, "scan": max(limit * 5, 50)}
        if party_type == "Lead":
            extra_where = "AND IFNULL(status, '') != %(converted)s"
            params["converted"] = NEXUS_LEAD_CONVERTED_STATUS
        elif party_type == "Customer":
            extra_where = "AND IFNULL(disabled, 0) = 0"

        rows = frappe.db.sql(f"""
            SELECT name, custom_google_maps_link,
                   custom_combined_coordinates, custom_latitude, custom_longitude
            FROM `tab{party_type}`
            WHERE IFNULL(custom_google_maps_link, '') != ''
            AND custom_google_maps_link != %(snap)s
            AND IFNULL(custom_combined_coordinates, '') NOT LIKE '%%,%%'
            AND (
                IFNULL(custom_latitude, '') = '' OR IFNULL(custom_longitude, '') = ''
                OR (CAST(custom_latitude AS DECIMAL(21,9)) = 0 AND CAST(custom_longitude AS DECIMAL(21,9)) = 0)
            )
            {extra_where}
            ORDER BY modified DESC
            LIMIT %(scan)s
        """, params, as_dict=True)

        for r in rows:
            if len(targets) >= limit:
                break
            if parse_combined_coords(r.custom_combined_coordinates, r.custom_latitude, r.custom_longitude):
                continue
            link = (r.custom_google_maps_link or "").strip()
            if not _is_geocodable_link(link) or _geocode_recently_failed(party_type, r.name, link):
                continue
            targets.append(frappe._dict(party_type=party_type, name=r.name, link=link))

    return targets


def process_bulk_geocoding_queue():

    try:
        if _collect_geocoding_targets(limit=1):
            frappe.enqueue("nexus_supply_chain.api.run_bulk_geocoding", queue="long", timeout=1800)
    except Exception as e:
        frappe.log_error(title="Nexus Bulk Geocode Scheduler Failed", message=str(e))


def run_bulk_geocoding():

    import time
    import random

    cache = frappe.cache()
    lock_key = cache.make_key(NEXUS_GEOCODE_LOCK_KEY)
    if not cache.set(lock_key, 1, ex=NEXUS_GEOCODE_LOCK_TTL_SEC, nx=True):
        return

    updated = 0
    try:
        targets = _collect_geocoding_targets(limit=NEXUS_GEOCODE_BATCH_SIZE)
        frappe.logger().info(f"[Nexus Geocode] Bulk sweep starting for {len(targets)} record(s).")

        for index, t in enumerate(targets):
            try:
                fields, error = _geocode_link(t.link)
                if fields and _apply_geocoded_coordinates(t.party_type, t.name, fields, t.link):
                    frappe.db.commit()
                    updated += 1
                else:
                    _mark_geocode_failed(t.party_type, t.name, t.link)
                    frappe.log_error(
                        title="Nexus Geocode Failed",
                        message=f"{t.party_type} {t.name}: {error or 'nothing written'} | link={t.link}"
                    )
            except Exception as e:
                frappe.db.rollback()
                _mark_geocode_failed(t.party_type, t.name, t.link)
                frappe.log_error(title="Nexus Bulk Geocode Error", message=f"{t.party_type} {t.name}: {e}")

            if index < len(targets) - 1:
                time.sleep(random.uniform(4.0, 7.0))
    finally:
        cache.delete_value(NEXUS_GEOCODE_LOCK_KEY)

    if updated:
        frappe.cache().set_value('nexus_needs_sync', True)
        frappe.logger().info(f"[Nexus Geocode] Bulk sweep finished. Geocoded {updated} record(s).")

@frappe.whitelist()
def check_mobile_app_access():

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
    customer_coord_fields = [
        f for f in ("custom_combined_coordinates", "custom_latitude", "custom_longitude")
        if frappe.db.has_column("Customer", f)
    ]
    for record in manifest_records:
        doc = frappe.get_doc("Vehicle Delivery Manifest", record.name)
        manifest_dict = doc.as_dict()

        for stop in manifest_dict.get("stops", []):
            if stop.get("customer"):
                # 🚨 Customer coordinates are text now; the driver app still
                # receives floats (or None), exactly as before. The customer's
                # pin wins; the stop's own saved lat/lng is the fallback.
                point = None
                try:
                    if customer_coord_fields:
                        coords = frappe.db.get_value(
                            "Customer", stop.get("customer"), customer_coord_fields, as_dict=True
                        ) or {}
                        point = parse_combined_coords(
                            coords.get("custom_combined_coordinates"),
                            coords.get("custom_latitude"),
                            coords.get("custom_longitude"),
                        )
                except Exception:
                    point = None

                if not point:
                    point = parse_combined_coords(None, stop.get("latitude"), stop.get("longitude"))

                stop["custom_latitude"] = point[0] if point else None
                stop["custom_longitude"] = point[1] if point else None

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

    if not sales_person_name:
        return []
    rows = frappe.db.sql("""
        SELECT DISTINCT parent FROM `tabSales Team`
        WHERE parenttype = 'Customer' AND sales_person = %s
    """, (sales_person_name,), as_dict=False)
    return [r[0] for r in rows] if rows else []


@frappe.whitelist()
def get_manager_team_roster():

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

    requested_email = frappe.request.headers.get("sales-rep-email")
    target_email = resolve_authorized_target_email(frappe.session.user, requested_email)

    auth_sps = get_authorized_sales_persons(target_email)
    if not auth_sps:
        return {"status": "error", "message": "No assigned sales profile hierarchy."}

    format_sps = ','.join(['%s'] * len(auth_sps))
    tuple_sps = tuple(auth_sps)

    root_sp = get_root_sales_person(target_email)
    root_sp_doc = frappe.db.get_value(
        "Sales Person", root_sp,
        ["is_group", "sales_person_name", "custom_sales_target", "custom_collections_target"],
        as_dict=True
    ) if root_sp else None
    is_manager = bool(root_sp_doc.is_group) if root_sp_doc else False

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

    try:
        leads = get_scoped_leads(target_email)
        lead_status_options = get_lead_status_options()
    except Exception as e:
        frappe.log_error(title="Nexus Lead Sync Failed", message=f"{target_email}: {e}")
        leads, lead_status_options = [], []

    try:
        lead_form_options = get_lead_form_options(target_email)
    except Exception as e:
        frappe.log_error(title="Nexus Lead Form Options Failed", message=f"{target_email}: {e}")
        lead_form_options = {}

    items = frappe.db.sql("""
        SELECT i.name as name, i.item_code, i.item_name
        FROM `tabItem` i
        JOIN `tabItem Group` ig ON i.item_group = ig.name
        WHERE i.disabled = 0
        AND ig.lft >= (SELECT lft FROM `tabItem Group` WHERE name = 'Finished Goods')
        AND ig.rgt <= (SELECT rgt FROM `tabItem Group` WHERE name = 'Finished Goods')
    """, as_dict=True)

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

    financial_totals = get_customer_scoped_financial_totals(customer_ids, start_of_month, end_of_month, sales_person_ids=auth_sps)

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

        "sales_target": float(sales_target),
        "collection_target": float(collection_target),
        "total_orders": int(total_orders),
        "total_orders_value": float(total_orders_value),
        "total_invoiced_orders": float(financial_totals["gross_invoiced"]),
        "total_returns": float(financial_totals["returns"]),
        "total_collections": float(financial_totals["collections"]),
        "total_outstanding": float(financial_totals["outstanding"]),
        "total_overdue": float(financial_totals["overdue"]),

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

        "is_manager": is_manager,
        "team_roster": team_roster,
        "show_personal_block": show_personal_block,
        "personal_sales_block": personal_sales_block,
        "personal_collections_block": personal_collections_block,

        "root_sales_person": root_sp,
        "root_sales_person_name": root_sp_doc.sales_person_name if root_sp_doc else None,

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
            "lead_status_options": lead_status_options,
            "lead_form_options": lead_form_options
        }
    }

@frappe.whitelist()
def get_invoice_details_for_order(order_id):

    items = frappe.db.sql("""
        SELECT si.item_code, si.item_name, si.qty, si.rate, si.amount
        FROM `tabSales Invoice Item` si
        JOIN `tabSales Invoice` s ON si.parent = s.name
        WHERE si.sales_order = %s AND s.docstatus = 1
    """, (order_id,), as_dict=True)

    return {"status": "success", "data": items}

@frappe.whitelist()
def get_customer_financial_brief(customer_id):

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

    requested_email = frappe.request.headers.get("sales-rep-email")
    target_email = resolve_authorized_target_email(frappe.session.user, requested_email)
    auth_sps = get_authorized_sales_persons(target_email)
    if not auth_sps:
        return {"status": "error", "message": "No assigned sales profile hierarchy."}

    format_sps = ','.join(['%s'] * len(auth_sps))
    tuple_sps = tuple(auth_sps)

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

    if isinstance(payload, str):
        payload = json.loads(payload)

    if not order_id:
        return {"status": "error", "message": "order_id is required."}

    if not frappe.db.exists("Sales Order", order_id):
        return {"status": "error", "message": "Sales Order not found."}

    try:
        so = frappe.get_doc("Sales Order", order_id)

        if so.docstatus != 0:
            return {
                "status": "error",
                "message": f"This order can no longer be edited (status: {so.status}). Only Draft orders can be edited."
            }

        items = payload.get("items", [])
        if not items:
            return {"status": "error", "message": "Order must contain at least one item."}

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
        start_of_year = datetime(getdate(today()).year, 1, 1).strftime('%Y-%m-%d')
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
        if doc.party_type != 'Customer' or not doc.party:
            return
        party = doc.party
        if doc.payment_type == 'Receive':
            if doc.docstatus == 1:
                increment_collection = float(doc.paid_amount or 0.0)
            elif doc.docstatus == 2:
                increment_collection = -float(doc.paid_amount or 0.0)

    elif doc.doctype == "Journal Entry":

        sign = 1 if doc.docstatus == 1 else -1
        for jea in doc.get("accounts", []):
            if jea.party_type == "Customer" and jea.party:
                party = jea.party
                row_amount = float(jea.credit_in_account_currency or 0.0) - float(jea.debit_in_account_currency or 0.0)
                increment_collection += sign * row_amount
        if not party:
            return
    else:
        return

    if increment_collection == 0.0:
        return

    invoice_ids = []
    if hasattr(doc, 'references'):
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

def trigger_lead_refresh_from_linked(doc, method=None):

    try:
        if doc.doctype == "Opportunity":
            is_lead = doc.get("opportunity_from") == "Lead"
        elif doc.doctype == "Quotation":
            is_lead = doc.get("quotation_to") == "Lead"
        else:
            return

        lead = doc.get("party_name")
        if not is_lead or not lead:
            return

        row = frappe.db.get_value("Lead", lead, ["lead_owner", "status"], as_dict=True)
        if not row:
            return

        affected = set()
        _add_user_and_managers(row.lead_owner, affected)
        if not affected:
            return

        frappe.enqueue(
            "nexus_supply_chain.api.execute_fastapi_webhook",
            queue="short",
            affected_emails=list(affected),
            doctype="Lead",
            docname=lead,
            command="FORCE_VAULT_SYNC",
            extra={"lead": lead, "lead_status": row.status, "lead_event": "updated"},
            enqueue_after_commit=True
        )
    except Exception as e:
        frappe.log_error(title="Nexus Linked Lead Refresh Failed", message=f"{doc.doctype} {doc.name}: {e}")

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

        if mobile_no:
            doc.mobile_no = mobile_no
        if phone_number:
            doc.custom_phone_number = phone_number

        if location_text:
            doc.custom_location = location_text

        # 🚨 Coordinates as exact text — all three fields hold the same digits.
        coord_fields, _point = build_coordinate_fields(
            payload.get("latitude") or payload.get("lat"),
            payload.get("longitude") or payload.get("lng"),
            payload.get("custom_combined_coordinates"),
        )
        for fieldname, value in (coord_fields or {}).items():
            doc.set(fieldname, value)

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

    try:
        if party_type not in NEXUS_VISIT_PARTY_FIELDS or not party or not frappe.db.exists(party_type, party):
            return {"status": "error", "message": f"{party_type} not found."}

        coord_fields, point = build_coordinate_fields(latitude, longitude, custom_combined_coordinates)
        if not coord_fields:
            frappe.log_error(
                title="Coord Update: Invalid Values",
                message=(f"{party_type} {party} received unusable coords: lat={latitude}, lng={longitude}, "
                         f"combined={custom_combined_coordinates}")
            )
            return {"status": "error", "message": "Invalid or out-of-range coordinate values."}

        lat_float, lng_float = point
        link_text = (google_maps_link or "").strip()

        geo_fields = [f for f in NEXUS_COORD_FIELDS + ("custom_google_maps_link",)
                      if frappe.db.has_column(party_type, f)]

        previous = frappe.db.get_value(party_type, party, geo_fields, as_dict=True) if geo_fields else None
        previous = previous or frappe._dict()
        previous_coords = parse_combined_coords(
            previous.get("custom_combined_coordinates"),
            previous.get("custom_latitude"),
            previous.get("custom_longitude")
        )

        update_dict = _existing_columns_only(party_type, coord_fields)
        if link_text and "custom_google_maps_link" in geo_fields:
            update_dict["custom_google_maps_link"] = link_text

        if not update_dict:
            return {"status": "error", "message": f"The {party_type} doctype has no GeoLocation fields to update."}

        frappe.db.set_value(party_type, party, update_dict, update_modified=False)

        acting_user = frappe.form_dict.get("acting_user") or frappe.session.user
        normalized_source = location_source if location_source in NEXUS_LOCATION_SOURCES else None

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
                    _apply_visit_location_correction(visit_row, point, normalized_source)
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
            (f"New — Latitude: <b>{_html_escape(coord_fields['custom_latitude'])}</b> | "
             f"Longitude: <b>{_html_escape(coord_fields['custom_longitude'])}</b>"),
        ]
        if normalized_source:
            comment_lines.append(f"Method: <b>{normalized_source}</b>")
        if link_text:
            comment_lines.append(f"Source link: {_html_escape(link_text)}")
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
            f"{coord_fields['custom_combined_coordinates']} by {acting_user} | "
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

    frappe.only_for("System Manager")
    dry_run = cint(dry_run)

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

    visit_owner = frappe.db.get_value("Nexus Sales Visit", target_visit_id, "sales_person")
    if visit_owner and visit_owner.lower() != frappe.session.user.lower():
        return {"status": "error", "message": "You can only submit a report for your own visit."}

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

    if frappe.db.has_column("Nexus Sales Visit", "visit_with_report"):
        update_dict["visit_with_report"] = 1

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

    try:
        if not frappe.cache().get_value('nexus_needs_sync'):
            return

        emails = _get_all_sales_rep_emails()

        response = requests.post(
            "https://crystal-api.crystalapps.dev/api/v1/cache/invalidate",
            json={
                "command": "GLOBAL_DEBOUNCED_SYNC",
                "doctype": "System",
                "docname": "Scheduled Sync",
                "emails": emails,
            },
            timeout=10
        )
        response.raise_for_status()

        frappe.cache().set_value('nexus_needs_sync', False)
    except Exception as e:
        frappe.log_error(title="Scheduled Orchestrator Sync Failed", message=str(e))

@frappe.whitelist()
def get_active_companies_for_dispatch():

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

