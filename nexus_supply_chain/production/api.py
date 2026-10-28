"""
Nexus Production: methods called by the Production Command page and by
Nexus Production Settings.

    nexus_supply_chain.production.api.get_board          page (Batch 4)
    nexus_supply_chain.production.api.get_working        "Show the working" on a card
    nexus_supply_chain.production.api.test_connection    Settings button
    nexus_supply_chain.production.api.preview_board      Settings button
    nexus_supply_chain.production.api.download_snapshot  Settings button (System Manager)

All of them only READ data. Access: Settings > Allowed Roles (System Manager always).
"""

import json
from collections import Counter

import frappe
from frappe.utils import cint, now_datetime

from nexus_supply_chain.production import service
from nexus_supply_chain.production.settings import assert_access, get_settings
from nexus_supply_chain.production.snapshot import default_window

MANAGERS = ("System Manager", "Manufacturing Manager")
PRIORITY_NAMES = {0: "P0 Urgent", 1: "P1 Order short", 2: "P2 Shelf / batch refill", 3: "P3 Covered"}


def _mode(mode):
    return "drafts" if mode == "drafts" else "confirmed"


@frappe.whitelist()
def get_board(mode="confirmed", refresh=0, from_date=None, to_date=None):
    """The whole board: {"board": {...engine output...}, "meta": {...}, "page": {...}}."""
    assert_access()
    result = service.get_board(_mode(mode), refresh=bool(cint(refresh)), from_date=from_date, to_date=to_date)
    s = get_settings()
    d_from, d_to = default_window(s)
    result["page"] = {
        "refresh_minutes": cint(s.refresh_interval_minutes) or 5,
        "night_refresh_minutes": cint(s.night_refresh_interval_minutes) or 15,
        "night_start": s.night_start or "",
        "night_end": s.night_end or "",
        "default_from": str(d_from),
        "default_to": str(d_to),
        "can_configure": bool(set(frappe.get_roles()) & set(MANAGERS)),
        "server_time": str(now_datetime()),
    }
    return result


@frappe.whitelist()
def get_working(item_code, mode="confirmed", from_date=None, to_date=None):
    """Everything behind one card's figure, taken from the same board the page shows."""
    assert_access()
    result = service.get_board(_mode(mode), from_date=from_date, to_date=to_date)
    board = result["board"]

    card = next((c for c in board.get("bulks", []) + board.get("items", []) if c.get("item_code") == item_code), None)
    material = next((m for m in board.get("materials", []) if m.get("item_code") == item_code), None)
    if card is None and material is None:
        frappe.throw("{0} is not on the current production board.".format(item_code), title="Not on the board")

    related = []
    if card is not None:
        wanted = set(card.get("children") or [])
        if card.get("home_bulk") and card.get("home_bulk") != item_code:
            wanted.add(card["home_bulk"])
        wanted.update(card.get("used_as_base_by") or [])
        for other in board.get("bulks", []) + board.get("items", []):
            if other.get("item_code") in wanted:
                related.append({k: other.get(k) for k in (
                    "item_code", "item_name", "route", "priority", "priority_label", "demand", "short",
                    "production_total", "cook", "home_bulk")})

    return {
        "item_code": item_code,
        "card": card,
        "material": material,
        "related": related,
        "needs_setup": [i for i in board.get("needs_setup", []) if i.get("item") == item_code],
        "settings_used": board.get("settings_used"),
        "meta": result["meta"],
    }


@frappe.whitelist()
def test_connection():
    frappe.only_for(MANAGERS)
    ok, message, version = service.health_check()
    return {"ok": ok, "message": message, "engine_version": version,
            "secret_found": bool(service.engine_secret())}


@frappe.whitelist()
def preview_board(mode="confirmed"):
    """A fresh calculation, summarised for the Settings page."""
    frappe.only_for(MANAGERS)
    result = service.get_board(_mode(mode), refresh=True, background=False)
    board, meta = result["board"], result["meta"]
    cards = board.get("bulks", []) + board.get("items", [])

    setup = Counter()
    setup_in_plan = Counter()
    titles = {}
    for issue in board.get("needs_setup", []):
        setup[issue["code"]] += 1
        titles[issue["code"]] = (issue.get("severity"), issue.get("title"))
        if issue.get("in_plan"):
            setup_in_plan[issue["code"]] += 1
    setup_rows = sorted(
        [{"code": c, "severity": titles[c][0], "title": titles[c][1], "count": n, "in_plan": setup_in_plan[c]}
         for c, n in setup.items()],
        key=lambda r: ({"error": 0, "warning": 1}.get(r["severity"], 2), -r["count"]))

    top = sorted(cards, key=lambda c: (c.get("priority", 3), c.get("oldest_order_at") or "9999"))[:10]
    return {
        "meta": meta,
        "summary": board.get("summary", {}),
        "priority_names": PRIORITY_NAMES,
        "needs_setup": setup_rows,
        "top_cards": [{
            "item_code": c.get("item_code"), "item_name": c.get("item_name"), "route": c.get("route"),
            "group": c.get("group"), "priority": c.get("priority"), "uom": c.get("uom"),
            "make": c.get("cook") if "cook" in c else c.get("production_total"),
            "short": c.get("short"), "oldest_order_at": c.get("oldest_order_at"),
        } for c in top],
        "top_materials": [{
            "item_code": m.get("item_code"), "item_name": m.get("item_name"), "kind": m.get("kind"),
            "uom": m.get("uom"), "short": m.get("short"), "shortage_value": m.get("shortage_value"),
        } for m in board.get("materials", []) if (m.get("short") or 0) > 0][:10],
        "older_orders": ((meta.get("window") or {}).get("earlier_orders")) or 0,
        "older_work_orders": len(board.get("older_work_orders", [])),
    }


@frappe.whitelist()
def download_snapshot(mode="confirmed"):
    """The exact data sent to the engine, as a JSON file. For reproducing a figure."""
    frappe.only_for("System Manager")
    payload, _window = service.build_snapshot(_mode(mode))
    frappe.response["filename"] = "nexus-production-snapshot-{0}-{1}.json".format(
        _mode(mode), now_datetime().strftime("%Y%m%d-%H%M"))
    frappe.response["filecontent"] = json.dumps(payload, indent=1, default=str)
    frappe.response["type"] = "download"
