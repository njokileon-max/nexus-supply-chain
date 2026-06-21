# Copyright (c) 2026, Nexus Supply Chain
# For license information, please see license.txt

import frappe
from frappe.model.document import Document

class CompanyOverheadSettings(Document):
    """
    Single DocType to store daily overhead amounts.
    Only one record exists; use frappe.get_single() to access.
    """
    
    def validate(self):
        """
        Runs before saving. Ensures daily_overhead is not negative.
        """
        if self.daily_overhead < 0:
            frappe.throw("Daily Overhead cannot be negative.")
        
        # Validate date range if both are set
        if self.valid_from and self.valid_to and self.valid_from > self.valid_to:
            frappe.throw("Valid From date cannot be later than Valid To date.")
    
    def on_update(self):
        """
        Called after save. Clears any cached values related to overhead.
        Useful if you later implement caching for the overhead value.
        """
        frappe.cache().delete_key("nexus:daily_overhead")
    
    @staticmethod
    def get_daily_overhead() -> float:
        """
        Helper method to fetch the current daily overhead.
        Returns 0.0 if settings not yet configured.
        """
        try:
            settings = frappe.get_single("Company Overhead Settings")
            return settings.daily_overhead or 0.0
        except Exception:
            return 0.0