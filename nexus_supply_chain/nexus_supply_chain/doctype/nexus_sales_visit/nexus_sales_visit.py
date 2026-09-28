# Copyright (c) 2026, leon and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document

from nexus_supply_chain.api import compute_is_on_site

VISIT_TYPE_CUSTOMER = "Customer"
VISIT_TYPE_LEAD = "Lead"


class NexusSalesVisit(Document):
	"""
	Every insert and every desk save passes through validate(), so the three
	derived values below can never be wrong on a record saved this way:
	  - visit_type matches exactly one filled link (customer XOR lead)
	  - party_name is the customer/lead display name
	  - is_on_site follows the single shared rule in api.compute_is_on_site

	Backend paths that write with frappe.db.set_value (location correction)
	bypass validate(), so they call the SAME compute_is_on_site helper
	themselves. There is no second copy of the rule anywhere.

	All field reads use self.get() so this controller never raises on a site
	where the new columns haven't been migrated yet.
	"""

	def validate(self):
		self.set_visit_type_default()
		self.validate_party_link()
		self.set_party_name()
		self.set_is_on_site()

	def set_visit_type_default(self):
		if not self.get("visit_type"):
			self.visit_type = (
				VISIT_TYPE_LEAD if (self.get("lead") and not self.get("customer")) else VISIT_TYPE_CUSTOMER
			)

	def validate_party_link(self):
		visit_type = self.get("visit_type")
		customer = self.get("customer")
		lead = self.get("lead")

		if visit_type == VISIT_TYPE_CUSTOMER:
			if not customer:
				frappe.throw(_("A Customer visit must have a Customer."))
			if lead:
				frappe.throw(_("A Customer visit cannot also have a Lead. Clear the Lead field or change Visit Type."))
		elif visit_type == VISIT_TYPE_LEAD:
			if not lead:
				frappe.throw(_("A Lead visit must have a Lead."))
			if customer:
				frappe.throw(_("A Lead visit cannot also have a Customer. Clear the Customer field or change Visit Type."))
		else:
			frappe.throw(_("Visit Type must be Customer or Lead."))

	def set_party_name(self):
		if self.get("visit_type") == VISIT_TYPE_LEAD:
			row = frappe.db.get_value("Lead", self.lead, ["lead_name", "company_name"], as_dict=True) or {}
			self.party_name = row.get("lead_name") or row.get("company_name") or self.lead
		else:
			self.party_name = frappe.db.get_value("Customer", self.customer, "customer_name") or self.customer

	def set_is_on_site(self):
		# Recompute only when the inputs change. A desk edit of an unrelated
		# field (e.g. visit notes) on a historical record therefore never
		# touches the value the one-time patch set for it.
		if (
			self.is_new()
			or self.has_value_changed("distance_from_target_meters")
			or self.has_value_changed("target_coordinates")
		):
			self.is_on_site = compute_is_on_site(
				self.get("target_coordinates"),
				self.get("distance_from_target_meters"),
			)

