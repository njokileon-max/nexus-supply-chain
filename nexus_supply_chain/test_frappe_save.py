import frappe
import requests

def run_test():
    customer_id = "CUS-07191" # Using your test customer
    link = "https://maps.app.goo.gl/v9ERJqfv69x69oYm8"
    
    print("=" * 50)
    print("🚀 SIMULATING THE NEW 'DUMB CALCULATOR' BACKGROUND WORKER")
    print("=" * 50)
    
    print(f"1. Reaching out to VPS to calculate coordinates...")
    try:
        response = requests.post(
            "https://crystal-api.crystalapps.dev/extract-coordinates", 
            json={"url": link}, 
            timeout=10
        )
        data = response.json()
        
        lat = float(data.get("lat"))
        lng = float(data.get("lng"))
        
        print(f"✅ VPS responded instantly with: {lat}, {lng}")
        print("\n2. Executing internal Frappe stealth write (No CSRF needed!)...")
        
        # This is the exact code the background worker will use.
        # update_modified=False prevents it from triggering on_change hooks and looping.
        frappe.db.set_value("Customer", customer_id, {
            "custom_latitude": lat,
            "custom_longitude": lng
        }, update_modified=False)
        
        # Background workers MUST explicitly commit to MariaDB
        frappe.db.commit()
        
        print("✅ SUCCESS! Check Customer CUS-07191 in the ERPNext UI.")
        print("The coordinates are locked into the database with ZERO authentication errors.")
        print("=" * 50)
        
    except Exception as e:
        print(f"❌ Test Failed: {str(e)}")
