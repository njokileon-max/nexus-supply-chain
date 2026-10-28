# Copyright (c) 2026, Nexus and contributors
# For license information, please see license.txt

from frappe.model.document import Document


class ManifestFuelLog(Document):
	"""
	Child row of Vehicle Delivery Manifest. One row per fuel purchase made on
	the road. Rows are written by the server only (driver app → api.py in
	Batch 2), which also normalises the coordinates and recalculates the
	parent's cumulative_additional_fuel_cost. No logic lives here so that a
	row never behaves differently depending on how the parent was saved.
	"""

	pass
