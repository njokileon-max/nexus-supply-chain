# Copyright (c) 2026, Nexus and contributors
# For license information, please see license.txt

"""
Nexus App Version (Single).

The Sales and Driver apps have separate minimum versions. `minimum_version`
is kept only for app builds released before the split; it always mirrors the
Sales minimum, which is what those older builds were checking.

Read on every app request by api.enforce_minimum_app_version, so it uses the
cached document (cleared automatically when the settings are saved).
"""

import re

import frappe
from frappe import _
from frappe.model.document import Document

VERSION_RE = re.compile(r"^\d+\.\d+\.\d+$")
APP_FIELDS = {"sales": "sales_minimum_version", "driver": "driver_minimum_version"}


class NexusAppVersion(Document):
	def validate(self):
		for fieldname in APP_FIELDS.values():
			value = (self.get(fieldname) or "").strip()
			if value and not VERSION_RE.match(value):
				frappe.throw(_("{0} must look like 1.2.3").format(self.meta.get_label(fieldname)))
			self.set(fieldname, value)

		self.minimum_version = self.sales_minimum_version or self.minimum_version


def get_minimum_version(app="sales"):
	"""Minimum version for 'sales' or 'driver'. Empty string means no limit."""
	app = (app or "sales").strip().lower()
	fieldname = APP_FIELDS.get(app, "sales_minimum_version")
	doc = frappe.get_cached_doc("Nexus App Version")
	value = doc.get(fieldname)
	if not value and fieldname == "sales_minimum_version":
		value = doc.get("minimum_version")
	return (value or "").strip()
