# apps/nexus_supply_chain/nexus_supply_chain/utils/geocoding.py

import frappe
import requests

def queue_customer_geocoding(doc, method):
    """
    Triggered every time a Customer is saved or imported.
    If there is a new Google Maps URL, it pushes it to the FastAPI background queue.
    """
    # 1. Check if the field exists and has a value
    link = doc.get("custom_google_maps_link")
    if not link:
        return

    # 2. Prevent infinite loops: Only queue if the link has changed 
    # OR if latitude/longitude are completely empty (like during a mass import)
    has_coords = doc.get("custom_latitude") and doc.get("custom_longitude")
    link_changed = doc.has_value_changed("custom_google_maps_link")
    
    if has_coords and not link_changed:
        return

    # 3. Fire the payload to the FastAPI Brain
    try:
        # 🚨 IMPORTANT: Replace these with an actual API Key and Secret generated 
        # from the Administrator user in ERPNext (User -> Settings -> API Access)
        api_key = "YOUR_API_KEY"
        api_secret = "YOUR_API_SECRET"

        payload = {
            "customer_name": doc.name,
            "url": link,
            "erp_url": frappe.utils.get_url(), # Dynamically grabs your ERPNext base URL
            "erp_headers": {
                "Authorization": f"token {api_key}:{api_secret}",
                "Content-Type": "application/json",
                "Accept": "application/json"
            }
        }

        fastapi_url = "http://127.0.0.1:8001/queue-geocode"
        
        # We use a tiny 2-second timeout. If FastAPI is offline, ERPNext ignores it 
        # and saves the customer anyway without lagging the UI.
        requests.post(fastapi_url, json=payload, timeout=2)

    except Exception as e:
        # Fail silently for the user, but log the error for the Admin
        frappe.log_error(message=str(e), title="Nexus FastAPI Geocode Queue Error")