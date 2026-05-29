# apps/nexus_supply_chain/nexus_supply_chain/utils/geocoding.py

import frappe
import requests

def queue_customer_geocoding(doc, method=None):
    """
    Triggered every time a Customer is saved or imported.
    Instantly hand off the geocoding check to an asynchronous background 
    worker loop to prevent database transaction blockage and lag.
    """
    frappe.enqueue(
        "nexus_supply_chain.utils.geocoding.execute_external_geocode_call",
        doc_name=doc.name,
        queue="short",
        timeout=300
    )

def execute_external_geocode_call(doc_name):
    """
    Executed out-of-band by background workers. Fetches the client document,
    verifies conditional modifications, and submits tasks to the FastAPI gateway.
    """
    try:
        # Re-fetch the live document state inside the worker instance
        doc = frappe.get_doc("Customer", doc_name)

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
        # 🚨 IMPORTANT: Replace these with an actual API Key and Secret generated 
        # from the Administrator user in ERPNext (User -> Settings -> API Access)
        api_key = "1f2418fc036348f"
        api_secret = "2536cd1f5256d41"

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

        fastapi_url = "http://nexus-brain:8001/queue-geocode"
        
        # Since this runs asynchronously on a worker, we can use a safer 10-second timeout 
        # without affecting the user interface experience.
        requests.post(fastapi_url, json=payload, timeout=10)

    except Exception as e:
        # Fail silently for the background loop, but log context trace for administrative auditing
        frappe.log_error(message=str(e), title="Nexus FastAPI Geocode Queue Error")