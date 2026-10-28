"""
Nexus Production: live refresh signals.

Documents that change the production board call on_doc_change (doc_events in
hooks.py). Nothing is sent from inside the save:

  1. on_doc_change only decides if the change matters and notes it on the
     request (no Redis, no SQL). Many changes in one request become one signal.
  2. After the transaction COMMITS, the signal:
       * makes the cached boards of the affected mode(s) obsolete (one Redis INCR),
       * tells open pages "the board changed" over the realtime socket, at most
         once every GATE_SECONDS. A change inside that window is kept as
         pending and sent by flush_pending (cron, every minute).
     A rolled-back save sends nothing.
  3. Open pages wait a few random seconds (so they do not all ask at once),
     then reload. The server calculates once; everyone else gets the cache.

The message carries no business data, only which mode(s) changed.
Turn the whole thing off with "nexus_production_live_updates": 0 in
site_config.json; the page then relies on its fallback timer.
"""

import frappe
from frappe.utils import cint, now_datetime

from nexus_supply_chain.production import service
from nexus_supply_chain.production.settings import get_settings

EVENT = "nexus_production_changed"
GATE_SECONDS = 30
PENDING_TTL_SECONDS = 3600
SIGNAL_PREFIX = "nexus_production_signal:"     # not under CACHE_PREFIX: a Settings save must not wipe it
GATE_KEY = SIGNAL_PREFIX + "gate"
PENDING_PREFIX = SIGNAL_PREFIX + "pending:"
FLAG = "nexus_production_signal"

BOTH = ("confirmed", "drafts")

# Item fields the board depends on. Saving an Item for anything else (prices,
# descriptions, images) sends nothing.
ITEM_FIELDS = (
    "disabled", "item_group", "stock_uom", "default_bom", "is_stock_item", "lead_time_days",
    "custom_is_bip", "custom_is_stainer", "custom_is_colourant", "custom_linked_bip",
    "custom_monitor_shelf_level", "custom_minimum_reorder_level", "custom_maximum_shelf_capacity",
    "custom_minimum_production_level", "custom_production_route_override",
    "custom_include_in_production_card", "custom_production_priority",
)


# ------------------------------------------------------------------ what matters

def _company():
    s = get_settings()
    return frappe.get_cached_value("Warehouse", s.fg_warehouse, "company") if s.fg_warehouse else None


def _linked_to_sales_order(doc, field):
    return any(row.get(field) for row in (doc.get("items") or []))


def _modes(doc, method):
    """The board modes this change affects, or () when it does not matter."""
    dt, status = doc.doctype, cint(doc.get("docstatus"))

    if dt == "Nexus Production Settings":
        return BOTH
    if dt == "Item":
        if method == "on_trash" or doc.is_new():
            return BOTH
        return BOTH if any(doc.has_value_changed(f) for f in ITEM_FIELDS if doc.meta.has_field(f)) else ()

    company = doc.get("company")
    if company and company != _company():
        return ()

    if dt == "Sales Order":
        # draft orders only feed the Drafts board
        return ("drafts",) if status == 0 else BOTH
    if dt == "Delivery Note":
        # even a DRAFT delivery note takes its order out of the plan;
        # a submitted or cancelled one also moves stock
        return BOTH if status > 0 or _linked_to_sales_order(doc, "against_sales_order") else ()
    if dt == "Sales Invoice":
        moves_stock = status > 0 and cint(doc.get("update_stock"))
        return BOTH if moves_stock or _linked_to_sales_order(doc, "sales_order") else ()
    # Work Order, Material Request, Purchase Order, Purchase Receipt, Stock Entry,
    # Stock Reconciliation, BOM: only submitted and cancelled documents count
    if method == "on_trash" or status == 0:
        return ()
    return BOTH


# ------------------------------------------------------------------ hook

def on_doc_change(doc, method=None):
    """doc_events handler (on_change / on_trash). Never raises: a save must not fail because of the board."""
    try:
        if not cint(frappe.conf.get("nexus_production_live_updates", 1)):
            return
        if frappe.flags.in_install or frappe.flags.in_migrate:
            return
        modes = _modes(doc, method)
        if not modes:
            return
        pending = frappe.flags.get(FLAG)
        if pending is None:
            pending = frappe.flags[FLAG] = {"modes": set(), "doctypes": set()}
            frappe.db.after_commit.add(_after_commit)
            frappe.db.after_rollback.add(_after_rollback)
        pending["modes"].update(modes)
        pending["doctypes"].add(doc.doctype)
    except Exception:
        frappe.log_error(title="Nexus Production signal failed", message=frappe.get_traceback())


def _after_rollback():
    frappe.flags.pop(FLAG, None)


def _after_commit():
    pending = frappe.flags.pop(FLAG, None)
    if not pending or not pending["modes"]:
        return
    try:
        signal(sorted(pending["modes"]), sorted(pending["doctypes"]))
    except Exception:
        frappe.log_error(title="Nexus Production signal failed", message=frappe.get_traceback())


# ------------------------------------------------------------------ send

def _publish(modes, doctypes):
    frappe.publish_realtime(EVENT, {"modes": list(modes), "doctypes": list(doctypes)[:10],
                                    "at": str(now_datetime())})


def _open_gate():
    """True for the first signal in a GATE_SECONDS window."""
    cache = frappe.cache()
    return bool(cache.set(cache.make_key(GATE_KEY), 1, ex=GATE_SECONDS, nx=True))


def signal(modes, doctypes=()):
    """The board changed for these modes: obsolete the cache now, tell open pages (rate limited)."""
    service.clear_board_cache(modes)
    if _open_gate():
        _publish(modes, doctypes)
        return
    cache = frappe.cache()
    for mode in modes:
        cache.set_value(PENDING_PREFIX + mode, 1, expires_in_sec=PENDING_TTL_SECONDS)


def flush_pending():
    """Cron, every minute: send the changes that arrived while the gate was closed."""
    cache = frappe.cache()
    modes = [m for m in BOTH if cache.get_value(PENDING_PREFIX + m, expires=True)]
    if not modes or not _open_gate():
        return
    for mode in modes:
        cache.delete_value(PENDING_PREFIX + mode)
    _publish(modes, ())
