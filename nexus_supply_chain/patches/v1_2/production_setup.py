"""
Nexus Production - one-time setup (Batch 1).

1. Creates / aligns the production custom fields.
2. Fills Nexus Production Settings with sensible defaults, but only fields that
   are still empty, so re-running never overwrites what you set.
3. Backfills Sales Order "Confirmed On" for orders submitted before this
   feature existed:
     - recent or still-open orders: the moment the Version history shows the
       order being submitted (docstatus 0 -> 1);
     - everything else, or when no Version is found: the order's transaction date.
"""

import json

import frappe
from frappe.utils import add_days, get_datetime, today

from nexus_supply_chain.production.custom_fields import ensure_custom_fields

SETTINGS = "Nexus Production Settings"


def execute():
    ensure_custom_fields()
    frappe.reload_doc("nexus_supply_chain", "doctype", "nexus_production_warehouse")
    frappe.reload_doc("nexus_supply_chain", "doctype", "nexus_production_draft_state")
    frappe.reload_doc("nexus_supply_chain", "doctype", "nexus_production_role")
    frappe.reload_doc("nexus_supply_chain", "doctype", "nexus_production_settings")
    set_default_settings()
    backfill_confirmed_on()


# --------------------------------------------------------------------- settings

def _first_existing(doctype, candidates):
    for name in candidates:
        if frappe.db.exists(doctype, name):
            return name
    return None


def _existing(doctype, names):
    return [n for n in names if frappe.db.exists(doctype, n)]


def set_default_settings():
    doc = frappe.get_single(SETTINGS)

    scalar_defaults = {
        "fg_warehouse": _first_existing("Warehouse", ["Finished Goods - CAL"]),
        "colourant_warehouse": _first_existing("Warehouse", ["WIP -Intermediary - CAL"]),
        "cook_target_warehouse": _first_existing("Warehouse", ["Work In Progress - CAL"]),
        "fill_target_warehouse": _first_existing("Warehouse", ["Finished Goods - CAL"]),
        "packaging_item_group": _first_existing("Item Group", ["Packaging Materials"]),
        "mass_balance_tolerance": 2,
        "order_age_limit_days": 30,
        "draft_age_limit_days": 14,
        "wo_age_limit_days": 30,
        "count_not_started": 1,
        "count_in_process": 1,
        "excess_fill_rule": "Lowest stock cover first",
        "tint_base_priority": "Urgent then oldest across all orders",
        "include_shelf_without_orders": 1,
        "refresh_interval_minutes": 5,
        "night_refresh_interval_minutes": 15,
        "night_start": "19:00:00",
        "night_end": "06:00:00",
        "cache_ttl_seconds": 60,
        "engine_url": "https://crystal-api.crystalapps.dev/api/v2/production-plan",
        "engine_timeout_seconds": 20,
        "auto_replace_sub_bom_links": 1,
    }
    checkboxes = ("count_not_started", "count_in_process", "include_shelf_without_orders", "auto_replace_sub_bom_links")
    is_new = not doc.get("fg_warehouse")  # never configured before
    for fieldname, value in scalar_defaults.items():
        if value is None:
            continue
        if fieldname in checkboxes:
            # An unticked box is a real choice, so only default it on first setup.
            if is_new:
                doc.set(fieldname, value)
        elif doc.get(fieldname) in (None, "", 0):
            doc.set(fieldname, value)

    if not doc.get("bulk_warehouses"):
        for wh in _existing("Warehouse", ["Work In Progress - CAL", "WIP -Intermediary - CAL", "Finished Goods - CAL"]):
            doc.append("bulk_warehouses", {"warehouse": wh})

    if not doc.get("rm_warehouses"):
        rm = _existing("Warehouse", ["Stores - CAL", "Packaging Materials - CAL", "Water Treatment - CAL"])
        kerosene = _first_existing("Warehouse", ["Kerosene Production  - CAL", "Kerosene Production - CAL"])
        if kerosene:
            rm.append(kerosene)
        for wh in rm:
            doc.append("rm_warehouses", {"warehouse": wh})

    if not doc.get("draft_workflow_states"):
        for state in _existing("Workflow State", ["Proceed To Order", "Pending Finance Approval", "Pending Customer Order Reconfirmation"]):
            doc.append("draft_workflow_states", {"workflow_state": state})

    if not doc.get("allowed_roles"):
        for role in _existing("Role", ["Manufacturing Manager", "Manufacturing User"]):
            doc.append("allowed_roles", {"role": role})

    # Never let a site-specific gap (e.g. a warehouse with another name) fail the migrate.
    doc.flags.ignore_validate = True
    doc.flags.ignore_mandatory = True
    doc.flags.ignore_permissions = True
    doc.save()
    frappe.db.commit()


# --------------------------------------------------------------- confirmed on

def _submission_times(docnames):
    """docname -> datetime of the first Version that changed docstatus to 1."""
    found = {}
    for i in range(0, len(docnames), 500):
        chunk = docnames[i:i + 500]
        rows = frappe.db.sql("""
            SELECT docname, creation, data FROM `tabVersion`
            WHERE ref_doctype = 'Sales Order' AND docname IN %(names)s
              AND data LIKE %(needle)s
            ORDER BY creation ASC
        """, {"names": tuple(chunk), "needle": "%docstatus%"}, as_dict=True)
        for r in rows:
            if r.docname in found:
                continue
            try:
                changed = (json.loads(r.data or "{}") or {}).get("changed") or []
            except ValueError:
                continue
            for change in changed:
                if len(change) >= 3 and change[0] == "docstatus" and str(change[2]) == "1":
                    found[r.docname] = get_datetime(r.creation)
                    break
    return found


def backfill_confirmed_on():
    if not frappe.db.has_column("Sales Order", "custom_confirmed_on"):
        return

    recent_cutoff = add_days(today(), -180)
    candidates = frappe.db.sql_list("""
        SELECT name FROM `tabSales Order`
        WHERE docstatus = 1 AND custom_confirmed_on IS NULL
          AND (transaction_date >= %s
               OR status IN ('To Deliver and Bill', 'To Deliver', 'To Bill', 'On Hold'))
    """, (recent_cutoff,))

    stamps = _submission_times(candidates) if candidates else {}
    for n, (name, when) in enumerate(stamps.items(), start=1):
        frappe.db.set_value("Sales Order", name, "custom_confirmed_on", when, update_modified=False)
        if n % 500 == 0:
            frappe.db.commit()

    # Everything still empty falls back to the order's transaction date.
    frappe.db.sql("""
        UPDATE `tabSales Order`
        SET custom_confirmed_on = TIMESTAMP(transaction_date)
        WHERE docstatus = 1 AND custom_confirmed_on IS NULL AND transaction_date IS NOT NULL
    """)
    frappe.db.commit()
