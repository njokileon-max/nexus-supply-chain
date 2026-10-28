"""
Nexus Production: cutover check (Batch 7). READ-ONLY, System Manager only.

Run from the ERPNext desk (browser console), on the test server first, then live:

    frappe.call({method: 'nexus_supply_chain.production.diagnostics.cutover_check'})
        .then(r => console.log(JSON.stringify(r.message, null, 1)))

Optional args: {include_drafts: 1} also times the Drafts board,
               {from_date: '2026-10-01', to_date: '2026-10-18'} checks another window.

It answers four questions:
  1. performance  How long do the snapshot, the engine and a cached page read take,
                  and how big is the upload? Does FastAPI accept compressed uploads?
  2. orders       Is every order on the board allowed to be there (submitted, open,
                  nothing delivered or invoiced, no Delivery Note / Sales Invoice), and
                  does every submitted order of the window that is NOT on the board
                  have a reason?
  3. roles        Who can open the board, who can create Work Orders / Material
                  Requests from it, and does every whitelisted production method
                  check access?
  4. leftovers    Is the old v1 / reservation code gone, and are the Batch 6 hooks in place?

Each check is "pass", "warn" or "fail"; "overall" is the worst of them.
It calls the engine directly (one or two calls) and never writes anything.
"""

import gzip
import inspect
import json
import time

import frappe
from frappe.utils import add_days, cint, flt, getdate

from nexus_supply_chain.production import service
from nexus_supply_chain.production.settings import get_settings, user_has_access
from nexus_supply_chain.production.snapshot import CLOSED_SO_STATUSES, build_snapshot, resolve_window, snapshot_counts
from nexus_supply_chain.production.snapshot import _company as snapshot_company

# performance budgets (warn above these)
SNAPSHOT_WARN_MS = 5000
ENGINE_WARN_MS = 10000
CACHED_READ_WARN_MS = 300
UPLOAD_WARN_BYTES = 3 * 1024 * 1024

SAMPLE_IN_PLAN = 300          # orders on the board re-checked one by one
MAX_WINDOW_ORDERS = 2000      # submitted orders of the window classified
MAX_USERS = 500

WATCHED_DOCTYPES = ("Sales Order", "Delivery Note", "Sales Invoice", "Work Order", "Material Request",
                    "Purchase Order", "Purchase Receipt", "Stock Entry", "Stock Reconciliation", "BOM", "Item")
PRODUCTION_MODULES = ("nexus_supply_chain.production.api", "nexus_supply_chain.production.actions",
                      "nexus_supply_chain.production.diagnostics", "nexus_supply_chain.production.pending")
LEGACY_API_FUNCTIONS = ("get_nexus_production_data", "get_nexus_live_inventory")
LEGACY_HOOK_MARKERS = ("reservation_hooks", "clear_board_cache_on_change", "publish_realtime_production")


class _Report:
    RANK = {"pass": 0, "warn": 1, "fail": 2}

    def __init__(self):
        self.checks = []

    def add(self, area, name, status, detail):
        self.checks.append({"area": area, "check": name, "status": status, "detail": detail})

    @property
    def overall(self):
        return max((c["status"] for c in self.checks), key=self.RANK.get, default="pass")


def _ms(start):
    return round((time.perf_counter() - start) * 1000, 1)


def _chunks(values, size=500):
    values = list(values)
    for i in range(0, len(values), size):
        yield values[i:i + size]


# ------------------------------------------------------------------ 1. performance

