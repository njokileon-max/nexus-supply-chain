"""
Nexus Production: the page's action buttons.

    nexus_supply_chain.production.actions.work_order       Create WO (cook, fill, tint)
    nexus_supply_chain.production.actions.material_request Raise MR (one material, or all short)
    nexus_supply_chain.production.actions.clear_board_cache_on_change   doc hook

Every action works in two steps from the page:
    1. preview (commit=0): the server works out quantity, BOM, warehouses and
       the Sales Order link from the CURRENT board and returns them, plus any
       draft documents that already exist for the same item;
    2. create  (commit=1): the same calculation again, then a DRAFT document.
       Nothing is ever submitted here: a supervisor reviews and submits.

Quantities always come from the server's board, never from the browser, so a
stale or edited page cannot create the wrong quantity. Actions are refused in
Drafts mode: draft sales orders are not a reason to make or buy anything yet.
"""

import json
import math

import frappe
from frappe.utils import add_days, cint, flt, getdate, now_datetime, nowdate

from nexus_supply_chain.production import service
from nexus_supply_chain.production.settings import assert_access, get_settings

EPS = 1e-6


# ------------------------------------------------------------------ helpers

def _as_bool(value):
    return bool(cint(value))


def _require_post():
    request = getattr(frappe.local, "request", None)
    if request is not None and request.method != "POST":
        frappe.throw("This action must be sent as a POST request.")


def _board(mode, from_date, to_date):
    if mode == "drafts":
        frappe.throw("Switch the page to Confirmed orders to create documents. Draft orders are only a preview.",
                     title="Drafts mode")
    # background=False: quantities must come from the current data, never from an older board
    result = service.get_board("confirmed", from_date=from_date, to_date=to_date, background=False)
    if result["meta"].get("stale"):
        frappe.throw("The board could not be recalculated just now, so quantities cannot be trusted. "
                     "Try again in a minute.<br><br>{0}".format(frappe.utils.escape_html(result["meta"].get("error") or "")),
                     title="Board not up to date")
    return result["board"]


def _card(board, item_code):
    for card in (board.get("bulks") or []) + (board.get("items") or []):
        if card.get("item_code") == item_code:
            return card
    frappe.throw("{0} is no longer on the production board. Refresh the page.".format(item_code),
                 title="Not on the board")


def _material(board, item_code):
    for m in board.get("materials") or []:
        if m.get("item_code") == item_code:
            return m
    return None


def _whole(item_code):
    uom = frappe.db.get_value("Item", item_code, "stock_uom")
    return cint(frappe.db.get_value("UOM", uom, "must_be_whole_number")) if uom else 0


def _round_qty(item_code, qty):
    qty = flt(qty)
    if _whole(item_code):
        return float(math.ceil(qty - EPS)) if qty > EPS else 0.0
    return round(qty, 3)


def _company(s):
    for w in [s.fg_warehouse] + list(s.bulk_warehouses) + list(s.rm_warehouses):
        company = frappe.db.get_value("Warehouse", w, "company") if w else None
        if company:
            return company
    return frappe.defaults.get_user_default("Company") or frappe.db.get_single_value("Global Defaults", "default_company")


def _item_default_warehouse(item_code, company):
    return frappe.db.get_value("Item Default", {"parent": item_code, "parenttype": "Item", "company": company},
                               "default_warehouse")


def _is_leaf(warehouse):
    return bool(warehouse) and frappe.db.exists("Warehouse", {"name": warehouse, "is_group": 0, "disabled": 0})


def _default_bom(item_code):
    bom = frappe.db.get_value("Item", item_code, "default_bom")
    if bom and frappe.db.get_value("BOM", bom, ["is_active", "docstatus"]) == (1, 1):
        return bom
    return frappe.db.get_value("BOM", {"item": item_code, "is_default": 1, "is_active": 1, "docstatus": 1}, "name")


def _open_drafts(doctype, item_code):
    if doctype == "Work Order":
        return frappe.get_all("Work Order", filters={"production_item": item_code, "docstatus": 0},
                              fields=["name", "qty", "creation"], order_by="creation desc", limit=5)
    rows = frappe.db.sql("""
        SELECT mr.name, mri.qty, mr.creation
        FROM `tabMaterial Request` mr
        INNER JOIN `tabMaterial Request Item` mri ON mri.parent = mr.name
        WHERE mr.docstatus = 0 AND mr.material_request_type = 'Purchase' AND mri.item_code = %s
        ORDER BY mr.creation DESC LIMIT 5
    """, (item_code,), as_dict=True)
    return rows


# ------------------------------------------------------------------ work orders

