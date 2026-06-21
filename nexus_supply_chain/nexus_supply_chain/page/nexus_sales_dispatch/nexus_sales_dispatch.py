import frappe

@frappe.whitelist()
def get_sales_team():
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

    return team_data or []