def _engine_call(payload, s):
    """The engine call service.call_engine makes, but reporting whether the compressed upload was accepted."""
    url, headers, timeout = service._engine_url(s), service._engine_headers(), service._timeout(s)
    body = json.dumps(payload, default=str, separators=(",", ":")).encode("utf-8")
    packed = gzip.compress(body, compresslevel=6)
    started = time.perf_counter()
    response = service._post(url, packed, dict(headers, **{"Content-Encoding": "gzip"}), timeout)
    gzip_ok = response.status_code == 200
    if response.status_code in (400, 415, 422):
        response = service._post(url, body, headers, timeout)
    if response.status_code != 200:
        raise service.EngineError(service._explain(response))
    round_trip = _ms(started)
    board = response.json()
    engine_ms = flt(board.get("timing_ms"))
    # elapsed = request sent -> reply headers received (upload + FastAPI work);
    # the rest of the round trip is downloading the reply
    to_headers = round(response.elapsed.total_seconds() * 1000, 1)
    return board, {"round_trip_ms": round_trip, "engine_ms": engine_ms,
                   "upload_and_server_ms": to_headers, "download_ms": round(round_trip - to_headers, 1),
                   "network_and_overhead_ms": round(round_trip - engine_ms, 1),
                   "reply_mb_unpacked": round(len(response.content) / 1048576, 2),
                   "reply_compressed": "gzip" in (response.headers.get("Content-Encoding") or ""),
                   "upload_bytes_plain": len(body), "upload_bytes_gzip": len(packed),
                   "gzip_upload_accepted": gzip_ok, "timeout_setting_s": timeout}


def _latency(s):
    """Three tiny requests: the network's own delay, without any data."""
    url = service._engine_url(s).rstrip("/") + "/health"
    times = []
    for _ in range(3):
        t = time.perf_counter()
        try:
            service._http().get(url, headers=service._engine_headers(), timeout=(5, 10))
            times.append(_ms(t))
        except Exception as e:
            return {"error": str(e)}
    return {"ms": times, "best_ms": min(times)}


def _wait_for_background(mode, from_date, to_date, s, before, limit_s=150):
    deadline = time.time() + limit_s
    k = service._keys(mode, s, from_date, to_date)
    while time.time() < deadline:
        time.sleep(2)
        last = service._read(k.last) or {}
        if last.get("_calculated_at") and last.get("_calculated_at") != before:
            return last.get("_calculated_at")
    return None


def check_performance(report, mode, from_date, to_date, s):
    started = time.perf_counter()
    payload, window = build_snapshot(mode, from_date, to_date)
    snapshot_ms = _ms(started)
    report.add("performance", "{0}: snapshot from the database".format(mode),
               "warn" if snapshot_ms > SNAPSHOT_WARN_MS else "pass",
               {"ms": snapshot_ms, "budget_ms": SNAPSHOT_WARN_MS, "counts": snapshot_counts(payload)})

    if mode == "confirmed":
        report.add("performance", "network delay to the engine (empty requests)", "pass", _latency(s))

    try:
        board, engine = _engine_call(payload, s)
    except service.EngineError as e:
        report.add("performance", "{0}: engine round trip".format(mode), "fail",
                   {"error": str(e), "timeout_setting_s": service._timeout(s)})
        return None
    report.add("performance", "{0}: engine round trip".format(mode),
               "warn" if engine["round_trip_ms"] > ENGINE_WARN_MS else "pass",
               dict(engine, budget_ms=ENGINE_WARN_MS))
    report.add("performance", "{0}: upload size".format(mode),
               "warn" if engine["upload_bytes_gzip"] > UPLOAD_WARN_BYTES else "pass",
               {"plain_mb": round(engine["upload_bytes_plain"] / 1048576, 2),
                "gzip_mb": round(engine["upload_bytes_gzip"] / 1048576, 2)})
    if not engine["gzip_upload_accepted"]:
        report.add("performance", "compressed upload", "warn",
                   "FastAPI refused the gzip upload and the snapshot was re-sent uncompressed (about 10x bigger). "
                   "Replace the production-plan v2 block in main.py with the Batch 7 block.")

    # the actions' path: a board from the current data, while you wait
    t = time.perf_counter()
    fresh = service.get_board(mode, refresh=True, from_date=from_date, to_date=to_date, background=False)
    meta = fresh["meta"]
    report.add("performance", "{0}: fresh board while waiting (Work Order / Material Request path)".format(mode),
               "fail" if meta["stale"] else ("warn" if _ms(t) > ENGINE_WARN_MS + SNAPSHOT_WARN_MS else "pass"),
               {"ms": _ms(t), "error": meta.get("error"), "timeout_setting_s": service._timeout(s)})

    # the page's path: must come from the cache at once
    t = time.perf_counter()
    read = service.get_board(mode, from_date=from_date, to_date=to_date)
    ms = _ms(t)
    report.add("performance", "{0}: page read".format(mode),
               "fail" if read["meta"]["stale"] else ("warn" if ms > CACHED_READ_WARN_MS else "pass"),
               {"ms": ms, "cached": read["meta"]["cached"], "refreshing": read["meta"].get("refreshing"),
                "budget_ms": CACHED_READ_WARN_MS})

    # background recalculation: needs the site's background workers
    before = (service._read(service._keys(mode, s, from_date, to_date).last) or {}).get("_calculated_at")
    t = time.perf_counter()
    service.get_board(mode, refresh=True, from_date=from_date, to_date=to_date)      # queues the job
    done_at = _wait_for_background(mode, from_date, to_date, s, before)
    report.add("performance", "{0}: background recalculation".format(mode), "pass" if done_at else "fail",
               {"ms": _ms(t), "calculated_at": done_at} if done_at else
               "No background worker finished the job within 150 s. Check that the site's workers run "
               "(bench doctor / the hosting panel), and the Error Log for 'Nexus Production board failed'.")
    return payload, window, board