def _work_order_plan(card, s, company):
    code = card["item_code"]
    is_bulk = "cook" in card
    qty = _round_qty(code, card.get("cook") if is_bulk else card.get("production_total"))
    if qty <= EPS:
        frappe.throw("Nothing to make for {0} on the current board.".format(code), title="Nothing to make")

    bom = _default_bom(code)
    if not bom:
        frappe.throw("{0} has no active, submitted default BOM, so a Work Order cannot be made. "
                     "See the Needs Setup tab.".format(code), title="No BOM")

    # where the finished item goes: the item's own default, else Settings
    target = _item_default_warehouse(code, company)
    if not _is_leaf(target):
        target = (s.cook_target_warehouse if is_bulk else s.fill_target_warehouse) \
            or (s.bulk_warehouses[0] if is_bulk else s.fg_warehouse)
    wip = frappe.db.get_single_value("Manufacturing Settings", "default_wip_warehouse")
    if not _is_leaf(wip):
        wip = s.cook_target_warehouse or (s.bulk_warehouses[0] if s.bulk_warehouses else None)
    if not _is_leaf(target):
        frappe.throw("No usable target warehouse for {0}. Set a default warehouse on the item, or "
                     "Cook / Fill Target Warehouse in Nexus Production Settings.".format(code))

    # the orders this card is making for
    orders = []
    for r in card.get("rows") or []:
        if r.get("sales_order") and not r.get("via") and flt(r.get("short")) > EPS and r["sales_order"] not in orders:
            orders.append(r["sales_order"])
    for r in card.get("short_orders") or []:
        if r.get("sales_order") and r["sales_order"] not in orders:
            orders.append(r["sales_order"])

    # link the Sales Order only when it is the single reason and ERPNext will accept it
    sales_order, link_note = None, ""
    if len(orders) == 1:
        so_qty = flt(frappe.db.sql("""
            SELECT SUM(stock_qty) FROM `tabSales Order Item`
            WHERE parent = %s AND item_code = %s
        """, (orders[0], code))[0][0])
        if so_qty <= EPS:
            link_note = "{0} does not contain {1} itself (the demand comes through another item), so it is not linked.".format(orders[0], code)
        elif qty > so_qty + EPS:
            link_note = "The quantity is more than {0} ordered ({1}), so it is not linked; the extra is shelf refill or batch top-up.".format(orders[0], so_qty)
        else:
            sales_order = orders[0]
    elif len(orders) > 1:
        link_note = "Made for {0} orders, so no single Sales Order is linked.".format(len(orders))

    return frappe._dict(
        item_code=code, item_name=card.get("item_name"), uom=card.get("uom"), qty=qty, bom=bom,
        fg_warehouse=target, wip_warehouse=wip, company=company, is_bulk=is_bulk,
        route=card.get("route"), priority=card.get("priority"), orders=orders,
        sales_order=sales_order, link_note=link_note,
        drafts=_open_drafts("Work Order", code),
    )


@frappe.whitelist(methods=["POST"])
def work_order(item_code, mode="confirmed", from_date=None, to_date=None, commit=0):
    """Preview (commit=0) or create (commit=1) a DRAFT Work Order for one card."""
    assert_access()
    frappe.has_permission("Work Order", "create", throw=True)
    _require_post()
    s = get_settings()
    company = _company(s)
    plan = _work_order_plan(_card(_board(mode, from_date, to_date), item_code), s, company)
    if not _as_bool(commit):
        return plan

    wo = frappe.new_doc("Work Order")
    wo.update({
        "production_item": plan.item_code,
        "bom_no": plan.bom,
        "qty": plan.qty,
        "company": plan.company,
        "fg_warehouse": plan.fg_warehouse,
        "wip_warehouse": plan.wip_warehouse,
        "planned_start_date": now_datetime(),
        # bulks are cooked on their own cards, so a fill never explodes its bulk into raw materials
        "use_multi_level_bom": 0,
    })
    if plan.sales_order:
        wo.sales_order = plan.sales_order
    wo.insert()   # ERPNext fills required items and source warehouses from the BOM

    reason = ", ".join(plan.orders[:20]) or "shelf refill / batch top-up"
    wo.add_comment("Comment", "Raised from Nexus Production Command ({0}, P{1}). For: {2}.{3}".format(
        plan.route or "", plan.priority if plan.priority is not None else "", reason,
        (" " + plan.link_note) if plan.link_note else ""))
    service.clear_board_cache()
    return {"doctype": "Work Order", "name": wo.name, "qty": plan.qty, "uom": plan.uom,
            "sales_order": plan.sales_order, "link_note": plan.link_note}


# ------------------------------------------------------------------ material requests

