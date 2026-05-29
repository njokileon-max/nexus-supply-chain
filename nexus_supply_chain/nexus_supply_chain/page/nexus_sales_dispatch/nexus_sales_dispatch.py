# apps/nexus_supply_chain/nexus_supply_chain/page/nexus_sales_dispatch/nexus_sales_dispatch.py

import frappe

@frappe.whitelist()
def get_sales_team():
    """
    Fetches the baseline active sales team using the strict relational hierarchy:
    Sales Person ➔ Employee ➔ User.
    
    This provides the Dispatch dashboard with the full names and emails 
    so they can be tracked (shown as Offline) even before they send their first live ping.
    It guarantees that only properly configured, active field reps are loaded.
    """
    
    # Perform the strict 3-table JOIN to guarantee data integrity.
    # 1. Start at Sales Person
    # 2. Hop to Employee (via sp.employee = emp.name)
    # 3. Hop to User (via emp.user_id = usr.name)
    # 4. Filter out any disabled, inactive, or non-system users across all 3 levels.
    
    team_data = frappe.db.sql("""
        SELECT 
            usr.name as email, 
            usr.full_name
        FROM `tabSales Person` sp
        JOIN `tabEmployee` emp ON sp.employee = emp.name
        JOIN `tabUser` usr ON emp.user_id = usr.name
        WHERE 
            sp.enabled = 1 
            AND emp.status = 'Active' 
            AND usr.enabled = 1
            AND usr.user_type = 'System User'
        ORDER BY usr.full_name ASC
    """, as_dict=True)

    # Return the clean list of dictionaries, or an empty array if nobody matches
    return team_data or []