# ------------------------------------------------------------------ 2. orders

def _linked(doctype, field, names):
    found = set()
    for chunk in _chunks(names):
        found.update(frappe.get_all(doctype, filters={field: ["in", chunk], "docstatus": ["<", 2]},
                                    pluck=field, distinct=True))
    return found


def _confirmed_at():
    return "COALESCE(so.custom_confirmed_on, so.transaction_date)" \
        if frappe.db.has_column("Sales Order", "custom_confirmed_on") else "so.transaction_date"


def check_orders(report, payload, window, board, s):
    company = snapshot_company(s)
    in_plan = sorted({o["sales_order"] for o in payload["orders"] if o["kind"] == "confirmed"})

    # a) the board shows every order the snapshot sent
    on_board = {o["sales_order"] for o in board.get("orders") or []} | \
               {o["sales_order"] for o in board.get("older_orders") or []}
    missing = [so for so in in_plan if so not in on_board]
    report.add("orders", "every order sent is on the board", "fail" if missing else "pass",
               {"orders_sent": len(in_plan), "missing_from_board": missing[:50]})

    line_status = {}
    for o in board.get("orders") or []:
        for ln in o.get("lines") or []:
            line_status[ln.get("status")] = line_status.get(ln.get("status"), 0) + 1
    unknown = line_status.get("unknown item", 0)
    report.add("orders", "order lines by status", "warn" if unknown else "pass",
               {"lines": line_status, "excluded_lines": len(board.get("excluded_lines") or []),
                "not_manufactured_items": len(board.get("not_manufactured") or [])})

    # b) every order on the board is allowed to be there (checked again, one by one)
    sample = in_plan[:SAMPLE_IN_PLAN]
    bad = []
    if sample:
        rows = {r.name: r for r in frappe.get_all(
            "Sales Order", filters={"name": ["in", sample]},
            fields=["name", "docstatus", "status", "per_delivered", "per_billed", "company"])}
        with_dn = _linked("Delivery Note Item", "against_sales_order", sample)
        with_si = _linked("Sales Invoice Item", "sales_order", sample)
        for so in sample:
            r = rows.get(so)
            reasons = []
            if not r:
                reasons.append("not found")
            else:
                if r.docstatus != 1:
                    reasons.append("not submitted")
                if r.status in CLOSED_SO_STATUSES:
                    reasons.append("status " + r.status)
                if flt(r.per_delivered) > 0:
                    reasons.append("{0}% delivered".format(flt(r.per_delivered)))
                if flt(r.per_billed) > 0:
                    reasons.append("{0}% billed".format(flt(r.per_billed)))
                if company and r.company != company:
                    reasons.append("company " + r.company)
            if so in with_dn:
                reasons.append("has a Delivery Note")
            if so in with_si:
                reasons.append("has a Sales Invoice")
            if reasons:
                bad.append({"sales_order": so, "why": reasons})
    report.add("orders", "orders on the board follow the rule", "fail" if bad else "pass",
               {"checked": len(sample), "of": len(in_plan), "should_not_be_there": bad[:50]})

    # c) every submitted order of the window NOT on the board has a reason
    f, t = getdate(window["from_date"]), getdate(window["to_date"])
    company_filter = "AND so.company = %(company)s" if company else ""
    window_orders = frappe.db.sql("""
        SELECT so.name, so.status, so.per_delivered, so.per_billed
        FROM `tabSales Order` so
        WHERE so.docstatus = 1 AND {at} >= %(from)s AND {at} < %(to_next)s {company_filter}
        ORDER BY so.name LIMIT %(limit)s
    """.format(at=_confirmed_at(), company_filter=company_filter),
        {"from": f, "to_next": add_days(t, 1), "company": company, "limit": MAX_WINDOW_ORDERS}, as_dict=True)

    planned = set(in_plan)
    left_out = [r for r in window_orders if r.name not in planned]
    names = [r.name for r in left_out]
    with_dn = _linked("Delivery Note Item", "against_sales_order", names) if names else set()
    with_si = _linked("Sales Invoice Item", "sales_order", names) if names else set()
    dropship = "AND IFNULL(delivered_by_supplier, 0) = 0" \
        if frappe.db.has_column("Sales Order Item", "delivered_by_supplier") else ""
    pending = set()
    for chunk in _chunks(names):
        pending.update(r[0] for r in frappe.db.sql("""
            SELECT DISTINCT parent FROM `tabSales Order Item`
            WHERE parent IN %(names)s AND qty > IFNULL(delivered_qty, 0) {dropship}
        """.format(dropship=dropship), {"names": tuple(chunk)}))

    reasons, unexplained = {}, []
    for r in left_out:
        if r.status in CLOSED_SO_STATUSES:
            why = "closed / on hold / completed"
        elif flt(r.per_delivered) > 0 or flt(r.per_billed) > 0:
            why = "partly or fully delivered or billed"
        elif r.name in with_dn:
            why = "Delivery Note exists (draft or submitted)"
        elif r.name in with_si:
            why = "Sales Invoice exists (draft or submitted)"
        elif r.name not in pending:
            why = "nothing left to deliver (or supplier-delivered only)"
        else:
            why = None
            unexplained.append(r.name)
        if why:
            reasons[why] = reasons.get(why, 0) + 1
    report.add("orders", "orders of the window left off the board have a reason",
               "fail" if unexplained else ("warn" if len(window_orders) >= MAX_WINDOW_ORDERS else "pass"),
               {"window": [str(f), str(t)], "submitted_in_window": len(window_orders), "on_board": len(in_plan),
                "left_off_by_reason": reasons, "left_off_without_reason": unexplained[:50],
                "earlier_orders_before_window": window.get("earlier_orders")})