def _mr_warehouse(item_code, company, s):
    """The item's own default if it is a raw-material warehouse, else where most of it already sits,
    else the first raw-material warehouse in Settings."""
    rm = [w for w in s.rm_warehouses if w]
    is_colourant = frappe.db.has_column("Item", "custom_is_colourant") and \
        cint(frappe.db.get_value("Item", item_code, "custom_is_colourant"))
    if is_colourant and s.colourant_warehouse:
        rm = [s.colourant_warehouse] + [w for w in rm if w != s.colourant_warehouse]
    default = _item_default_warehouse(item_code, company)
    if default in rm:
        return default
    if rm:
        best = frappe.db.sql("""
            SELECT warehouse FROM `tabBin` WHERE item_code = %s AND warehouse IN %s
            ORDER BY actual_qty DESC LIMIT 1
        """, (item_code, tuple(rm)))
        if best and best[0][0]:
            return best[0][0]
        return rm[0]
    return default


def _mr_line(m, company, s):
    code = m["item_code"]
    qty = _round_qty(code, m.get("suggested_request"))
    if qty <= EPS:
        return None
    lead = cint(frappe.db.get_value("Item", code, "lead_time_days"))
    return frappe._dict(
        item_code=code, item_name=m.get("item_name"), uom=frappe.db.get_value("Item", code, "stock_uom"),
        qty=qty, warehouse=_mr_warehouse(code, company, s), kind=m.get("kind"),
        schedule_date=str(add_days(nowdate(), max(1, lead))),
        shortage_value=flt(m.get("shortage_value")), value_at_risk=flt(m.get("value_at_risk")),
        orders=[o.get("sales_order") for o in (m.get("orders") or []) if o.get("sales_order")],
        sources=[x.get("item_code") for x in (m.get("sources") or [])][:10],
        drafts=_open_drafts("Material Request", code),
    )


@frappe.whitelist(methods=["POST"])
def material_request(item_codes=None, all_short=0, mode="confirmed", from_date=None, to_date=None, commit=0):
    """
    Preview (commit=0) or create (commit=1) ONE draft Purchase Material Request.
      item_codes: JSON list of materials, or
      all_short=1: every material short on the current board.
    Quantity per line = the board's suggested request (shortfall after purchase
    orders, minus what is already requested on submitted Material Requests).
    """
    assert_access()
    frappe.has_permission("Material Request", "create", throw=True)
    _require_post()
    s = get_settings()
    company = _company(s)
    board = _board(mode, from_date, to_date)

    if _as_bool(all_short):
        materials = [m for m in board.get("materials") or [] if flt(m.get("short")) > EPS]
    else:
        codes = json.loads(item_codes) if isinstance(item_codes, str) else (item_codes or [])
        materials = [m for m in (_material(board, c) for c in codes) if m]
        missing = [c for c in codes if not _material(board, c)]
        if missing:
            frappe.throw("Not on the current board: {0}. Refresh the page.".format(", ".join(missing)))

    lines = [ln for ln in (_mr_line(m, company, s) for m in materials) if ln]
    if not lines:
        frappe.throw("Nothing to request: every selected material is covered by stock, purchase orders "
                     "or Material Requests already submitted.", title="Nothing to request")
    lines.sort(key=lambda ln: -ln.shortage_value)
    preview = {"company": company, "lines": lines,
               "total_value": round(sum(ln.shortage_value for ln in lines), 2)}
    if not _as_bool(commit):
        return preview

    mr = frappe.new_doc("Material Request")
    mr.update({"material_request_type": "Purchase", "company": company, "transaction_date": nowdate(),
               "schedule_date": min(getdate(ln.schedule_date) for ln in lines)})
    for ln in lines:
        mr.append("items", {"item_code": ln.item_code, "qty": ln.qty, "uom": ln.uom, "conversion_factor": 1,
                            "warehouse": ln.warehouse, "schedule_date": ln.schedule_date})
    mr.insert()

    orders = sorted({o for ln in lines for o in ln.orders})
    mr.add_comment("Comment", "Raised from Nexus Production Command. {0} material(s), shortfall value KES {1:,.0f}. "
                   "Orders waiting: {2}.".format(len(lines), preview["total_value"],
                                                 ", ".join(orders[:30]) + (" and more" if len(orders) > 30 else "")
                                                 if orders else "none (shelf refills and batch top-ups)"))
    service.clear_board_cache()
    return {"doctype": "Material Request", "name": mr.name, "lines": len(lines)}


# ------------------------------------------------------------------ hook

def clear_board_cache_on_change(doc, method=None):
    """Work Order / Material Request / Stock Entry / Delivery Note changes: the next page refresh recalculates."""
    try:
        service.clear_board_cache()
    except Exception:
        frappe.log_error(title="Nexus Production cache clear failed", message=frappe.get_traceback())
