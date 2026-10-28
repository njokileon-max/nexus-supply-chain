"""
Nexus Production snapshot.

Collects everything the calculation engine needs in a fixed number of
set-based queries (no per-item loops), driven entirely by Nexus Production
Settings, and shapes it into the payload accepted by FastAPI's
/api/v2/production-plan.

    payload = build_snapshot("confirmed")      # or "drafts"

fetch_raw() does the reading; shape_payload() is pure Python, so the exact
payload contract can be tested without a database.

Rules
-----
* Confirmed demand: every submitted Sales Order of the company that is not
  Closed / On Hold / Completed, line by line, counting only what is still to
  deliver: (qty - delivered_qty) x conversion factor, in stock units.
  Delivered quantities only change when a Delivery Note is submitted, which
  is also when the stock leaves the Bin, so demand and stock always move
  together (partly delivered orders count only their remainder; drop-ship
  lines are skipped). Age limits are applied by the engine, which also
  lists the older orders it leaves out.
* Drafts (Drafts mode only): orders still in draft (docstatus 0) in one of
  the Draft Workflow States, created within the draft age limit.
* Stock: Bins of the Settings warehouses only, with on-order and requested
  quantities (ERPNext's reserved-for-production is sent too, for reference).
* Work Orders (only when Settings > Include Work Orders is ticked):
  - submitted Not Started / In Process / Stopped Work Orders; the engine
    decides which ones it counts (status and age limit);
  - wo_materials: per open Work Order planned inside the age limit, what it
    still needs of each material (required - max(transferred, consumed)), in
    its source warehouse. The engine sets aside only what the Work Orders it
    counts still need, so old or wrong Work Orders never lock up stock;
  - transfers: per open Work Order, material moved to WIP but not used yet.
  When the box is unticked none of this is read.
* Purchase Orders: the Bins' ordered quantity is always sent; the engine
  uses it only when Settings > Include Purchase Orders is ticked.
* Material value: the item's Last Purchase Rate and its Valuation Rate
  (average Bin value in the raw-material / colourant warehouses, else the
  item's Valuation Rate) are both sent; Settings > Price for Shortfall Value
  chooses which one the engine uses.
* Every custom field is read through a column check, so the snapshot still
  works (with defaults) on a site where a migrate has not run yet.
"""

import frappe
from frappe.utils import add_days, cint, flt, getdate, now_datetime

from nexus_supply_chain.production.settings import get_settings

OPEN_WO_STATUSES = ("Not Started", "In Process")
LISTED_WO_STATUSES = ("Not Started", "In Process", "Stopped")
CLOSED_SO_STATUSES = ("Closed", "On Hold", "Completed")
CHUNK = 2000


# ------------------------------------------------------------------ helpers

def _col(doctype, column, alias=None, default="NULL", table_alias=None):
    """`alias.column AS alias` if the column exists, else `default AS alias`."""
    alias = alias or column
    prefix = (table_alias + ".") if table_alias else ""
    if frappe.db.has_column(doctype, column):
        return "{0}`{1}` AS `{2}`".format(prefix, column, alias)
    return "{0} AS `{1}`".format(default, alias)


def _chunks(values, size=CHUNK):
    values = list(values)
    for i in range(0, len(values), size):
        yield values[i:i + size]


def _in(values):
    return ", ".join(["%s"] * len(values))


def _text(value):
    if value is None:
        return None
    return str(value)


def _warehouses(s):
    seen = []
    for w in [s.fg_warehouse] + list(s.bulk_warehouses) + [s.colourant_warehouse] + list(s.rm_warehouses):
        if w and w not in seen:
            seen.append(w)
    return seen


def _company(s):
    for w in [s.fg_warehouse] + list(s.bulk_warehouses):
        if w:
            company = frappe.db.get_value("Warehouse", w, "company")
            if company:
                return company
    return None


def check_settings(s):
    missing = []
    if not s.fg_warehouse:
        missing.append("Finished Goods Warehouse")
    if not s.bulk_warehouses:
        missing.append("Bulk Warehouses")
    if not s.rm_warehouses:
        missing.append("Raw Material Warehouses")
    if missing:
        frappe.throw(
            "Nexus Production Settings is incomplete: set {0}.".format(", ".join(missing)),
            title="Production settings",
        )