# ------------------------------------------------------------------ 3. roles

def check_roles(report, s):
    allowed = list(s.allowed_roles)
    report.add("roles", "allowed roles", "pass" if allowed else "warn",
               {"allowed_roles": allowed, "note": "System Manager always has access."
                if allowed else "No roles listed: only System Managers can open the board."})

    managers = {"System Manager", "Manufacturing Manager"}
    users = frappe.get_all("User", filters={"enabled": 1, "user_type": "System User"},
                           pluck="name", limit=MAX_USERS)
    matrix, cannot_act = [], []
    for user in users:
        if user in ("Administrator", "Guest") or not user_has_access(user):
            continue
        roles = set(frappe.get_roles(user))
        row = {"user": user,
               "work_order": bool(frappe.has_permission("Work Order", "create", user=user)),
               "material_request": bool(frappe.has_permission("Material Request", "create", user=user)),
               "settings": bool(roles & managers)}
        matrix.append(row)
        if not (row["work_order"] and row["material_request"]):
            cannot_act.append(user)
    report.add("roles", "who can open the board", "pass",
               {"users_with_access": len(matrix), "of_enabled_users": len(users), "users": matrix[:100]})
    if cannot_act:
        report.add("roles", "board users who cannot create documents", "warn",
                   {"users": cannot_act[:50], "note": "They see the buttons, and get a permission message when "
                    "they click. Give them create rights on Work Order / Material Request, or accept it."})

    # every whitelisted production method must check access
    unguarded = []
    for module_name in PRODUCTION_MODULES:
        try:
            module = frappe.get_module(module_name)
        except Exception:
            continue
        for name, fn in inspect.getmembers(module, inspect.isfunction):
            if fn.__module__ != module_name or fn not in frappe.whitelisted:
                continue
            source = inspect.getsource(fn)
            if not any(guard in source for guard in ("assert_access(", "only_for(")):
                unguarded.append("{0}.{1}".format(module_name, name))
    report.add("roles", "every production method checks access", "fail" if unguarded else "pass",
               {"unguarded": unguarded})


