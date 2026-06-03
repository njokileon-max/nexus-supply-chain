# apps/nexus_supply_chain/nexus_supply_chain/utils/geocoding.py

import frappe
import requests

def queue_customer_geocoding(doc, method=None):
    """
    Triggered every time a Customer is saved or imported (before_save hook).
    Evaluates synchronously if the link changed, then safely hands off to a 
    background worker AFTER the database commits to prevent row locks.
    """
    link = doc.get("custom_google_maps_link")
    if not link:
        return

    # Check if all coordinate fields are already populated
    has_coords = bool(
        doc.get("custom_latitude") and 
        doc.get("custom_longitude") and 
        doc.get("custom_combined_coordinates")
    )
    
    # Determine if we need to push to the extraction queue.
    # Condition 1: A brand new link was pasted or altered in the UI
    link_changed = doc.has_value_changed("custom_google_maps_link")
    
    # Condition 2: Link exists, but coordinates are completely missing (e.g., during a CSV mass import)
    needs_initial_geocoding = not has_coords

    if not (link_changed or needs_initial_geocoding):
        return

    # Synchronous decision made. Pass PURE DATA to the worker and lock it behind the DB commit.
    frappe.enqueue(
        "nexus_supply_chain.utils.geocoding.execute_external_geocode_call",
        doc_name=doc.name,
        url=link, # Pass the URL directly so the worker doesn't need to fetch the doc again
        queue="short",
        timeout=300,
        enqueue_after_commit=True # 🚨 CRITICAL FIX: Prevents Database Lock Timeout
    )

def execute_external_geocode_call(doc_name, url):
    """
    Executed blindly and safely out-of-band by background workers. 
    Submits the URL payload to the FastAPI gateway for advanced extraction.
    """
    try:
        # 🚨 IMPORTANT: Replace these with an actual API Key and Secret generated 
        # from the Administrator user in ERPNext (User -> Settings -> API Access)
        api_key = "1f2418fc036348f"
        api_secret = "2536cd1f5256d41"

        payload = {
            "customer_name": doc_name,
            "url": url,
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