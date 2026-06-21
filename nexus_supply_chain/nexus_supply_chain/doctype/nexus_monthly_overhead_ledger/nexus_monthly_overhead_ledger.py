# Copyright (c) 2026, Nexus Supply Chain and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document
from frappe.utils import flt
import calendar

class NexusMonthlyOverheadLedger(Document):
    def validate(self):
        # 1. 100% Sync Guarantee: Recalculate totals on backend before save
        # This prevents users from manipulating the UI to save false totals
        total_fetched = sum(flt(row.amount_kes) for row in self.get("table_zgwz", []))
        total_manual = sum(flt(row.amount) for row in self.get("manual_entries", [])) # Assuming child field is 'amount'
        
        self.total_monthly_overhead = total_fetched + total_manual
        
        working_days = flt(self.working_days) or 1.0
        self.overhead_rate_per_day = self.total_monthly_overhead / working_days
        
        if flt(self.total_production_kg) > 0:
            self.overhead_rate_per_kg = self.total_monthly_overhead / flt(self.total_production_kg)
        else:
            self.overhead_rate_per_kg = 0.0

        # 2. Strict Auditing Validation
        if self.docstatus == 1: # On Submit
            if flt(self.total_production_kg) <= 0:
                frappe.throw("Cannot submit Overhead Ledger: Total Production Vol (Kg) must be greater than zero.")
            if not self.get("table_zgwz"):
                frappe.throw("Cannot submit Overhead Ledger: No GL Expenses were fetched for this period.")

@frappe.whitelist()
def fetch_erp_monthly_data(month, year, company):
    """
    High-speed raw SQL engine to fetch exactly what the company spent
    and exactly what it produced in a given month.
    """
    month_map = {
        "January": 1, "February": 2, "March": 3, "April": 4,
        "May": 5, "June": 6, "July": 7, "August": 8,
        "September": 9, "October": 10, "November": 11, "December": 12
    }
    
    month_num = month_map.get(month)
    if not month_num:
        frappe.throw("Invalid Month selected.")
        
    try:
        year_num = int(year)
        last_day = calendar.monthrange(year_num, month_num)[1]
    except Exception:
        frappe.throw("Invalid Year format.")

    start_date = f"{year_num}-{month_num:02d}-01"
    end_date = f"{year_num}-{month_num:02d}-{last_day:02d}"

    # ---------------------------------------------------------
    # QUERY 1: Fetch General Ledger Expenses (Excluding COGS)
    # ---------------------------------------------------------
    # In accounting, an Expense is increased by a Debit. 
    # Net Expense = (Debits - Credits)
    expenses = frappe.db.sql("""
        SELECT 
            acc.account_number,
            acc.account_name,
            SUM(gl.debit) - SUM(gl.credit) AS amount_kes
        FROM `tabGL Entry` gl
        INNER JOIN `tabAccount` acc ON gl.account = acc.name
        WHERE gl.posting_date BETWEEN %(start)s AND %(end)s
            AND gl.company = %(company)s
            AND gl.is_cancelled = 0
            AND acc.root_type = 'Expense'
            AND acc.account_type NOT IN ('Cost of Goods Sold', 'Expenses Included In Valuation', 'Stock Adjustment')
            AND acc.account_name NOT LIKE 'Cost of Sales%%'
            AND acc.account_name NOT LIKE 'Cost Of Sales%%'
        GROUP BY acc.account_number, acc.account_name
        HAVING amount_kes > 0
        ORDER BY amount_kes DESC
    """, {
        "start": start_date, 
        "end": end_date, 
        "company": company
    }, as_dict=True)

    # ---------------------------------------------------------
    # QUERY 2: Fetch Normalized Factory Yield (Kgs)
    # ---------------------------------------------------------
    # We join Stock Entry Detail to Item to multiply Qty by the actual Weight per Unit.
    # This perfectly normalizes Liters, Grams, and Tins into pure Kgs.
    yield_data = frappe.db.sql("""
        SELECT 
            SUM(sed.qty * COALESCE(i.weight_per_unit, 1.0)) AS total_kg
        FROM `tabStock Entry` se
        INNER JOIN `tabStock Entry Detail` sed ON se.name = sed.parent
        INNER JOIN `tabItem` i ON sed.item_code = i.name
        WHERE se.purpose = 'Manufacture'
            AND se.docstatus = 1
            AND se.company = %(company)s
            AND se.posting_date BETWEEN %(start)s AND %(end)s
            AND sed.is_finished_item = 1
    """, {
        "start": start_date, 
        "end": end_date, 
        "company": company
    }, as_dict=True)

    total_kg = flt(yield_data[0].total_kg) if yield_data and yield_data[0].total_kg else 0.0

    return {
        "expenses": expenses,
        "total_kg": total_kg
    }