"""
Nexus Production: the "Pending Work Orders & Purchase Orders" tab.

    nexus_supply_chain.production.pending.get_pending

Loaded only when someone opens the tab, straight from ERPNext (no engine call),
so the production board never waits for it. READ-ONLY: it lists documents and
says what to do with them; it never closes or cancels anything.

* Work Orders: every submitted Work Order still in progress (Not Started or
  In Process) for the company, with what was made, whether material sits in
  WIP, whether the board counts it, flags and a recommended action. Stopped
  Work Orders are left out: the board never deducts them.
* Purchase Orders: every submitted Purchase Order not fully received with at
  least one open line into a production warehouse, with what is still to
  come, value, how overdue it is, the last receipt and a recommended action.

Each full list is built once a minute (two queries per list) and cached in
Redis; searching, filtering and paging work on that cached list. A relevant
document change (Batch 6 signals) or a Settings save makes it rebuild at once.
"""

import frappe
from frappe.utils import add_days, cint, date_diff, flt, getdate, nowdate

from nexus_supply_chain.production import service
from nexus_supply_chain.production.settings import assert_access, get_settings
from nexus_supply_chain.production.snapshot import _company, _warehouses

CACHE_SECONDS = 60
# bump when the shape or the rules of the cached rows change, so a deploy never serves old rows
CACHE_VERSION = 2
MAX_PAGE = 200
# the statuses the board can count; Stopped and Closed Work Orders are never deducted
OPEN_WO = ("Not Started", "In Process")
CLOSED_PO = ("Closed", "Completed", "Delivered", "Cancelled")


def _cache_key(kind):
    # the generation changes whenever the board's data changes (Batch 6), so the list rebuilds with it
    return "{0}pending:v{1}:{2}:{3}".format(service.CACHE_PREFIX, CACHE_VERSION, kind,
                                            service._generation("confirmed"))


def _cached(kind, build):
    key = _cache_key(kind)
    rows = frappe.cache().get_value(key, expires=True)
    if rows is None:
        rows = build()
        frappe.cache().set_value(key, rows, expires_in_sec=CACHE_SECONDS)
    return rows


# ------------------------------------------------------------------ Work Orders

def _work_order_rows():
    s = get_settings()
    company = _company(s)
    today = getdate(nowdate())
    stale_days = s.pending_stale_days
    age_limit = max(1, cint(s.wo_age_limit_days) or 30)
    counted_status = {st for st, on in (("Not Started", s.count_not_started), ("In Process", s.count_in_process)) if on}
    in_wip = "woi.transferred_qty > IFNULL(woi.consumed_qty, 0)" \
        if frappe.db.has_column("Work Order Item", "consumed_qty") else "woi.transferred_qty > 0"

    rows = frappe.db.sql("""
        SELECT wo.name, wo.production_item AS item_code, wo.item_name, wo.status, wo.qty, wo.produced_qty,
               wo.stock_uom AS uom, wo.planned_start_date, wo.creation, wo.modified, wo.sales_order,
               wo.wip_warehouse, IFNULL(w.lines_in_wip, 0) AS lines_in_wip
        FROM `tabWork Order` wo
        LEFT JOIN (
            SELECT woi.parent, COUNT(*) AS lines_in_wip
            FROM `tabWork Order Item` woi
            INNER JOIN `tabWork Order` x ON x.name = woi.parent
            WHERE x.docstatus = 1 AND x.status IN %(open)s AND {in_wip}
            GROUP BY woi.parent
        ) w ON w.parent = wo.name
        WHERE wo.docstatus = 1 AND wo.status IN %(open)s {company}
    """.format(in_wip=in_wip, company="AND wo.company = %(company)s" if company else ""),
        {"open": OPEN_WO, "company": company}, as_dict=True)

    out = []
    for r in rows:
        planned = getdate(r.planned_start_date or r.creation)
        age = date_diff(today, planned)
        idle = date_diff(today, getdate(r.modified))
        made, qty = flt(r.produced_qty), flt(r.qty)
        flags = []
        if age > age_limit:
            flags.append("Older than {0} days".format(age_limit))
        if r.lines_in_wip and made <= 0 and idle > stale_days:
            flags.append("Material in WIP, nothing made")
        if made < qty and idle > stale_days:
            flags.append("No progress for {0} days".format(idle))

        if made <= 0 and not r.lines_in_wip:
            action = "If it is wrong or no longer needed: Cancel it."
        elif made <= 0:
            action = ("If it is wrong: cancel its Material Transfer, then cancel it. If the transfer cannot be "
                      "cancelled: transfer the material back to the store, then Close it.")
        else:
            action = "If no more will be made: transfer unused material back to the store, then Close it."

        counted = bool(cint(s.include_work_orders) and r.status in counted_status
                       and age <= age_limit and made < qty)
        out.append({
            "name": r.name, "item_code": r.item_code, "item_name": r.item_name, "status": r.status,
            "qty": qty, "produced_qty": made, "uom": r.uom, "sales_order": r.sales_order,
            "planned": str(planned), "age_days": age, "idle_days": idle, "lines_in_wip": cint(r.lines_in_wip),
            "counted": counted, "flags": flags, "action": action if flags else "",
        })
    out.sort(key=lambda x: (not x["flags"], x["planned"], x["name"]))
    return out