# ------------------------------------------------------------------ reading

def fetch_raw(s, mode, from_date, to_date):
    company = _company(s)
    warehouses = _warehouses(s)
    raw = frappe._dict(company=company, warehouses=warehouses)

    # 1. default BOMs and their rows
    raw.boms = frappe.db.sql("""
        SELECT b.name, b.item, b.quantity, {loss}
        FROM `tabBOM` b
        WHERE b.is_default = 1 AND b.is_active = 1 AND b.docstatus = 1
    """.format(loss=_col("BOM", "process_loss_percentage", default="0", table_alias="b")), as_dict=True)

    raw.bom_lines = frappe.db.sql("""
        SELECT bi.parent, bi.idx, bi.item_code, bi.stock_qty, bi.stock_uom, bi.bom_no
        FROM `tabBOM Item` bi
        INNER JOIN `tabBOM` b ON b.name = bi.parent
        WHERE b.is_default = 1 AND b.is_active = 1 AND b.docstatus = 1
          AND bi.parenttype = 'BOM'
        ORDER BY bi.parent, bi.idx
    """, as_dict=True)

    # 2. confirmed orders: submitted, not closed, and untouched by dispatch:
    #    nothing delivered or billed, and no Delivery Note or Sales Invoice
    #    (draft or submitted) pointing at the order yet. Only orders confirmed
    #    inside the page's date window are read.
    company_filter = "AND so.company = %(company)s" if company else ""
    dropship = "AND IFNULL(soi.delivered_by_supplier, 0) = 0" \
        if frappe.db.has_column("Sales Order Item", "delivered_by_supplier") else ""
    confirmed_at = "COALESCE(so.custom_confirmed_on, so.transaction_date)" \
        if frappe.db.has_column("Sales Order", "custom_confirmed_on") else "so.transaction_date"
    untouched = """
          AND so.docstatus = 1
          AND so.status NOT IN %(closed)s
          AND IFNULL(so.per_delivered, 0) = 0
          AND IFNULL(so.per_billed, 0) = 0
          AND NOT EXISTS (SELECT 1 FROM `tabDelivery Note Item` dni
                          WHERE dni.against_sales_order = so.name AND dni.docstatus < 2)
          AND NOT EXISTS (SELECT 1 FROM `tabSales Invoice Item` sii
                          WHERE sii.sales_order = so.name AND sii.docstatus < 2)
          {company_filter}
    """.format(company_filter=company_filter)
    window = {"closed": CLOSED_SO_STATUSES, "company": company,
              "from": from_date, "to_next": add_days(to_date, 1)}

    raw.order_lines = frappe.db.sql("""
        SELECT so.name AS sales_order, so.customer, so.customer_name,
               {confirmed}, so.transaction_date, so.creation, {priority},
               NULL AS workflow_state, 'confirmed' AS kind,
               soi.item_code, soi.qty, soi.delivered_qty, soi.conversion_factor, soi.base_net_amount
        FROM `tabSales Order` so
        INNER JOIN `tabSales Order Item` soi ON soi.parent = so.name AND soi.parenttype = 'Sales Order'
        WHERE {confirmed_at} >= %(from)s AND {confirmed_at} < %(to_next)s
          AND soi.qty > IFNULL(soi.delivered_qty, 0)
          {untouched} {dropship}
    """.format(
        confirmed=_col("Sales Order", "custom_confirmed_on", "confirmed_on", table_alias="so"),
        priority=_col("Sales Order", "custom_production_priority", "priority", "'Normal'", table_alias="so"),
        confirmed_at=confirmed_at, untouched=untouched, dropship=dropship,
    ), window, as_dict=True)

    # how many qualifying orders were confirmed BEFORE the window (shown, not planned)
    raw.earlier_orders = cint(frappe.db.sql("""
        SELECT COUNT(*) FROM `tabSales Order` so
        WHERE {confirmed_at} < %(from)s {untouched}
    """.format(confirmed_at=confirmed_at, untouched=untouched), window)[0][0])

    # 3. drafts (Drafts mode only): created inside the same window
    raw.draft_lines = []
    if mode == "drafts" and s.draft_workflow_states and frappe.db.has_column("Sales Order", "workflow_state"):
        raw.draft_lines = frappe.db.sql("""
            SELECT so.name AS sales_order, so.customer, so.customer_name,
                   NULL AS confirmed_on, so.transaction_date, so.creation, {priority},
                   so.workflow_state, 'draft' AS kind,
                   soi.item_code, soi.qty, 0 AS delivered_qty, soi.conversion_factor, soi.base_net_amount
            FROM `tabSales Order` so
            INNER JOIN `tabSales Order Item` soi ON soi.parent = so.name AND soi.parenttype = 'Sales Order'
            WHERE so.docstatus = 0
              AND so.workflow_state IN %(states)s
              AND so.transaction_date >= %(from)s AND so.transaction_date < %(to_next)s
              AND soi.qty > 0
              {company_filter} {dropship}
        """.format(
            priority=_col("Sales Order", "custom_production_priority", "priority", "'Normal'", table_alias="so"),
            company_filter=company_filter, dropship=dropship,
        ), dict(window, states=tuple(s.draft_workflow_states)), as_dict=True)

    # 4-5. Work Orders, what they still need, and what they moved to WIP (only when included)
    raw.work_orders, raw.wo_materials, raw.transfers = [], [], []
    if cint(s.include_work_orders):
        wo_company = "AND company = %(company)s" if company else ""
        raw.work_orders = frappe.db.sql("""
            SELECT name, production_item, status, qty, produced_qty, planned_start_date, creation, bom_no
            FROM `tabWork Order`
            WHERE docstatus = 1 AND status IN %(statuses)s {wo_company}
        """.format(wo_company=wo_company), {"statuses": LISTED_WO_STATUSES, "company": company}, as_dict=True)

        # only Work Orders inside the age limit can be counted, so older ones are not sent
        wo_window = {"statuses": OPEN_WO_STATUSES, "company": company,
                     "since": add_days(getdate(now_datetime()), -max(1, cint(s.wo_age_limit_days) or 30))}
        wo_company_alias = "AND wo.company = %(company)s" if company else ""
        used = "GREATEST(IFNULL(woi.transferred_qty, 0), IFNULL(woi.consumed_qty, 0))" \
            if frappe.db.has_column("Work Order Item", "consumed_qty") else "IFNULL(woi.transferred_qty, 0)"
        raw.wo_materials = frappe.db.sql("""
            SELECT woi.parent AS work_order, woi.item_code, woi.source_warehouse AS warehouse,
                   woi.required_qty - {used} AS qty
            FROM `tabWork Order Item` woi
            INNER JOIN `tabWork Order` wo ON wo.name = woi.parent
            WHERE wo.docstatus = 1 AND wo.status IN %(statuses)s
              AND COALESCE(wo.planned_start_date, wo.creation) >= %(since)s
              AND woi.required_qty > {used}
              {wo_company}
        """.format(used=used, wo_company=wo_company_alias), wo_window, as_dict=True)

        if frappe.db.has_column("Work Order Item", "consumed_qty"):
            raw.transfers = frappe.db.sql("""
                SELECT wo.name AS work_order, woi.item_code, wo.wip_warehouse AS warehouse,
                       SUM(woi.transferred_qty - IFNULL(woi.consumed_qty, 0)) AS qty
                FROM `tabWork Order Item` woi
                INNER JOIN `tabWork Order` wo ON wo.name = woi.parent
                WHERE wo.docstatus = 1 AND wo.status IN %(statuses)s
                  AND wo.wip_warehouse IN %(warehouses)s
                  AND woi.transferred_qty > IFNULL(woi.consumed_qty, 0)
                GROUP BY wo.name, woi.item_code, wo.wip_warehouse
            """, {"statuses": OPEN_WO_STATUSES, "warehouses": tuple(warehouses)}, as_dict=True)

    # 6. which items take part: everything referenced above + tagged items
    codes = set()
    codes.update(b.item for b in raw.boms)
    codes.update(l.item_code for l in raw.bom_lines)
    codes.update(l.item_code for l in raw.order_lines)
    codes.update(l.item_code for l in raw.draft_lines)
    codes.update(w.production_item for w in raw.work_orders)
    codes.update(t.item_code for t in raw.transfers)
    flags = [f for f in ("custom_is_bip", "custom_is_colourant", "custom_monitor_shelf_level")
             if frappe.db.has_column("Item", f)]
    if flags:
        codes.update(frappe.db.sql_list("""
            SELECT name FROM `tabItem` WHERE disabled = 0 AND ({0})
        """.format(" OR ".join("IFNULL(`{0}`, 0) = 1".format(f) for f in flags))))
    codes.discard(None)
    codes.discard("")

    # 7. item masters
    item_cols = ", ".join([
        _col("Item", "custom_is_bip", "is_bip", "0", "i"),
        _col("Item", "custom_is_stainer", "is_stainer", "0", "i"),
        _col("Item", "custom_is_colourant", "is_colourant", "0", "i"),
        _col("Item", "custom_linked_bip", "linked_bip", "NULL", "i"),
        _col("Item", "custom_monitor_shelf_level", "monitor_shelf", "0", "i"),
        _col("Item", "custom_minimum_reorder_level", "mrl", "0", "i"),
        _col("Item", "custom_maximum_shelf_capacity", "max_shelf", "0", "i"),
        _col("Item", "custom_minimum_production_level", "min_production", "0", "i"),
        _col("Item", "custom_production_route_override", "route_override", "NULL", "i"),
    ])
    raw.item_rows = []
    for chunk in _chunks(sorted(codes)):
        raw.item_rows += frappe.db.sql("""
            SELECT i.name AS item_code, i.item_name, i.item_group, i.stock_uom,
                   IFNULL(u.must_be_whole_number, 0) AS whole_number,
                   i.default_bom, i.valuation_rate, i.last_purchase_rate, {cols}
            FROM `tabItem` i
            LEFT JOIN `tabUOM` u ON u.name = i.stock_uom
            WHERE i.name IN ({ph})
        """.format(cols=item_cols, ph=_in(chunk)), tuple(chunk), as_dict=True)

    # 8. bins in the Settings warehouses, for the items above
    raw.bins = []
    for chunk in _chunks(sorted(codes)):
        raw.bins += frappe.db.sql("""
            SELECT item_code, warehouse, actual_qty, reserved_qty_for_production,
                   ordered_qty, indented_qty, stock_value
            FROM `tabBin`
            WHERE warehouse IN %(warehouses)s AND item_code IN %(items)s
        """, {"warehouses": tuple(warehouses), "items": tuple(chunk)}, as_dict=True)

    # 9. item groups
    raw.item_groups = frappe.db.sql("""
        SELECT name, parent_item_group AS parent, {include}
        FROM `tabItem Group`
    """.format(include=_col("Item Group", "custom_include_in_production_card", "include", "0")), as_dict=True)

    return raw


