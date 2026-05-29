import frappe
from frappe.utils import now_datetime

def expire_reservations():
    frappe.db.sql("""UPDATE `tabNexus Inventory Reservation` 
                     SET status = 'Expired' 
                     WHERE status = 'Active' AND expiry_datetime < %s""", now_datetime())
    frappe.db.commit()