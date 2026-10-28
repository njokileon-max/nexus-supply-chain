"""
Nexus Production Batch 8: give the new Settings fields their starting values.

A new field on a Single doctype has no stored value until someone saves the
form, and an unset Check reads as 0. Without this patch "Include Work Orders"
would silently start as unticked. Only fields with NO stored value are set,
so running it again never overwrites a choice made in the form.
"""

import frappe

DOCTYPE = "Nexus Production Settings"
START_VALUES = {
    "include_work_orders": 1,              # same as before Batch 8, with the corrected set-aside
    "include_purchase_orders": 0,          # received POs are not being closed yet: tick once they are
    "material_price_basis": "Last Purchase Rate",
    "pending_stale_days": 14,
}


def execute():
    if not frappe.db.exists("DocType", DOCTYPE):
        return
    for field, value in START_VALUES.items():
        stored = frappe.db.sql("SELECT 1 FROM `tabSingles` WHERE doctype = %s AND field = %s", (DOCTYPE, field))
        if not stored:
            frappe.db.set_single_value(DOCTYPE, field, value)
    frappe.clear_document_cache(DOCTYPE, DOCTYPE)