# ------------------------------------------------------------------ shaping (pure)

def _settings_out(s, window_days=0):
    return {
        "fg_warehouse": s.fg_warehouse,
        "bulk_warehouses": list(s.bulk_warehouses),
        "colourant_warehouse": s.colourant_warehouse,
        "rm_warehouses": list(s.rm_warehouses),
        # the date window already chose the orders; the engine must not drop any of them
        "order_age_limit_days": max(cint(s.order_age_limit_days) or 30, window_days),
        "draft_age_limit_days": max(cint(s.draft_age_limit_days) or 14, window_days),
        "wo_age_limit_days": cint(s.wo_age_limit_days) or 30,
        "count_not_started": cint(s.count_not_started),
        "count_in_process": cint(s.count_in_process),
        "excess_fill_rule": s.excess_fill_rule or None,
        "tint_base_priority": s.tint_base_priority or None,
        "include_shelf_without_orders": cint(s.include_shelf_without_orders),
        "packaging_item_group": s.packaging_item_group or "Packaging Materials",
        "mass_balance_tolerance": flt(s.mass_balance_tolerance) if s.mass_balance_tolerance is not None else 2.0,
        "include_work_orders": cint(s.include_work_orders),
        "include_purchase_orders": cint(s.include_purchase_orders),
        "material_price_basis": s.material_price_basis or "Last Purchase Rate",
    }


