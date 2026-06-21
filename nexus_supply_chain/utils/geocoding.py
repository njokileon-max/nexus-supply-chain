import frappe
import requests

def queue_customer_geocoding(doc, method=None):
    link = doc.get("custom_google_maps_link")
    if not link:
        return

    has_coords = bool(
        doc.get("custom_latitude") and 
        doc.get("custom_longitude") and 
        doc.get("custom_combined_coordinates")
    )
    
    link_changed = doc.has_value_changed("custom_google_maps_link")
    
    needs_initial_geocoding = not has_coords

    if not (link_changed or needs_initial_geocoding):
        return

    frappe.enqueue(
        "nexus_supply_chain.utils.geocoding.execute_external_geocode_call",
        doc_name=doc.name,
        url=link,
        queue="short",
        timeout=300,
        enqueue_after_commit=True
    )

def execute_external_geocode_call(doc_name, url):
    try:
        api_key = "1f2418fc036348f"
        api_secret = "2536cd1f5256d41"

        payload = {
            "customer_name": doc_name,
            "url": url,
            "erp_url": frappe.utils.get_url(),
            "erp_headers": {
                "Authorization": f"token {api_key}:{api_secret}",
                "Content-Type": "application/json",
                "Accept": "application/json"
            }
        }

        fastapi_url = "https://crystal-api.crystalapps.dev/queue-geocode"
        
        requests.post(fastapi_url, json=payload, timeout=10)

    except Exception as e:
        frappe.log_error(message=str(e), title="Nexus FastAPI Geocode Queue Error")