# ------------------------------------------------------------------ Purchase Orders

def _purchase_order_rows():
    s = get_settings()
    company = _company(s)
    warehouses = _warehouses(s)
    today = getdate(nowdate())
    stale_days = s.pending_stale_days
    if not warehouses:
        return []

    rows = frappe.db.sql("""
        SELECT po.name, po.supplier, po.supplier_name, po.transaction_date, po.schedule_date, po.status,
               IFNULL(po.per_received, 0) AS per_received, po.currency,
               COUNT(*) AS open_lines,
               SUM((poi.qty - IFNULL(poi.received_qty, 0)) * poi.base_rate) AS pending_value,
               GROUP_CONCAT(DISTINCT poi.item_code ORDER BY poi.idx SEPARATOR ', ') AS items
        FROM `tabPurchase Order` po
        INNER JOIN `tabPurchase Order Item` poi ON poi.parent = po.name
        WHERE po.docstatus = 1 AND po.status NOT IN %(closed)s AND IFNULL(po.per_received, 0) < 100
          AND poi.qty > IFNULL(poi.received_qty, 0) AND poi.warehouse IN %(warehouses)s
          {company}
        GROUP BY po.name
    """.format(company="AND po.company = %(company)s" if company else ""),
        {"closed": CLOSED_PO, "warehouses": tuple(warehouses), "company": company}, as_dict=True)

    last_receipt = {}
    names = [r.name for r in rows]
    for i in range(0, len(names), 500):
        for po, posted in frappe.db.sql("""
            SELECT pri.purchase_order, MAX(pr.posting_date)
            FROM `tabPurchase Receipt Item` pri
            INNER JOIN `tabPurchase Receipt` pr ON pr.name = pri.parent
            WHERE pr.docstatus = 1 AND pri.purchase_order IN %(names)s
            GROUP BY pri.purchase_order
        """, {"names": tuple(names[i:i + 500])}):
            last_receipt[po] = posted

    included = bool(cint(s.include_purchase_orders))
    out = []
    for r in rows:
        due = getdate(r.schedule_date or r.transaction_date)
        overdue = max(0, date_diff(today, due))
        received = last_receipt.get(r.name)
        since_receipt = date_diff(today, getdate(received)) if received else None
        flags = []
        if overdue > stale_days:
            flags.append("Overdue {0} days".format(overdue))
            if not flt(r.per_received):
                flags.append("Nothing received yet")
            elif since_receipt is not None and since_receipt > stale_days:
                flags.append("Part received, nothing for {0} days".format(since_receipt))
        items = (r["items"] or "").split(", ")
        out.append({
            "name": r.name, "supplier": r.supplier, "supplier_name": r.supplier_name or r.supplier,
            "status": r.status, "ordered_on": str(r.transaction_date), "due": str(due), "overdue_days": overdue,
            "per_received": round(flt(r.per_received), 1), "pending_value": round(flt(r.pending_value), 2),
            "open_lines": cint(r.open_lines), "items": items[:5], "more_items": max(0, len(items) - 5),
            "last_receipt": str(received) if received else None,
            "counted": included, "flags": flags,
            "action": ("If the goods came in without this Purchase Order, or will not come: Close it "
                       "(Status > Close).") if flags else "",
        })
    out.sort(key=lambda x: (not x["flags"], x["due"], x["name"]))
    return out


# ------------------------------------------------------------------ endpoint

def _page(rows, start, page_length, search, flagged_only, fields):
    q = (search or "").strip().lower()
    if q:
        rows = [r for r in rows if any(q in str(r.get(f) or "").lower() for f in fields)]
    if cint(flagged_only):
        rows = [r for r in rows if r["flags"]]
    start = max(0, cint(start))
    return {"rows": rows[start:start + page_length], "total": len(rows), "start": start,
            "flagged": sum(1 for r in rows if r["flags"])}


@frappe.whitelist()
def get_pending(wo_start=0, po_start=0, page_length=50, search=None, flagged_only=1):
    """Both lists, one page each. Each list is paged on its own (wo_start / po_start)."""
    assert_access()
    s = get_settings()
    page_length = min(MAX_PAGE, max(1, cint(page_length) or 50))
    work_orders = _cached("work_orders", _work_order_rows)
    purchase_orders = _cached("purchase_orders", _purchase_order_rows)
    return {
        "work_orders": _page(work_orders, wo_start, page_length, search, flagged_only,
                             ("name", "item_code", "item_name", "sales_order", "status")),
        "purchase_orders": _page(purchase_orders, po_start, page_length, search, flagged_only,
                                 ("name", "supplier", "supplier_name", "items", "status")),
        "settings": {
            "include_work_orders": cint(s.include_work_orders),
            "include_purchase_orders": cint(s.include_purchase_orders),
            "stale_days": s.pending_stale_days,
            "wo_age_limit_days": cint(s.wo_age_limit_days) or 30,
        },
        "today": nowdate(),
        "since": str(add_days(nowdate(), -s.pending_stale_days)),
    }