def _valuation(raw, s):
    """item_code -> rate: average Bin value in the RM / colourant warehouses."""
    value_whs = set(s.rm_warehouses) | ({s.colourant_warehouse} if s.colourant_warehouse else set())
    totals = {}
    for b in raw.bins:
        if b.get("warehouse") in value_whs and flt(b.get("actual_qty")) > 0:
            t = totals.setdefault(b.get("item_code"), [0.0, 0.0])
            t[0] += flt(b.get("stock_value"))
            t[1] += flt(b.get("actual_qty"))
    return {code: v / q for code, (v, q) in totals.items() if q > 0 and v > 0}


def _orders(lines):
    orders = {}
    for ln in lines:
        cf = flt(ln.get("conversion_factor")) or 1.0
        qty = flt(ln.get("qty"))
        pending = qty - flt(ln.get("delivered_qty"))
        if not ln.get("item_code") or qty <= 0 or pending <= 0:
            continue
        so = orders.get(ln["sales_order"])
        if so is None:
            so = orders[ln["sales_order"]] = {
                "sales_order": ln["sales_order"],
                "customer": ln.get("customer"),
                "customer_name": ln.get("customer_name"),
                "kind": ln.get("kind") or "confirmed",
                "confirmed_on": _text(ln.get("confirmed_on")),
                "transaction_date": _text(ln.get("transaction_date")),
                "creation": _text(ln.get("creation")),
                "workflow_state": ln.get("workflow_state"),
                "priority": ln.get("priority") or "Normal",
                "lines": [],
            }
        so["lines"].append({
            "item_code": ln["item_code"],
            "qty": round(pending * cf, 6),
            "amount": round(flt(ln.get("base_net_amount")) * pending / qty, 2),
        })
    return list(orders.values())


