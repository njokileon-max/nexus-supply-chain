# Copyright (c) 2026, Crystal Adhesives Ltd

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import cint, flt

LINK_WAREHOUSE_FIELDS = ("fg_warehouse", "colourant_warehouse", "cook_target_warehouse", "fill_target_warehouse")
WAREHOUSE_TABLES = ("bulk_warehouses", "rm_warehouses")


class NexusProductionSettings(Document):
    def validate(self):
        self._dedupe_rows("bulk_warehouses", "warehouse")
        self._dedupe_rows("rm_warehouses", "warehouse")
        self._dedupe_rows("draft_workflow_states", "workflow_state")
        self._dedupe_rows("allowed_roles", "role")
        self._validate_warehouses()
        self._validate_numbers()

    def on_update(self):
        # The board's cached results depend on these settings.
        frappe.cache().delete_keys("nexus_production:")

    # ------------------------------------------------------------------ helpers

    def _dedupe_rows(self, table, link_field):
        seen, keep = set(), []
        for row in self.get(table) or []:
            value = row.get(link_field)
            if value and value not in seen:
                seen.add(value)
                keep.append(row)
        self.set(table, keep)

    def _check_leaf_warehouse(self, warehouse, label):
        if not warehouse:
            return
        is_group = frappe.db.get_value("Warehouse", warehouse, "is_group")
        if is_group is None:
            frappe.throw(_("{0}: warehouse {1} does not exist.").format(label, frappe.bold(warehouse)))
        if cint(is_group):
            frappe.throw(_("{0}: {1} is a group warehouse. Choose a warehouse that holds stock.").format(label, frappe.bold(warehouse)))

    def _validate_warehouses(self):
        for fieldname in LINK_WAREHOUSE_FIELDS:
            self._check_leaf_warehouse(self.get(fieldname), _(self.meta.get_label(fieldname)))
        for table in WAREHOUSE_TABLES:
            label = _(self.meta.get_label(table))
            for row in self.get(table) or []:
                self._check_leaf_warehouse(row.warehouse, label)

        if not self.get("bulk_warehouses"):
            frappe.throw(_("Add at least one Bulk Warehouse."))
        if not self.get("rm_warehouses"):
            frappe.throw(_("Add at least one Raw Material Warehouse."))

    def _validate_numbers(self):
        positive = {
            "order_age_limit_days": 1,
            "draft_age_limit_days": 1,
            "wo_age_limit_days": 1,
            "refresh_interval_minutes": 1,
            "night_refresh_interval_minutes": 1,
            "cache_ttl_seconds": 0,
            "engine_timeout_seconds": 5,
            "pending_stale_days": 1,
        }
        for fieldname, minimum in positive.items():
            # a field this site's DocType does not have yet (code deployed before the migrate)
            # must never block saving; it is checked once the migrate adds it
            if not self.meta.has_field(fieldname):
                continue
            if cint(self.get(fieldname)) < minimum:
                frappe.throw(_("{0} must be at least {1}.").format(_(self.meta.get_label(fieldname)), minimum))

        tolerance = flt(self.mass_balance_tolerance)
        if tolerance < 0 or tolerance > 50:
            frappe.throw(_("BOM Mass Balance Tolerance must be between 0 and 50%."))

        if bool(self.night_start) != bool(self.night_end):
            frappe.throw(_("Set both Night Shift Starts and Night Shift Ends, or neither."))
