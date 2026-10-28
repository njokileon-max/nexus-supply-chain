"""
BOM housekeeping: keep parent BOMs pointing at each component's CURRENT
default BOM.

In ERPNext each BOM line stores which BOM of its component it uses (BOM Item
.bom_no). Making a new version of a bulk's BOM leaves every parent BOM on
the old version, so Update Cost keeps reading the old cost and the old BOM
cannot be deactivated. ERPNext's own fix is Manufacturing > BOM Update Tool >
Replace BOM. This module drives that same tool automatically:

  * on_bom_default_change  (BOM hook) - when a submitted, active BOM becomes
    the default, queue Replace for every parent still on an older version.
    Switch: Nexus Production Settings > Auto-replace sub-BOM links.
  * replace_outdated_sub_bom_links (whitelisted) - one-off clean-up of every
    outdated link on the site, with a dry run first.

Every replacement goes through ERPNext's BOM Update Log, so progress and
errors are visible in /app/bom-update-log.
"""

import re

import frappe
from frappe import _
from frappe.utils import cint

from nexus_supply_chain.production.settings import get_settings

MANAGER_ROLES = ("System Manager", "Manufacturing Manager")


# ---------------------------------------------------------------- helpers

def _version_number(bom_name):
    """BOM-ITEM-007 -> 7. Returns None if the name has no numeric suffix."""
    m = re.search(r"-(\d+)$", bom_name or "")
    return int(m.group(1)) if m else None


def _is_backwards(old_bom, new_bom):
    """True when parents use a NEWER version than the current default,
    e.g. parents on -002 while -001 is the default."""
    old_v, new_v = _version_number(old_bom), _version_number(new_bom)
    return old_v is not None and new_v is not None and old_v > new_v


def _queue_replace(old_bom, new_bom):
    """Queue one ERPNext Replace BOM job. Returns the BOM Update Log name or None."""
    from erpnext.manufacturing.doctype.bom_update_tool.bom_update_tool import enqueue_replace_bom

    try:
        log = enqueue_replace_bom(boms={"current_bom": old_bom, "new_bom": new_bom})
        return log.name if log else None
    except Exception:
        frappe.log_error(
            title="Nexus BOM relink failed",
            message=f"Replace {old_bom} -> {new_bom}\n\n{frappe.get_traceback()}",
        )
        return None


def get_outdated_pairs(item_code=None):
    """
    Every (component, old BOM, current default BOM) where an ACTIVE, non-
    cancelled parent BOM still points at a BOM that is not the component's
    current default. Optionally limited to one component.
    """
    conditions = ""
    values = {}
    if item_code:
        conditions = "AND bi.item_code = %(item_code)s"
        values["item_code"] = item_code

    rows = frappe.db.sql(f"""
        SELECT bi.item_code, bi.bom_no AS old_bom, d.name AS new_bom,
               COUNT(DISTINCT bi.parent) AS parents
        FROM `tabBOM Item` bi
        JOIN `tabBOM` p ON p.name = bi.parent AND p.docstatus < 2 AND p.is_active = 1
        JOIN `tabBOM` d ON d.item = bi.item_code AND d.is_default = 1
                       AND d.is_active = 1 AND d.docstatus = 1
        WHERE IFNULL(bi.bom_no, '') != '' AND bi.bom_no != d.name
        {conditions}
        GROUP BY bi.item_code, bi.bom_no, d.name
        ORDER BY parents DESC
    """, values, as_dict=True)

    for r in rows:
        r["backwards"] = _is_backwards(r.old_bom, r.new_bom)
    return rows


# ---------------------------------------------------------------- BOM hook

def on_bom_default_change(doc, method=None):
    """
    BOM on_submit / on_update_after_submit. When this BOM is submitted,
    active and the default for its item, re-point every parent BOM that still
    uses an older version. Runs in the background after the save commits, so
    saving the BOM never waits and never fails because of it.
    """
    if doc.docstatus != 1 or not cint(doc.is_active) or not cint(doc.is_default):
        return
    try:
        if not get_settings().auto_replace_sub_bom_links:
            return
    except Exception:
        return

    has_outdated_parent = frappe.db.sql("""
        SELECT 1 FROM `tabBOM Item` bi
        JOIN `tabBOM` p ON p.name = bi.parent AND p.docstatus < 2 AND p.is_active = 1
        WHERE bi.item_code = %s AND IFNULL(bi.bom_no, '') NOT IN ('', %s)
        LIMIT 1
    """, (doc.item, doc.name))
    if not has_outdated_parent:
        return

    frappe.enqueue(
        "nexus_supply_chain.production.bom_sync.relink_parents_for_item",
        queue="long",
        timeout=1800,
        item_code=doc.item,
        new_bom=doc.name,
        job_id=f"nexus_bom_relink::{doc.item}",
        deduplicate=True,
        enqueue_after_commit=True,
    )


def relink_parents_for_item(item_code, new_bom):
    """Background job: replace every outdated link to item_code with new_bom."""
    current = frappe.db.get_value(
        "BOM", {"item": item_code, "is_default": 1, "is_active": 1, "docstatus": 1}, "name"
    )
    if current != new_bom:
        return  # the default changed again since this job was queued

    queued = []
    for pair in get_outdated_pairs(item_code):
        if pair.new_bom != new_bom:
            continue
        log = _queue_replace(pair.old_bom, new_bom)
        if log:
            queued.append(log)
        frappe.db.commit()

    if queued:
        frappe.logger("nexus_production").info(
            f"[BOM relink] {item_code}: queued {len(queued)} replacement(s) -> {new_bom}"
        )


# ---------------------------------------------------------------- one-off clean-up

@frappe.whitelist()
def replace_outdated_sub_bom_links(dry_run=1, include_backwards=0):
    """
    Site-wide clean-up of outdated sub-BOM links.

      dry_run=1           returns what would be replaced, changes nothing
      dry_run=0           queues the replacements (must be a POST)
      include_backwards=1 also replaces "backwards" pairs, where parents use a
                          newer version than the current default. Off by default:
                          settle which version is right first.
    """
    frappe.only_for(MANAGER_ROLES)
    dry_run = cint(dry_run)
    include_backwards = cint(include_backwards)

    pairs = get_outdated_pairs()
    backwards = [p for p in pairs if p.backwards]
    selected = [p for p in pairs if include_backwards or not p.backwards]

    summary = {
        "dry_run": dry_run,
        "total_pairs": len(pairs),
        "selected_pairs": len(selected),
        "skipped_backwards": 0 if include_backwards else len(backwards),
        "parent_boms_affected": sum(p.parents for p in selected),
        "pairs": [dict(p) for p in selected[:300]],
        "backwards_pairs": [dict(p) for p in backwards],
    }
    if dry_run or not selected:
        return summary

    request = getattr(frappe.local, "request", None)
    if request is not None and request.method != "POST":
        frappe.throw(_("Queuing replacements must be sent as a POST request."))

    frappe.enqueue(
        "nexus_supply_chain.production.bom_sync.process_pairs",
        queue="long",
        timeout=3600,
        pairs=[{"old_bom": p.old_bom, "new_bom": p.new_bom} for p in selected],
        job_id="nexus_bom_relink_all",
        deduplicate=True,
    )
    summary["queued"] = len(selected)
    return summary


def process_pairs(pairs):
    """Background job for the one-off clean-up."""
    done = 0
    for p in pairs:
        if _queue_replace(p["old_bom"], p["new_bom"]):
            done += 1
        frappe.db.commit()
    frappe.logger("nexus_production").info(f"[BOM relink] clean-up queued {done}/{len(pairs)} replacement(s)")