def shape_payload(raw, s, mode, now, window_days=0):
    rates = _valuation(raw, s)
    items = []
    for it in raw.item_rows:
        code = it.get("item_code")
        items.append({
            "item_code": code,
            "item_name": it.get("item_name"),
            "item_group": it.get("item_group"),
            "stock_uom": it.get("stock_uom"),
            "whole_number": cint(it.get("whole_number")),
            "is_bip": cint(it.get("is_bip")),
            "is_stainer": cint(it.get("is_stainer")),
            "is_colourant": cint(it.get("is_colourant")),
            "linked_bip": it.get("linked_bip") or None,
            "monitor_shelf": cint(it.get("monitor_shelf")),
            "mrl": flt(it.get("mrl")),
            "max_shelf": flt(it.get("max_shelf")),
            "min_production": flt(it.get("min_production")),
            "route_override": it.get("route_override") or None,
            "default_bom": it.get("default_bom") or None,
            "valuation_rate": flt(rates.get(code) or it.get("valuation_rate")),
            "last_purchase_rate": flt(it.get("last_purchase_rate")),
        })

    lines_by_bom = {}
    for ln in raw.bom_lines:
        lines_by_bom.setdefault(ln.get("parent"), []).append({
            "idx": cint(ln.get("idx")),
            "item_code": ln.get("item_code"),
            "stock_qty": flt(ln.get("stock_qty")),
            "stock_uom": ln.get("stock_uom"),
            "bom_no": ln.get("bom_no") or None,
        })
    boms = [{
        "name": b.get("name"),
        "item": b.get("item"),
        "quantity": flt(b.get("quantity")),
        "process_loss_percentage": flt(b.get("process_loss_percentage")),
        "lines": lines_by_bom.get(b.get("name"), []),
    } for b in raw.boms]

    bins = [{
        "item_code": b.get("item_code"),
        "warehouse": b.get("warehouse"),
        "actual_qty": flt(b.get("actual_qty")),
        "reserved_qty_for_production": flt(b.get("reserved_qty_for_production")),
        "ordered_qty": flt(b.get("ordered_qty")),
        "indented_qty": flt(b.get("indented_qty")),
    } for b in raw.bins]

    transfers = [{"item_code": t.get("item_code"), "warehouse": t.get("warehouse"), "qty": flt(t.get("qty")),
                  "work_order": t.get("work_order")}
                 for t in raw.transfers if flt(t.get("qty")) > 0]

    wo_materials = [{"work_order": m.get("work_order"), "item_code": m.get("item_code"),
                     "warehouse": m.get("warehouse") or None, "qty": flt(m.get("qty"))}
                    for m in raw.wo_materials if m.get("item_code") and flt(m.get("qty")) > 0]

    work_orders = [{
        "name": w.get("name"),
        "production_item": w.get("production_item"),
        "status": w.get("status"),
        "qty": flt(w.get("qty")),
        "produced_qty": flt(w.get("produced_qty")),
        "planned_start_date": _text(w.get("planned_start_date")),
        "creation": _text(w.get("creation")),
        "bom_no": w.get("bom_no"),
    } for w in raw.work_orders]

    groups = [{"name": g.get("name"), "parent": g.get("parent") or None, "include": cint(g.get("include"))}
              for g in raw.item_groups]

    orders = _orders(list(raw.order_lines) + (list(raw.draft_lines) if mode == "drafts" else []))

    return {
        "now": _text(now),
        "mode": mode,
        "settings": _settings_out(s, window_days),
        "item_groups": groups,
        "items": items,
        "boms": boms,
        "bins": bins,
        "transfers": transfers,
        "orders": orders,
        "work_orders": work_orders,
        "wo_materials": wo_materials,
    }


