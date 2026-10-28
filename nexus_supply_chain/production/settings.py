import frappe
from frappe.utils import cint, flt

SETTINGS_DOCTYPE = "Nexus Production Settings"


def get_settings():
    """
    Nexus Production Settings as a plain dict, with the Table MultiSelect
    child tables flattened to lists. Uses the cached single doc, which Frappe
    invalidates automatically whenever the settings are saved.
    """
    doc = frappe.get_cached_doc(SETTINGS_DOCTYPE)

    def rows(fieldname, link_field):
        return [r.get(link_field) for r in (doc.get(fieldname) or []) if r.get(link_field)]

    return frappe._dict(
        fg_warehouse=doc.fg_warehouse,
        bulk_warehouses=rows("bulk_warehouses", "warehouse"),
        colourant_warehouse=doc.colourant_warehouse,
        rm_warehouses=rows("rm_warehouses", "warehouse"),
        cook_target_warehouse=doc.cook_target_warehouse,
        fill_target_warehouse=doc.fill_target_warehouse,
        packaging_item_group=doc.packaging_item_group,
        mass_balance_tolerance=flt(doc.mass_balance_tolerance),
        order_age_limit_days=cint(doc.order_age_limit_days),
        draft_age_limit_days=cint(doc.draft_age_limit_days),
        draft_workflow_states=rows("draft_workflow_states", "workflow_state"),
        include_work_orders=cint(doc.get("include_work_orders")),
        wo_age_limit_days=cint(doc.wo_age_limit_days),
        count_not_started=cint(doc.count_not_started),
        count_in_process=cint(doc.count_in_process),
        include_purchase_orders=cint(doc.get("include_purchase_orders")),
        material_price_basis=doc.get("material_price_basis") or "Last Purchase Rate",
        pending_stale_days=max(1, cint(doc.get("pending_stale_days")) or 14),
        excess_fill_rule=doc.excess_fill_rule,
        tint_base_priority=doc.tint_base_priority,
        include_shelf_without_orders=cint(doc.include_shelf_without_orders),
        refresh_interval_minutes=cint(doc.refresh_interval_minutes),
        night_refresh_interval_minutes=cint(doc.night_refresh_interval_minutes),
        night_start=str(doc.night_start or ""),
        night_end=str(doc.night_end or ""),
        cache_ttl_seconds=cint(doc.cache_ttl_seconds),
        engine_url=doc.engine_url,
        engine_timeout_seconds=cint(doc.engine_timeout_seconds),
        auto_replace_sub_bom_links=cint(doc.auto_replace_sub_bom_links),
        allowed_roles=rows("allowed_roles", "role"),
    )


def user_has_access(user=None):
    """True if the user holds any role listed in Settings > Allowed Roles.
    System Manager always has access so the board can never lock everyone out."""
    user = user or frappe.session.user
    roles = set(frappe.get_roles(user))
    if "System Manager" in roles:
        return True
    allowed = set(get_settings().allowed_roles)
    return bool(roles & allowed)


def assert_access(user=None):
    if not user_has_access(user):
        frappe.throw(
            frappe._("You do not have access to Nexus Production. Ask a System Manager to add your role in Nexus Production Settings."),
            frappe.PermissionError,
        )
