# Copyright (c) 2026, Nexus and contributors
# For license information, please see license.txt

from frappe.model.document import Document


class ManifestReturnCollection(Document):
	"""
	Child row of Vehicle Delivery Manifest: goods from an EARLIER invoice that
	the customer handed to the driver at a stop. The driver records only the
	invoice number; nothing is created from the app (no return, credit note,
	stock entry or payment). The back office processes each row later and
	sets Back Office Status (and, in a later update, links the return it
	created). Rows are written by driver_api.confirm_stop_from_app only.
	"""

	pass