# ------------------------------------------------------------------ 4. leftovers

def _strings(value):
    """Every text in a hooks structure, flattened. Other apps may register doc_events
    under tuple keys (several doctypes at once), which json.dumps cannot handle."""
    out = []
    if isinstance(value, dict):
        for k, v in value.items():
            out.extend(_strings(k))
            out.extend(_strings(v))
    elif isinstance(value, (list, tuple, set)):
        for v in value:
            out.extend(_strings(v))
    elif value is not None:
        out.append(str(value))
    return out


def _hook_strings(hook):
    return "\n".join(_strings(frappe.get_hooks(hook) or {}))


def _handlers_for(doc_events, doctype):
    """Handlers registered for a doctype, including under '*' and tuple keys."""
    found = []
    for key, events in (doc_events or {}).items():
        keys = key if isinstance(key, (list, tuple, set)) else (key,)
        if doctype in keys or "*" in keys:
            found.extend(_strings(events))
    return found


def check_leftovers(report):
    ok, message, version = service.health_check()
    report.add("leftovers", "engine reachable", "pass" if ok else "fail", {"message": message, "version": version})

    api = frappe.get_module("nexus_supply_chain.api")
    still_there = [n for n in LEGACY_API_FUNCTIONS if hasattr(api, n)]
    report.add("leftovers", "old v1 / reservation functions removed from api.py",
               "warn" if still_there else "pass", {"still_present": still_there})

    events = _hook_strings("doc_events")
    old_hooks = [m for m in LEGACY_HOOK_MARKERS if m in events]
    report.add("leftovers", "no old hooks registered", "warn" if old_hooks else "pass", {"found": old_hooks})

    doc_events = frappe.get_hooks("doc_events") or {}
    signal = "nexus_supply_chain.production.signals.on_doc_change"
    unwatched = [dt for dt in WATCHED_DOCTYPES if signal not in _handlers_for(doc_events, dt)]
    report.add("leftovers", "live refresh hooks (Batch 6)", "warn" if unwatched else "pass",
               {"doctypes_without_signal": unwatched})
    cron = "nexus_supply_chain.production.signals.flush_pending" in _hook_strings("scheduler_events")
    report.add("leftovers", "live refresh catch-up job (Batch 6)", "pass" if cron else "warn",
               {"flush_pending_scheduled": cron})

    if frappe.db.exists("DocType", "Nexus Inventory Reservation"):
        table = frappe.db.count("Nexus Inventory Reservation")
        report.add("leftovers", "Nexus Inventory Reservation doctype", "pass",
                   {"records": table, "note": "Nothing reads it any more. Keep it until you have exported the "
                    "records you want; deleting the doctype deletes its table."})


# ------------------------------------------------------------------ entry point

@frappe.whitelist(methods=["POST"])
def cutover_check(include_drafts=0, from_date=None, to_date=None):
    frappe.only_for("System Manager")
    s = get_settings()
    f, t, _ = resolve_window(from_date, to_date, s)
    report = _Report()
    started = time.perf_counter()

    def run(area, fn, *args):
        try:
            return fn(report, *args)
        except Exception as e:
            report.add(area, "check crashed", "fail", "{0}: {1}".format(type(e).__name__, e))
            frappe.log_error(title="Nexus Production cutover check: " + area, message=frappe.get_traceback())
            return None

    result = run("performance", check_performance, "confirmed", f, t, s)
    if result:
        run("orders", check_orders, *result, s)
    if cint(include_drafts):
        run("performance", check_performance, "drafts", f, t, s)
    run("roles", check_roles, s)
    run("leftovers", check_leftovers)

    return {"overall": report.overall, "window": [str(f), str(t)], "took_ms": _ms(started),
            "site": frappe.local.site, "checks": report.checks}