def snapshot_counts(payload):
    return {
        "items": len(payload["items"]),
        "boms": len(payload["boms"]),
        "bins": len(payload["bins"]),
        "orders": sum(1 for o in payload["orders"] if o["kind"] == "confirmed"),
        "drafts": sum(1 for o in payload["orders"] if o["kind"] == "draft"),
        "order_lines": sum(len(o["lines"]) for o in payload["orders"]),
        "work_orders": len(payload["work_orders"]),
        "wo_materials": len(payload.get("wo_materials") or []),
        "transfers": len(payload["transfers"]),
    }


# ------------------------------------------------------------------ entry point

MAX_WINDOW_DAYS = 366


def default_window(s=None):
    """(from_date, to_date): the last 'Count Confirmed Orders From Last (Days)' days up to today."""
    s = s or get_settings()
    today = getdate(now_datetime())
    return add_days(today, -max(1, cint(s.order_age_limit_days) or 14)), today


def resolve_window(from_date=None, to_date=None, s=None):
    """Validated (from_date, to_date, is_default). Never in the future, never longer than a year."""
    d_from, d_to = default_window(s)
    f = getdate(from_date) if from_date else d_from
    t = getdate(to_date) if to_date else d_to
    today = getdate(now_datetime())
    t = min(t, today)
    if f > t:
        f, t = t, f
    if (t - f).days > MAX_WINDOW_DAYS:
        f = add_days(t, -MAX_WINDOW_DAYS)
    return f, t, (f == d_from and t == d_to)


def build_snapshot(mode="confirmed", from_date=None, to_date=None):
    """(payload for the engine, extra facts for the page)."""
    mode = "drafts" if mode == "drafts" else "confirmed"
    s = get_settings()
    check_settings(s)
    f, t, is_default = resolve_window(from_date, to_date, s)
    now = now_datetime()
    raw = fetch_raw(s, mode, f, t)
    window_days = (getdate(now) - f).days + 1
    payload = shape_payload(raw, s, mode, now, window_days)
    extra = {"from_date": str(f), "to_date": str(t), "is_default_window": is_default,
             "earlier_orders": raw.earlier_orders}
    return payload, extra
