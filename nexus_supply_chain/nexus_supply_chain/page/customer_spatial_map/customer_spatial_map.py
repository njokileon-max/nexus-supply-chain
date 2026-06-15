import frappe

@frappe.whitelist()
def get_mapped_customers():
    """Fetches all customers who have valid GPS coordinates."""
    customers = frappe.get_all(
        "Customer",
        filters={
            "custom_latitude": ["is", "set"],
            "custom_longitude": ["is", "set"],
            "disabled": 0
        },
        fields=[
            "name", 
            "customer_name", 
            "territory", 
            "customer_group", 
            "custom_latitude", 
            "custom_longitude"
        ]
    )
    return customers
