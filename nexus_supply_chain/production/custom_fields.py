"""
Custom fields owned by Nexus Production.

Why code instead of a fixtures JSON: several of these fields already exist on
your site (created through Customize Form). A fixtures import would overwrite
them wholesale, including their position on the form. ensure_custom_fields()
instead:
  * creates a field only if it is missing (using insert_after for placement),
  * for a field that already exists, updates ONLY its behaviour properties
    (label, depends_on, description, options, filters ...) and never moves it
    or changes its type.
It is idempotent and runs on every `bench migrate` (after_migrate hook), so a
change made here reaches every site automatically.
"""

import json

import frappe

ROUTE_OVERRIDE_OPTIONS = "\n".join([
    "",
    "Bulk Fill",
    "Blend Fill",
    "Tint from Bulk",
    "Tint from Packed Base",
    "Colourant Fill",
    "Stainer Fill",
    "Repack",
    "Exclude from Production Card",
])

CUSTOM_FIELDS = {
    "Item": [
        {
            "fieldname": "custom_is_bip",
            "label": "Is BIP",
            "fieldtype": "Check",
            "insert_after": "default_bom",
            "description": "Bulk Intermediate Product: cooked in kg and filled, tinted or blended into other items.",
        },
        {
            "fieldname": "custom_is_stainer",
            "label": "Is Stainer",
            "fieldtype": "Check",
            "insert_after": "custom_is_bip",
            "depends_on": "eval:doc.custom_is_bip",
            "description": "A bulk whose job is to colour other bulks or tints (e.g. universal pastes).",
        },
        {
            "fieldname": "custom_is_colourant",
            "label": "Is Colourant",
            "fieldtype": "Check",
            "insert_after": "custom_is_stainer",
            "depends_on": "eval:!doc.custom_is_bip",
            "description": "A tinting concentrate added in small amounts per unit (e.g. CMDU...).",
        },
        {
            "fieldname": "custom_linked_bip",
            "label": "Linked BIP",
            "fieldtype": "Link",
            "options": "Item",
            "insert_after": "custom_is_colourant",
            "depends_on": "eval:!doc.custom_is_bip && !doc.custom_is_colourant",
            "description": "Optional. Only decides which bulk card a finished good sits on when its BOM uses two bulks.",
            "link_filters": json.dumps([["Item", "custom_is_bip", "=", 1]]),
        },
        {
            "fieldname": "custom_monitor_shelf_level",
            "label": "Monitor Shelf Level",
            "fieldtype": "Check",
            "insert_after": "custom_linked_bip",
            "depends_on": "eval:!doc.custom_is_bip && !doc.custom_is_colourant",
            "description": "Fast mover: refill the shelf to Maximum Shelf Capacity once stock falls to the Minimum Reorder Level.",
        },
        {
            "fieldname": "custom_minimum_reorder_level",
            "label": "Minimum Reorder Level",
            "fieldtype": "Float",
            "insert_after": "custom_monitor_shelf_level",
            "depends_on": "eval:doc.custom_monitor_shelf_level",
            "mandatory_depends_on": "eval:doc.custom_monitor_shelf_level",
            "description": "In stock units. When free stock after orders is at or below this, the shelf is refilled.",
        },
        {
            "fieldname": "custom_maximum_shelf_capacity",
            "label": "Maximum Shelf Capacity",
            "fieldtype": "Float",
            "insert_after": "custom_minimum_reorder_level",
            "depends_on": "eval:doc.custom_monitor_shelf_level",
            "mandatory_depends_on": "eval:doc.custom_monitor_shelf_level",
            "description": "In stock units. Refill target, and the ceiling for absorbing excess bulk.",
        },
        {
            "fieldname": "custom_minimum_production_level",
            "label": "Minimum Production Level",
            "fieldtype": "Float",
            "insert_after": "custom_maximum_shelf_capacity",
            "depends_on": "eval:doc.custom_is_bip || doc.custom_is_colourant",
            "description": "Smallest batch worth making, in the item's stock unit (kg for bulks). Cooks are rounded up to this.",
        },
        {
            "fieldname": "custom_production_route_override",
            "label": "Production Route Override",
            "fieldtype": "Select",
            "options": ROUTE_OVERRIDE_OPTIONS,
            "insert_after": "custom_minimum_production_level",
            "depends_on": "eval:!doc.custom_is_bip",
            "description": "Leave blank: the route is read from the default BOM. Use only to correct a wrong classification or to exclude an item.",
        },
    ],
    "Item Group": [
        {
            "fieldname": "custom_include_in_production_card",
            "label": "Include in Production Card",
            "fieldtype": "Check",
            "insert_after": "is_group",
            "description": "Show this group as a section on the Nexus Production page. Sub-groups roll up to the nearest ticked parent.",
        },
    ],
    "Sales Order": [
        {
            "fieldname": "custom_confirmed_on",
            "label": "Confirmed On",
            "fieldtype": "Datetime",
            "insert_after": "delivery_date",
            "read_only": 1,
            "no_copy": 1,
            "allow_on_submit": 1,
            "description": "Set automatically when the order is submitted (Order Confirmed). Used as the first-in-first-out clock for production.",
        },
        {
            "fieldname": "custom_production_priority",
            "label": "Production Priority",
            "fieldtype": "Select",
            "options": "Normal\nUrgent",
            "default": "Normal",
            "insert_after": "custom_confirmed_on",
            "allow_on_submit": 1,
            "no_copy": 1,
            "in_standard_filter": 1,
            "description": "Urgent orders jump to the top of the production board (P0).",
        },
    ],
}

# Properties that may be changed on a field that already exists.
# insert_after and fieldtype are deliberately NOT in this list.
BEHAVIOUR_PROPS = (
    "label", "description", "depends_on", "mandatory_depends_on", "options",
    "default", "read_only", "no_copy", "allow_on_submit", "in_standard_filter",
    "link_filters",
)


def ensure_custom_fields():
    from frappe.custom.doctype.custom_field.custom_field import create_custom_field

    cf_meta = frappe.get_meta("Custom Field")
    for dt, fields in CUSTOM_FIELDS.items():
        changed_dt = False
        for df in fields:
            existing = frappe.db.get_value("Custom Field", {"dt": dt, "fieldname": df["fieldname"]})
            if not existing:
                create_df = {k: v for k, v in df.items() if cf_meta.has_field(k) or k in ("fieldname", "fieldtype", "label", "insert_after", "options")}
                create_custom_field(dt, create_df, ignore_validate=True)
                changed_dt = True
                continue

            cf = frappe.get_doc("Custom Field", existing)
            if cf.fieldtype != df["fieldtype"]:
                frappe.log_error(
                    title="Nexus Production: custom field type mismatch",
                    message=f"{dt}.{df['fieldname']} is {cf.fieldtype} on this site but Nexus expects {df['fieldtype']}. Left unchanged.",
                )
                continue

            dirty = False
            for prop in BEHAVIOUR_PROPS:
                if prop not in df or not cf_meta.has_field(prop):
                    continue
                if (cf.get(prop) or "") != (df[prop] or ""):
                    cf.set(prop, df[prop])
                    dirty = True
            if dirty:
                cf.flags.ignore_validate = True
                cf.save(ignore_permissions=True)
                changed_dt = True

        if changed_dt:
            frappe.clear_cache(doctype=dt)
