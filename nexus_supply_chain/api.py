import frappe
import requests
import json
import math
from datetime import datetime
from frappe.utils import today, add_days, add_months, get_first_day, get_last_day, get_datetime

def extract_and_set_coords(doc, method):
    if doc.custom_google_maps_link and doc.has_value_changed("custom_google_maps_link"):
        try:
            response = requests.post(
                "https://crystal-api.crystalapps.dev/extract-coordinates",
                json={"url": doc.custom_google_maps_link},
                timeout=15  
            )
            
            if response.status_code == 200:
                data = response.json()
                if data.get("status") == "success":
                    doc.custom_latitude = data.get("lat")
                    doc.custom_longitude = data.get("lng")
                    if data.get("combined_coordinates"):
                        doc.custom_combined_coordinates = data.get("combined_coordinates")
                else:
                    frappe.msgprint(f"Nexus Brain Warning: {data.get('message')}")
            else:
                frappe.log_error(title="Nexus Brain API Down", message=f"Status: {response.status_code}")
                
        except Exception as e:
            frappe.log_error(title="Coord Extraction Failed", message=str(e))

@frappe.whitelist()
def check_mobile_app_access():
    """
    Strictly checks the user's native ERPNext Role Profile against the allowed 
    roles in Nexus App Settings. No hardcoded admin bypasses.
    """
    if frappe.session.user == "Guest":
        frappe.local.response["http_status_code"] = 401
        return {"status": "denied", "message": "Please log in first."}

    try:
        settings = frappe.get_doc("Nexus App Settings")
        
        table_rows = settings.get("allowed_roles", [])
        allowed_roles = [str(row.role).strip() for row in table_rows if row.role]
        
    except Exception as e:
        return {"status": "denied", "message": f"Server Error: {str(e)}"}

    user_roles = frappe.get_roles(frappe.session.user)
    clean_user_roles = [str(r).strip() for r in user_roles]

    has_access = any(role in allowed_roles for role in clean_user_roles)
    
    if has_access:
        return {"status": "success", "message": "Access Granted"}
    else:
        debug_msg = f"Denied.\nUser has: {clean_user_roles}\nServer allows: {allowed_roles}"
        
        frappe.local.login_manager.logout()
        frappe.db.commit()
        
        frappe.local.response["http_status_code"] = 403
        return {"status": "denied", "message": debug_msg}

@frappe.whitelist(allow_guest=True)
def get_user_profile():
    """
    Called by the React Native app immediately after native login. 
    Returns the user's details, roles, CSRF token, and the SID to authorize proxy requests.
    """
    if frappe.session.user == "Guest":
        frappe.local.response["http_status_code"] = 401
        return {"status": "failed", "message": "Unauthorized"}

    user_doc = frappe.get_doc("User", frappe.session.user)
    roles = frappe.get_roles(frappe.session.user)

    return {
        "status": "success",
        "message": {
            "full_name": user_doc.full_name,
            "email": user_doc.email,
            "roles": roles,
            "csrf_token": frappe.sessions.get_csrf_token(),
            "sid": frappe.session.sid 
        }
    }

@frappe.whitelist()
def get_nexus_live_inventory():
    reservations = frappe.db.sql("""
        SELECT ri.item_code, ri.sales_order, SUM(ri.reserved_qty) as reserved_qty
        FROM `tabNexus Inventory Reservation Item` ri
        JOIN `tabNexus Inventory Reservation` r ON ri.parent = r.name
        WHERE r.reservation_status IN ('Active', 'Waiting for Stock') AND r.docstatus = 1
        GROUP BY ri.item_code, ri.sales_order
    """, as_dict=True)

    if not reservations:
        return []

    active_items = list(set([r['item_code'] for r in reservations if r.get('item_code')]))
    active_sos = list(set([r['sales_order'] for r in reservations if r.get('sales_order')]))

    if not active_items or not active_sos:
        return []

    format_items = ','.join(['%s'] * len(active_items))
    format_sos = ','.join(['%s'] * len(active_sos))

    sales_orders = frappe.db.sql(f"""
        SELECT so.name as sales_order, soi.item_code, soi.qty
        FROM `tabSales Order` so
        JOIN `tabSales Order Item` soi ON so.name = soi.parent
        WHERE so.status = 'To Deliver and Bill' AND so.docstatus = 1
        AND so.name IN ({format_sos})
        AND soi.item_code IN ({format_items})
    """, tuple(active_sos + active_items), as_dict=True)

    items = frappe.db.sql(f"""
        SELECT name as item_code, item_name
        FROM `tabItem`
        WHERE name IN ({format_items})
    """, tuple(active_items), as_dict=True)

    stock = frappe.db.sql(f"""
        SELECT item_code, SUM(actual_qty) as actual_qty
        FROM `tabBin`
        WHERE warehouse = 'Finished Goods - CAL' AND item_code IN ({format_items})
        GROUP BY item_code
    """, tuple(active_items), as_dict=True)

    payload = {
        "items": items,
        "stock": stock,
        "sales_orders": sales_orders,
        "reservations": reservations
    }

    fastapi_url = "https://crystal-api.crystalapps.dev/api/v1/live-inventory" 
    
    try:
        response = requests.post(fastapi_url, json=payload, timeout=15)
        response.raise_for_status()
        return response.json().get("data", [])
    except Exception as e:
        frappe.log_error(message=str(e), title="Nexus Live Inventory Sync Failed")
        return []
    
@frappe.whitelist()
def get_nexus_production_data():
    sales_orders = frappe.db.sql("""
        SELECT so.name as sales_order, soi.item_code, soi.qty
        FROM `tabSales Order` so
        JOIN `tabSales Order Item` soi ON so.name = soi.parent
        WHERE so.status = 'To Deliver and Bill' AND so.docstatus = 1
    """, as_dict=True)

    reservations = frappe.db.sql("""
        SELECT ri.item_code, ri.sales_order, SUM(ri.reserved_qty) as reserved_qty
        FROM `tabNexus Inventory Reservation Item` ri
        JOIN `tabNexus Inventory Reservation` r ON ri.parent = r.name
        WHERE r.reservation_status IN ('Active', 'Waiting for Stock') AND r.docstatus = 1
        GROUP BY ri.item_code, ri.sales_order
    """, as_dict=True)

    mrl_breaches = frappe.db.sql("""
        SELECT i.name as item_code
        FROM `tabItem` i
        LEFT JOIN `tabBin` b ON i.name = b.item_code AND b.warehouse = 'Finished Goods - CAL'
        WHERE i.custom_linked_bip IS NOT NULL 
        AND i.custom_minimum_reorder_level > 0
        AND IFNULL(b.actual_qty, 0) <= i.custom_minimum_reorder_level
    """, as_dict=True)

    active_items = list(set(
        [so['item_code'] for so in sales_orders] + 
        [r['item_code'] for r in reservations] +
        [m['item_code'] for m in mrl_breaches]
    ))

    if not active_items: return []

    format_items = ','.join(['%s'] * len(active_items))
    tuple_items = tuple(active_items)

    fgs = frappe.db.sql(f"""
        SELECT 
            i.name as item_code, 
            i.item_name, 
            i.custom_linked_bip,
            i.custom_minimum_reorder_level as mrl,
            i.custom_maximum_shelf_capacity as max_shelf,
            i.weight_per_unit,
            (SELECT bi.item_code 
             FROM `tabBOM Item` bi 
             JOIN `tabBOM` b ON bi.parent = b.name 
             JOIN `tabItem` pack_item ON bi.item_code = pack_item.name
             WHERE b.item = i.name AND b.is_default = 1 AND b.docstatus = 1 
             AND pack_item.item_group = 'Packaging Materials' LIMIT 1) as pack_code
        FROM `tabItem` i
        WHERE i.name IN ({format_items}) AND i.custom_linked_bip IS NOT NULL
    """, tuple_items, as_dict=True)

    active_bips = list(set([fg['custom_linked_bip'] for fg in fgs if fg.get('custom_linked_bip')]))
    if not active_bips: return []
    format_bips = ','.join(['%s'] * len(active_bips))
    tuple_bips = tuple(active_bips)

    bips = frappe.db.sql(f"""
        SELECT name as bip_code, item_name as bip_name, custom_minimum_production_level as min_batch
        FROM `tabItem`
        WHERE custom_is_bip = 1 AND name IN ({format_bips})
    """, tuple_bips, as_dict=True)

    stock = frappe.db.sql(f"""
        SELECT item_code, SUM(actual_qty) as actual_qty
        FROM `tabBin`
        WHERE warehouse = 'Finished Goods - CAL' AND item_code IN ({format_items})
        GROUP BY item_code
    """, tuple_items, as_dict=True)

    payload = {"bips": bips, "fgs": fgs, "stock": stock, "sales_orders": sales_orders, "reservations": reservations}
    fastapi_url = "http://nexus-brain:8001/api/v1/production-cards" 
    
    try:
        response = requests.post(fastapi_url, json=payload, timeout=15)
        response.raise_for_status()
        return response.json().get("data", [])
    except Exception as e:
        frappe.log_error(message=str(e), title="Nexus Production Sync Failed")
        return []
    
@frappe.whitelist(allow_guest=False)
def sync_manifest_from_app(manifest_name, trip_status=None, stops=None):
    doc = frappe.get_doc("Vehicle Delivery Manifest", manifest_name)

    if trip_status:
        doc.trip_status = trip_status

    if stops:
        if isinstance(stops, str):
            stops = json.loads(stops)
            
        for app_stop in stops:
            target_sales_order = None
            for d in doc.stops:
                if d.name == app_stop.get("name"):
                    d.delivery_status = app_stop.get("delivery_status")
                    d.driver_notes = app_stop.get("driver_notes")
                    target_sales_order = d.sales_order
                    break
                    
            returned_items = app_stop.get("returned_items")
            if returned_items and isinstance(returned_items, list) and target_sales_order:
                for item in returned_items:
                    doc.append("returned_items", {
                        "sales_order": target_sales_order,
                        "item_code": item.get("item_code"),
                        "returned_qty": item.get("qty_returned"),
                        "reason": app_stop.get("primary_reason_for_return")
                    })

    has_pending_stops = any(d.delivery_status == 'Pending' for d in doc.stops)
    
    if not has_pending_stops and doc.trip_status == 'Dispatched':
        doc.trip_status = 'Returning'
        vehicle_transit_name = frappe.db.get_value("Vehicle In Transit", {"current_driver": doc.driver}, "name")
        if vehicle_transit_name:
            frappe.db.set_value("Vehicle In Transit", vehicle_transit_name, "current_status", "Returning")

    if doc.trip_status == 'Completed':
        vehicle_transit_name = frappe.db.get_value("Vehicle In Transit", {"current_driver": doc.driver}, "name")
        if vehicle_transit_name:
            frappe.db.set_value("Vehicle In Transit", vehicle_transit_name, "current_status", "Idle")

    doc.flags.ignore_validate_update_after_submit = True
    doc.save(ignore_permissions=True)
    
    return {"status": "success", "message": "Manifest synced securely."}

@frappe.whitelist()
def get_my_active_manifests_and_context():
    driver_email = frappe.session.user
    
    manifest_records = frappe.get_all(
        "Vehicle Delivery Manifest",
        filters=[
            ["driver", "=", driver_email],
            ["trip_status", "in", ["Ready", "Dispatched", "Completed", "Returning"]]
        ],
        fields=["name", "vehicle", "trip_status", "route_geojson"]
    )
    
    manifests = []
    for record in manifest_records:
        doc = frappe.get_doc("Vehicle Delivery Manifest", record.name)
        manifest_dict = doc.as_dict()
        
        for stop in manifest_dict.get("stops", []):
            if stop.get("customer"):
                try:
                    coords = frappe.db.get_value(
                        "Customer", 
                        stop.get("customer"), 
                        ["custom_latitude", "custom_longitude"], 
                        as_dict=True
                    )
                    if coords:
                        stop["custom_latitude"] = coords.get("custom_latitude") or stop.get("latitude")
                        stop["custom_longitude"] = coords.get("custom_longitude") or stop.get("longitude")
                    else:
                        stop["custom_latitude"] = stop.get("latitude")
                        stop["custom_longitude"] = stop.get("longitude")
                except Exception:
                    stop["custom_latitude"] = stop.get("latitude")
                    stop["custom_longitude"] = stop.get("longitude")
            
            stop["items"] = []
            if stop.get("sales_order"):
                try:
                    so_items = frappe.get_all(
                        "Sales Order Item",
                        filters={"parent": stop.get("sales_order")},
                        fields=["item_code", "item_name", "qty as max_qty"]
                    )
                    stop["items"] = so_items
                except Exception:
                    pass
        
        manifests.append(manifest_dict)

    vehicle = frappe.db.get_value("Vehicle In Transit", {"current_driver": driver_email}, "name") or "Idle"
    
    active_manifest = None
    if manifests:
        dispatched = [m.name for m in manifests if m.trip_status == "Dispatched"]
        returning = [m.name for m in manifests if m.trip_status == "Returning"]
        ready = [m.name for m in manifests if m.trip_status == "Ready"]
        completed = [m.name for m in manifests if m.trip_status == "Completed"]
        
        if dispatched:
            active_manifest = dispatched[0]
        elif returning:
            active_manifest = returning[0]
        elif ready:
            active_manifest = ready[0]
        elif completed:
            active_manifest = completed[0]

    return {
        "status": "success",
        "message": {
            "manifests": manifests,
            "context": {
                "vehicle": vehicle,
                "active_manifest_id": active_manifest or "No_Active_Manifest"
            }
        }
    }

@frappe.whitelist()
def save_fcm_token(fcm_token):
    user = frappe.session.user
    if user == "Guest":
        frappe.local.response["http_status_code"] = 401
        return {"status": "failed", "message": "Unauthorized"}
        
    try:
        existing_device = frappe.db.get_value("Nexus FCM Device", {"user": user, "fcm_token": fcm_token}, "name")
        if not existing_device:
            doc = frappe.new_doc("Nexus FCM Device")
            doc.user = user
            doc.fcm_token = fcm_token
            doc.insert(ignore_permissions=True)
            frappe.db.commit()
            
        return {"status": "success", "message": "Device registered for push notifications."}
    except Exception as e:
        frappe.log_error("FCM Token Save Error", str(e))
        return {"status": "failed", "message": "Failed to save token. Check server logs."}

@frappe.whitelist()
def get_driver_context():
    driver_email = frappe.session.user
    vehicle = frappe.db.get_value("Vehicle In Transit", {"current_driver": driver_email}, "name")
    
    if not vehicle:
        return {"status": "failed", "message": "No vehicle assigned to this driver."}
        
    manifest = frappe.db.get_value("Vehicle Delivery Manifest", {
        "vehicle": vehicle, 
        "trip_status": ["in", ["Ready", "Dispatched"]]
    }, "name")
    
    return {
        "status": "success",
        "vehicle": vehicle,
        "manifest_id": manifest or "No_Active_Manifest"
    }

def get_root_sales_person(user_email):
    employee_name = frappe.db.get_value("Employee", {"user_id": user_email}, "name")
    if employee_name:
        sales_person = frappe.db.get_value("Sales Person", {"employee": employee_name}, "name")
        if sales_person: return sales_person
            
    fallback_sp = frappe.db.get_value("Sales Person", {"employee": user_email}, "name")
    if fallback_sp: return fallback_sp
    return None

def get_authorized_sales_persons(user_email):
    root_sp = get_root_sales_person(user_email)
    if not root_sp: return []

    sp_doc = frappe.db.get_value("Sales Person", root_sp, ["lft", "rgt"], as_dict=True)
    if not sp_doc: return []

    authorized_sps = frappe.db.sql("""
        SELECT name FROM `tabSales Person` 
        WHERE lft >= %s AND rgt <= %s
    """, (sp_doc.lft, sp_doc.rgt), as_dict=False)
    
    return [sp[0] for sp in authorized_sps] if authorized_sps else []

@frappe.whitelist()
def get_sales_dashboard_data():
    user = frappe.session.user
    auth_sps = get_authorized_sales_persons(user)
    
    if not auth_sps:
        return {"status": "error", "message": "No Sales Person hierarchy linked to your account."}

    cache_key = f"nexus_sales_dashboard_{user}_{today()}"
    cached_data = frappe.cache().get_value(cache_key)
    
    if cached_data:
        return {"status": "success", "source": "cache", "data": cached_data}

    start_of_month = get_first_day(today())
    end_of_month = get_last_day(today())
    
    format_sps = ','.join(['%s'] * len(auth_sps))
    tuple_sps = tuple(auth_sps)

    sales_target = 0.0
    collection_target = 0.0
    
    employee_id = frappe.db.get_value("Employee", {"user_id": user}, "name")
    root_sp = get_root_sales_person(user)
    
    cascade_path = [
        ("User", user),
        ("Employee", employee_id),
        ("Sales Person", root_sp)
    ]
    
    for doctype, docname in cascade_path:
        if not docname: continue
        
        if sales_target == 0.0 and frappe.db.has_column(doctype, "custom_sales_target"):
            val = frappe.db.get_value(doctype, docname, "custom_sales_target")
            if val: sales_target = float(val)
            
        if collection_target == 0.0 and frappe.db.has_column(doctype, "custom_collection_target"):
            val = frappe.db.get_value(doctype, docname, "custom_collection_target")
            if val: collection_target = float(val)
            
        if sales_target > 0.0 and collection_target > 0.0:
            break

    assigned_customers = frappe.db.sql(f"""
        SELECT DISTINCT parent FROM `tabSales Team` 
        WHERE parenttype = 'Customer' AND sales_person IN ({format_sps})
    """, tuple_sps, as_dict=False)
    
    customer_list = [c[0] for c in assigned_customers] if assigned_customers else []
    
    if not customer_list:
        empty_payload = {
            "targets": {"sales": sales_target, "collection": collection_target},
            "sales_total": 0,
            "collection_total": 0,
            "sales_graph": [],
            "collections_graph": []
        }
        frappe.cache().set_value(cache_key, empty_payload, expires_in_sec=1800)
        return {"status": "success", "source": "db", "data": empty_payload}

    format_customers = ','.join(['%s'] * len(customer_list))

    sales_data = frappe.db.sql(f"""
        SELECT DAY(posting_date) as day, SUM(grand_total) as value
        FROM `tabSales Invoice`
        WHERE docstatus = 1 AND posting_date BETWEEN %s AND %s
        AND customer IN ({format_customers})
        GROUP BY DAY(posting_date)
        ORDER BY DAY(posting_date)
    """, tuple([start_of_month, end_of_month] + customer_list), as_dict=True)

    total_sales_made = sum([s['value'] for s in sales_data])

    collection_data = frappe.db.sql(f"""
        SELECT DAY(posting_date) as day, SUM(paid_amount) as value
        FROM `tabPayment Entry`
        WHERE docstatus = 1 AND payment_type = 'Receive' AND posting_date BETWEEN %s AND %s
        AND party_type = 'Customer' AND party IN ({format_customers})
        GROUP BY DAY(posting_date)
        ORDER BY DAY(posting_date)
    """, tuple([start_of_month, end_of_month] + customer_list), as_dict=True)

    total_collections_made = sum([c['value'] for c in collection_data])

    payload = {
        "targets": {"sales": sales_target, "collection": collection_target},
        "sales_total": total_sales_made, "collection_total": total_collections_made,
        "sales_graph": sales_data, "collections_graph": collection_data
    }

    frappe.cache().set_value(cache_key, payload, expires_in_sec=1800)
    return {"status": "success", "source": "db", "data": payload}

@frappe.whitelist()
def get_sales_context():
    """
    Fetches the App's core operational data in ONE call for FastAPI to hash.
    Optimized via Nested Sets to pull all customers for the rep's authorized branch.
    Now includes Order Recovery Engine, Debt Snapshot, 0-Lag Dashboard Stats, and Dropdown Metadata.
    """
    auth_sps = get_authorized_sales_persons(frappe.session.user)
    if not auth_sps:
        return {"status": "error", "message": "No assigned sales profile hierarchy."}

    format_sps = ','.join(['%s'] * len(auth_sps))
    tuple_sps = tuple(auth_sps)

    customers = frappe.db.sql(f"""
        SELECT
            c.name as name,
            c.customer_name,
            c.default_price_list,
            c.payment_terms,
            COALESCE(c.custom_phone_number, c.mobile_no) as mobile_no,
            c.custom_location,
            c.custom_latitude,
            c.custom_longitude,
            c.custom_combined_coordinates,
            c.custom_google_maps_link,
            c.name as customer_id,
            (SELECT MAX(posting_date) FROM `tabSales Invoice` WHERE customer = c.name AND docstatus = 1) as last_invoiced_date
        FROM `tabCustomer` c
        JOIN `tabSales Team` st ON c.name = st.parent AND st.parenttype = 'Customer'
        WHERE st.sales_person IN ({format_sps}) AND c.disabled = 0
        GROUP BY c.name
    """, tuple_sps, as_dict=True)
    
    customer_ids = [c['name'] for c in customers]

    items = frappe.db.sql("""
        SELECT i.name as name, i.item_code, i.item_name
        FROM `tabItem` i
        JOIN `tabItem Group` ig ON i.item_group = ig.name
        WHERE i.disabled = 0
        AND ig.lft >= (SELECT lft FROM `tabItem Group` WHERE name = 'Finished Goods')
        AND ig.rgt <= (SELECT rgt FROM `tabItem Group` WHERE name = 'Finished Goods')
    """, as_dict=True)

    prices = frappe.db.sql("""
        SELECT item_code, price_list, price_list_rate
        FROM `tabItem Price`
    """, as_dict=True)

    bins = frappe.db.sql("""
        SELECT item_code, SUM(actual_qty) as actual_qty
        FROM `tabBin`
        WHERE warehouse = 'Finished Goods - CAL'
        GROUP BY item_code
    """, as_dict=True)

    try:
        regions = frappe.db.sql("""SELECT name FROM `tabDelivery Region`""", as_dict=True)
    except Exception:
        regions = [{"name": "Default Center"}]

    try:
        customer_groups = frappe.db.sql("""SELECT name FROM `tabCustomer Group`""", as_dict=True)
    except Exception:
        customer_groups = [{"name": "Commercial"}]

    try:
        territories = frappe.db.sql("""SELECT name FROM `tabTerritory`""", as_dict=True)
    except Exception:
        territories = [{"name": "All Territories"}]

    try:
        price_lists = frappe.db.sql("""SELECT name FROM `tabPrice List` WHERE selling = 1""", as_dict=True)
    except Exception:
        price_lists = [{"name": "Standard Selling"}]

    try:
        payment_terms_templates = frappe.db.sql("""SELECT name FROM `tabPayment Terms Template`""", as_dict=True)
    except Exception:
        payment_terms_templates = [{"name": "Standard Cash"}]
        
    try:
        currencies = frappe.db.sql("""SELECT name FROM `tabCurrency` WHERE enabled = 1""", as_dict=True)
    except Exception:
        currencies = [{"name": "KES"}]
        
    try:
        tax_categories = frappe.db.sql("""SELECT name FROM `tabTax Category`""", as_dict=True)
    except Exception:
        tax_categories = []

    thirty_days_ago = add_days(today(), -30)
    recent_orders = frappe.db.sql("""
        SELECT name as id, customer_name as customer, custom_delivery_region as region, 
               grand_total as total, status, transaction_date as date
        FROM `tabSales Order`
        WHERE docstatus < 2 AND owner = %s AND transaction_date >= %s
        ORDER BY creation DESC
    """, (frappe.session.user, thirty_days_ago), as_dict=True)
    
    if recent_orders:
        order_names = [o.id for o in recent_orders]
        format_orders = ','.join(['%s'] * len(order_names))
        order_items = frappe.db.sql(f"""
            SELECT parent, item_code, item_name, qty, rate
            FROM `tabSales Order Item`
            WHERE parent IN ({format_orders})
        """, tuple(order_names), as_dict=True) 
        
        invoices = frappe.db.sql(f"""
            SELECT si.sales_order, s.name as invoice_id, s.outstanding_amount, s.grand_total 
            FROM `tabSales Invoice Item` si 
            JOIN `tabSales Invoice` s ON si.parent = s.name 
            WHERE si.sales_order IN ({format_orders}) AND s.docstatus = 1
        """, tuple(order_names), as_dict=True) 
        
        inv_map = {}
        for inv in invoices:
            if inv.sales_order not in inv_map:
                inv_map[inv.sales_order] = {'grand': 0, 'out': 0, 'invoices': []}
            inv_map[inv.sales_order]['grand'] += inv.grand_total
            inv_map[inv.sales_order]['out'] += inv.outstanding_amount
            inv_map[inv.sales_order]['invoices'].append(inv.invoice_id)

        item_map = {}
        for it in order_items:
            item_map.setdefault(it.parent, []).append(it)
            
        for o in recent_orders:
            o['items'] = item_map.get(o.id, [])
            o['totalQty'] = sum(i['qty'] for i in o['items'])
            
            inv_data = inv_map.get(o.id)
            if inv_data:
                if inv_data['out'] <= 0:
                    o['payment_status'] = "Paid"
                elif inv_data['out'] < inv_data['grand']:
                    o['payment_status'] = "Partially Paid"
                else:
                    o['payment_status'] = "Unpaid"
                
                o['invoice_id'] = inv_data['invoices'][0] if inv_data['invoices'] else None
            else:
                o['payment_status'] = "Unpaid"
                o['invoice_id'] = None
    else:
        recent_orders = []

    debt_snapshot = []
    if customer_ids:
        format_custs = ','.join(['%s'] * len(customer_ids))
        unpaid_invoices = frappe.db.sql(f"""
            SELECT name as invoice_id, customer as customer_id, customer_name, posting_date, due_date, grand_total, outstanding_amount
            FROM `tabSales Invoice`
            WHERE docstatus = 1 AND outstanding_amount > 0 AND customer IN ({format_custs})
            ORDER BY due_date ASC
        """, tuple(customer_ids), as_dict=True)

        if unpaid_invoices:
            inv_names = [inv.invoice_id for inv in unpaid_invoices]
            format_invs = ','.join(['%s'] * len(inv_names))
            inv_items = frappe.db.sql(f"""
                SELECT parent, item_code, item_name, qty, rate, amount
                FROM `tabSales Invoice Item`
                WHERE parent IN ({format_invs})
            """, tuple(inv_names), as_dict=True) 

            inv_item_map = {}
            for it in inv_items:
                inv_item_map.setdefault(it.parent, []).append(it)

            for inv in unpaid_invoices:
                inv['items'] = inv_item_map.get(inv.invoice_id, [])
            
            debt_snapshot = unpaid_invoices

    start_of_month = get_first_day(today())
    end_of_month = get_last_day(today())

    user = frappe.session.user
    sales_target = 0.0
    collection_target = 0.0
    
    employee_id = frappe.db.get_value("Employee", {"user_id": user}, "name")
    root_sp = get_root_sales_person(user)
    
    cascade_path = [
        ("User", user),
        ("Employee", employee_id),
        ("Sales Person", root_sp)
    ]
    
    for doctype, docname in cascade_path:
        if not docname: continue
        
        if sales_target == 0.0 and frappe.db.has_column(doctype, "custom_sales_target"):
            val = frappe.db.get_value(doctype, docname, "custom_sales_target")
            if val: sales_target = float(val)
            
        if collection_target == 0.0 and frappe.db.has_column(doctype, "custom_collection_target"):
            val = frappe.db.get_value(doctype, docname, "custom_collection_target")
            if val: collection_target = float(val)
            
        if sales_target > 0.0 and collection_target > 0.0:
            break

    mtd_sales = 0.0
    mtd_collections = 0.0

    if customer_ids:
        format_custs = ','.join(['%s'] * len(customer_ids))
        
        sales_data = frappe.db.sql(f"""
            SELECT SUM(grand_total) as value
            FROM `tabSales Invoice`
            WHERE docstatus = 1 AND posting_date BETWEEN %s AND %s
            AND customer IN ({format_custs})
        """, tuple([start_of_month, end_of_month] + customer_ids), as_dict=True)
        mtd_sales = sales_data[0]['value'] if sales_data and sales_data[0]['value'] else 0.0

        collection_data = frappe.db.sql(f"""
            SELECT SUM(paid_amount) as value
            FROM `tabPayment Entry`
            WHERE docstatus = 1 AND payment_type = 'Receive' AND posting_date BETWEEN %s AND %s
            AND party_type = 'Customer' AND party IN ({format_custs})
        """, tuple([start_of_month, end_of_month] + customer_ids), as_dict=True)
        mtd_collections = collection_data[0]['value'] if collection_data and collection_data[0]['value'] else 0.0

    dashboard_stats = {
        "sales_target": float(sales_target),
        "collection_target": float(collection_target),
        "mtd_sales": float(mtd_sales),
        "mtd_collections": float(mtd_collections)
    }

    return {
        "status": "success",
        "data": {
            "customers": customers,
            "items": items,
            "prices": prices,
            "bins": bins,
            "regions": regions,
            "customer_groups": customer_groups,
            "territories": territories,
            "price_lists": price_lists,
            "payment_terms_templates": payment_terms_templates,
            "currencies": currencies,
            "tax_categories": tax_categories,
            "order_history": recent_orders,
            "debt_snapshot": debt_snapshot,
            "dashboard_stats": dashboard_stats
        }
    }

@frappe.whitelist()
def get_invoice_details_for_order(order_id):
    """
    On-Demand fetch for the Differential Viewer.
    Pulls strictly the invoiced items associated with a specific Sales Order intent.
    """
    items = frappe.db.sql("""
        SELECT si.item_code, si.item_name, si.qty, si.rate, si.amount
        FROM `tabSales Invoice Item` si
        JOIN `tabSales Invoice` s ON si.parent = s.name
        WHERE si.sales_order = %s AND s.docstatus = 1
    """, (order_id,), as_dict=True)
    
    return {"status": "success", "data": items}

@frappe.whitelist()
def submit_sales_order_from_app(payload):
    """
    Takes the cart payload and securely routes it through Frappe's ORM.
    Handles pricing, taxes, and permissions natively before saving as a Draft.
    """
    if isinstance(payload, str):
        payload = json.loads(payload)

    try:
        so = frappe.new_doc("Sales Order")
        so.customer = payload.get("customer")
        so.order_type = "Sales"
        so.transaction_date = today()
        so.delivery_date = add_days(today(), 1)
        
        if payload.get("delivery_region"):
            so.custom_delivery_region = payload.get("delivery_region")
            
        if payload.get("notes"):
            so.inter_company_reference = payload.get("notes") 

        for item in payload.get("items", []):
            so.append("items", {
                "item_code": item.get("item_code"),
                "qty": float(item.get("qty")),
                "rate": float(item.get("rate")),
                "description": payload.get("notes", "") 
            })

        sales_person = get_root_sales_person(frappe.session.user)
        if sales_person:
            so.append("sales_team", {
                "sales_person": sales_person,
                "allocated_percentage": 100.0
            })

        so.insert(ignore_permissions=True)
        
        return {
            "status": "success", 
            "erp_order_id": so.name, 
            "message": f"Order {so.name} successfully created."
        }

    except Exception as e:
        frappe.log_error(title="App Order Submission Failed", message=str(e))
        return {"status": "error", "message": f"Failed to create order: {str(e)}"}

@frappe.whitelist()
def register_sales_check_in(customer, lat, lng):
    user_email = frappe.session.user
    
    cust_coords = frappe.db.get_value("Customer", customer, ["custom_latitude", "custom_longitude"], as_dict=True)
    
    distance = 0.0
    if cust_coords and cust_coords.get("custom_latitude") and cust_coords.get("custom_longitude"):
        try:
            clat, clng = float(cust_coords.custom_latitude), float(cust_coords.custom_longitude)
            dlat = math.radians(float(lat) - clat)
            dlng = math.radians(float(lng) - clng)
            a = math.sin(dlat/2)**2 + math.cos(math.radians(clat)) * math.cos(math.radians(float(lat))) * math.sin(dlng/2)**2
            c = 2 * math.atan2(math.sqrt(a), math.sqrt(1-a))
            distance = 6371.0 * c * 1000 
        except:
            pass

    doc = frappe.new_doc("Nexus Sales Visit")
    doc.sales_person = user_email 
    doc.customer = customer
    doc.check_in_time = frappe.utils.now_datetime()
    doc.latitude = str(lat)
    doc.longitude = str(lng)
    doc.distance_from_target_meters = distance
    doc.insert(ignore_permissions=True)

    try:
        requests.post(
            "https://crystal-api.crystalapps.dev/telemetry/sales-check-in",
            json={"sales_rep": frappe.session.user, "customer": customer},
            timeout=2
        )
    except Exception:
        pass 

    return {"status": "success", "message": "Check-In recorded successfully.", "distance_m": distance, "visit_id": doc.name}

@frappe.whitelist()
def register_sales_check_out(customer):
    user_email = frappe.session.user

    visit_name = frappe.db.sql("""
        SELECT name, check_in_time 
        FROM `tabNexus Sales Visit`
        WHERE sales_person = %s AND customer = %s AND (check_out_time IS NULL OR check_out_time = '')
        ORDER BY creation DESC LIMIT 1
    """, (user_email, customer), as_dict=True) 

    if not visit_name:
        return {"status": "error", "message": "No active check-in found to close."}

    doc_name = visit_name[0].name
    check_in_time = visit_name[0].check_in_time
    check_out_time = frappe.utils.now_datetime()

    duration_minutes = 0.0
    if check_in_time:
        in_dt = get_datetime(check_in_time)
        out_dt = get_datetime(check_out_time)
        duration_minutes = round((out_dt - in_dt).total_seconds() / 60, 2)

    try:
        frappe.db.set_value("Nexus Sales Visit", doc_name, {
            "check_out_time": check_out_time,
            "duration_minutes": duration_minutes
        })
    except Exception as e:
        if "1020" in str(e) or "Record has changed" in str(e):
            frappe.db.rollback()
            pass 
        else:
            return {"status": "error", "message": str(e)}

    return {"status": "success", "message": "Checked out successfully.", "duration_minutes": duration_minutes}

@frappe.whitelist()
def get_extended_sales_reports(report_type):
    """
    On-Demand fetch for Debt Collection. Now aggressively optimized with JOINs 
    to pull exact line items for transparent dispute resolution on the mobile UI.
    """
    auth_sps = get_authorized_sales_persons(frappe.session.user)
    if not auth_sps: return {"status": "error", "message": "No sales profile hierarchy."}

    format_sps = ','.join(['%s'] * len(auth_sps))
    tuple_sps = tuple(auth_sps)

    assigned_customers = frappe.db.sql(f"""
        SELECT parent FROM `tabSales Team` WHERE parenttype = 'Customer' AND sales_person IN ({format_sps})
    """, tuple_sps, as_dict=False)
    
    customer_list = [c[0] for c in assigned_customers] if assigned_customers else []
    if not customer_list: return {"status": "success", "data": []}
    
    format_customers = ','.join(['%s'] * len(customer_list))
    data = []

    if report_type == "Outstanding":
        start_of_year = datetime(today().year, 1, 1).strftime('%Y-%m-%d')
        data = frappe.db.sql(f"""
            SELECT name as invoice_id, customer as customer_id, customer_name, posting_date, grand_total, outstanding_amount, due_date
            FROM `tabSales Invoice`
            WHERE docstatus = 1 AND outstanding_amount > 0 AND posting_date >= %s
            AND customer IN ({format_customers})
            ORDER BY posting_date DESC
        """, tuple([start_of_year] + customer_list), as_dict=True)

    elif report_type == "Overdues":
        data = frappe.db.sql(f"""
            SELECT name as invoice_id, customer as customer_id, customer_name, posting_date, grand_total, outstanding_amount, due_date
            FROM `tabSales Invoice`
            WHERE docstatus = 1 AND outstanding_amount > 0 AND due_date < %s
            AND customer IN ({format_customers})
            ORDER BY due_date ASC
        """, tuple([today()] + customer_list), as_dict=True)

    elif report_type == "PDC":
        max_date = add_months(today(), 2)
        data = frappe.db.sql(f"""
            SELECT name as payment_entry, party as customer, party_name, reference_date, paid_amount, reference_no
            FROM `tabPayment Entry`
            WHERE docstatus = 1 AND payment_type = 'Receive' AND party_type = 'Customer'
            AND reference_date BETWEEN %s AND %s
            AND party IN ({format_customers})
            ORDER BY reference_date ASC
        """, tuple([today(), max_date] + customer_list), as_dict=True)
        return {"status": "success", "data": data}

    else:
        return {"status": "error", "message": "Invalid report type."}

    if data:
        inv_names = [d.invoice_id for d in data]
        format_invs = ','.join(['%s'] * len(inv_names))
        items = frappe.db.sql(f"""
            SELECT parent, item_code, item_name, qty, rate, amount
            FROM `tabSales Invoice Item`
            WHERE parent IN ({format_invs})
        """, tuple(inv_names), as_dict=True) 

        item_map = {}
        for it in items:
            item_map.setdefault(it.parent, []).append(it)

        for d in data:
            d['items'] = item_map.get(d.invoice_id, [])

    return {"status": "success", "data": data}

def trigger_app_customer_refresh(doc, method=None):
    old_doc = doc.get_doc_before_save()
    if not old_doc: return

    monitored_fields = ['customer_name', 'default_price_list', 'payment_terms', 'mobile_no', 'custom_latitude', 'custom_longitude']
    vault_data_changed = any(doc.get(field) != old_doc.get(field) for field in monitored_fields)

    old_sales_persons = set([row.sales_person for row in old_doc.get("sales_team", []) if row.sales_person])
    new_sales_persons = set([row.sales_person for row in doc.get("sales_team", []) if row.sales_person])
    
    affected_sales_persons = old_sales_persons.symmetric_difference(new_sales_persons)
    
    if vault_data_changed:
        affected_sales_persons.update(new_sales_persons)

    if not affected_sales_persons:
        return 

    affected_emails = set()
    format_affected = ','.join(['%s'] * len(affected_sales_persons))
    
    affected_coords = frappe.db.sql(f"""
        SELECT lft, rgt FROM `tabSales Person` WHERE name IN ({format_affected})
    """, tuple(affected_sales_persons), as_dict=True)

    if affected_coords:
        or_conditions = [f"(lft <= {c.lft} AND rgt >= {c.rgt})" for c in affected_coords]
        where_clause = " OR ".join(or_conditions)

        ancestor_sps = frappe.db.sql(f"""
            SELECT name, employee FROM `tabSales Person` WHERE {where_clause}
        """, as_dict=True)

        for sp in ancestor_sps:
            if sp.employee:
                user_email = frappe.db.get_value("Employee", sp.employee, "user_id")
                if user_email:
                    affected_emails.add(user_email)
            elif "@" in sp.employee: 
                affected_emails.add(sp.employee)

    if affected_emails:
        try:
            requests.post(
                "https://crystal-api.crystalapps.dev/telemetry/force-app-refresh",
                json={"emails": list(affected_emails), "command": "FORCE_REFRESH_CUSTOMERS"},
                timeout=3
            )
        except Exception:
            pass

def trigger_app_catalog_refresh(doc, method=None):
    if hasattr(doc, 'docstatus') and doc.docstatus == 0:
        return
        
    reps = frappe.db.sql("""
        SELECT e.user_id 
        FROM `tabSales Person` sp
        JOIN `tabEmployee` e ON sp.employee = e.name
        WHERE e.user_id IS NOT NULL AND e.status = 'Active'
    """, as_dict=True)
    
    affected_emails = set([r.user_id for r in reps if r.user_id])
    
    fallback = frappe.db.sql("""
        SELECT employee FROM `tabSales Person` WHERE employee LIKE '%@%'
    """, as_dict=True)
    for r in fallback:
        affected_emails.add(r.employee)
        
    if affected_emails:
        try:
            requests.post(
                "https://crystal-api.crystalapps.dev/telemetry/force-app-refresh",
                json={"emails": list(affected_emails), "command": "FORCE_VAULT_SYNC"},
                timeout=3
            )
        except Exception as e:
            frappe.log_error(title="App Catalog Refresh Trigger Failed", message=str(e))

def trigger_financial_refresh(doc, method=None):
    """
    🚨 FIX: Triggered on Payment Entry.
    Traces Payment -> Sales Invoice -> Sales Order, recalculates payment status,
    and ships the updated_orders array and increment_collection to the mobile app for 0-lag badging.
    """
    if doc.party_type != 'Customer' or not doc.party:
        return

    increment_collection = 0.0
    if doc.payment_type == 'Receive':
        if doc.docstatus == 1:
            increment_collection = float(doc.paid_amount or 0.0)
        elif doc.docstatus == 2:
            increment_collection = -float(doc.paid_amount or 0.0)

    invoice_ids = []
    if hasattr(doc, 'references'):
        for ref in doc.references:
            if ref.reference_doctype == 'Sales Invoice' and ref.reference_name:
                invoice_ids.append(ref.reference_name)

    updated_orders = []
    if invoice_ids:
        format_invs = ','.join(['%s'] * len(invoice_ids))
        sos = frappe.db.sql(f"""
            SELECT DISTINCT sales_order 
            FROM `tabSales Invoice Item` 
            WHERE parent IN ({format_invs}) AND sales_order IS NOT NULL AND sales_order != ''
        """, tuple(invoice_ids), as_dict=True)
        
        if sos:
            so_names = [s.sales_order for s in sos]
            format_sos = ','.join(['%s'] * len(so_names))
            so_invs = frappe.db.sql(f"""
                SELECT si.sales_order, s.outstanding_amount, s.grand_total 
                FROM `tabSales Invoice Item` si 
                JOIN `tabSales Invoice` s ON si.parent = s.name 
                WHERE si.sales_order IN ({format_sos}) AND s.docstatus = 1
            """, tuple(so_names), as_dict=True)
            
            so_map = {}
            for inv in so_invs:
                if inv.sales_order not in so_map:
                    so_map[inv.sales_order] = {'grand': 0, 'out': 0}
                so_map[inv.sales_order]['grand'] += inv.grand_total
                so_map[inv.sales_order]['out'] += inv.outstanding_amount
                
            for so_name in so_names:
                data = so_map.get(so_name)
                if data:
                    if data['out'] <= 0: p_status = "Paid"
                    elif data['out'] < data['grand']: p_status = "Partially Paid"
                    else: p_status = "Unpaid"
                    updated_orders.append({"id": so_name, "payment_status": p_status})

    sales_team = frappe.db.sql("""
        SELECT sales_person FROM `tabSales Team` 
        WHERE parent = %s AND parenttype = 'Customer'
    """, (doc.party,), as_dict=True)
    
    if not sales_team:
        return
        
    affected_sales_persons = set([row.sales_person for row in sales_team if row.sales_person])
    if not affected_sales_persons:
        return 

    affected_emails = set()
    format_affected = ','.join(['%s'] * len(affected_sales_persons))
    
    affected_coords = frappe.db.sql(f"""
        SELECT lft, rgt FROM `tabSales Person` WHERE name IN ({format_affected})
    """, tuple(affected_sales_persons), as_dict=True)

    if affected_coords:
        or_conditions = [f"(lft <= {c.lft} AND rgt >= {c.rgt})" for c in affected_coords]
        where_clause = " OR ".join(or_conditions)

        ancestor_sps = frappe.db.sql(f"""
            SELECT name, employee FROM `tabSales Person` WHERE {where_clause}
        """, as_dict=True)

        for sp in ancestor_sps:
            if sp.employee:
                user_email = frappe.db.get_value("Employee", sp.employee, "user_id")
                if user_email:
                    affected_emails.add(user_email)
            elif "@" in sp.employee: 
                affected_emails.add(sp.employee)

    if affected_emails:
        try:
            requests.post(
                "https://crystal-api.crystalapps.dev/telemetry/force-app-refresh",
                json={
                    "emails": list(affected_emails), 
                    "command": "PAYMENT_RECEIVED",
                    "customer_id": doc.party,
                    "invoice_ids": invoice_ids,
                    "updated_orders": updated_orders,
                    "increment_collection": increment_collection
                },
                timeout=3
            )
        except Exception:
            pass

def trigger_order_status_update(doc, method=None):
    """
    🚨 FIX: Triggered on Sales Order update.
    Extracts invoice_id and determines 'Paid' / 'Unpaid' status before pushing to UI.
    """
    if not doc.owner or "@" not in doc.owner:
        return
        
    payment_status = "Unpaid"
    invoice_id = None
    
    invoices = frappe.db.sql("""
        SELECT s.name as invoice_id, s.outstanding_amount, s.grand_total 
        FROM `tabSales Invoice Item` si 
        JOIN `tabSales Invoice` s ON si.parent = s.name 
        WHERE si.sales_order = %s AND s.docstatus = 1
    """, (doc.name,), as_dict=True)
    
    if invoices:
        invoice_id = invoices[0].invoice_id
        total_grand = sum(i.get('grand_total', 0) for i in invoices)
        total_out = sum(i.get('outstanding_amount', 0) for i in invoices)
        if total_out <= 0:
            payment_status = "Paid"
        elif total_out < total_grand:
            payment_status = "Partially Paid"
        else:
            payment_status = "Unpaid"
            
    try:
        requests.post(
            "https://crystal-api.crystalapps.dev/telemetry/force-app-refresh",
            json={
                "emails": [doc.owner], 
                "command": "UPDATE_ORDER_STATUS",
                "order_id": doc.name,
                "status": doc.status,
                "payment_status": payment_status,
                "invoice_id": invoice_id 
            },
            timeout=3
        )
    except Exception as e:
        frappe.log_error(title="App Order Status Trigger Failed", message=str(e))

def trigger_sales_person_update(doc, method=None):
    """
    🚨 NEW HOOK: Triggered on Sales Person update.
    Checks if targets changed, and forces a silent background vault sync for that specific rep.
    """
    old_doc = doc.get_doc_before_save()
    if not old_doc:
        return
        
    targets_changed = (
        doc.get("custom_sales_target") != old_doc.get("custom_sales_target") or 
        doc.get("custom_collection_target") != old_doc.get("custom_collection_target")
    )
    
    if not targets_changed:
        return
        
    if not doc.employee:
        return
        
    user_email = frappe.db.get_value("Employee", doc.employee, "user_id")
    if not user_email:
        return
        
    try:
        requests.post(
            "https://crystal-api.crystalapps.dev/telemetry/force-app-refresh",
            json={
                "emails": [user_email], 
                "command": "FORCE_VAULT_SYNC"
            },
            timeout=3
        )
    except Exception as e:
        frappe.log_error(title="App Sales Person Trigger Failed", message=str(e))

def _add_sp_and_ancestors(sales_person, affected_emails):
    sp_doc = frappe.db.get_value("Sales Person", sales_person, ["lft", "rgt"], as_dict=True)
    if not sp_doc: return
    ancestors = frappe.db.sql("SELECT employee FROM `tabSales Person` WHERE lft <= %s AND rgt >= %s", (sp_doc.lft, sp_doc.rgt), as_dict=True)
    for a in ancestors:
        if a.employee:
            user_email = frappe.db.get_value("Employee", a.employee, "user_id")
            if user_email: affected_emails.add(user_email)
            elif "@" in a.employee: affected_emails.add(a.employee)

def _get_all_sales_rep_emails():
    """
    Returns a set of all active sales rep emails (from Sales Person -> Employee -> User).
    Used for global metadata changes (Customer Group, Territory, Currency, Tax Category).
    """
    emails = set()
    reps = frappe.db.sql("""
        SELECT e.user_id
        FROM `tabSales Person` sp
        JOIN `tabEmployee` e ON sp.employee = e.name
        WHERE sp.enabled = 1 AND e.user_id IS NOT NULL AND e.user_id != ''
    """, as_dict=True)
    for r in reps:
        if r.user_id:
            emails.add(r.user_id)
    
    fallback = frappe.db.sql("""
        SELECT employee FROM `tabSales Person`
        WHERE enabled = 1 AND employee LIKE '%@%'
    """, as_dict=True)
    for f in fallback:
        emails.add(f.employee)
    
    return emails

def trigger_cache_eviction_and_notify(doc, method=None):
    """
    Centralized Webhook Runner: Fires to FastAPI to drop the cache and notify mobile units.
    """
    try:
        if getattr(frappe.flags, 'in_import', False):
            return

        if hasattr(doc, 'docstatus') and doc.docstatus == 0 and doc.doctype != "Customer":
            return

        affected_emails = set()
        
        if doc.doctype in ["Item", "Item Price", "Stock Entry", "Stock Reconciliation", "Purchase Receipt", "Delivery Note"]:
            reps = frappe.db.sql("""
                SELECT e.user_id, sp.employee 
                FROM `tabSales Person` sp
                LEFT JOIN `tabEmployee` e ON sp.employee = e.name
                WHERE sp.enabled = 1
            """, as_dict=True)
            for r in reps:
                if r.user_id: affected_emails.add(r.user_id)
                elif r.employee and "@" in r.employee: affected_emails.add(r.employee)
                
        elif doc.doctype == "Customer":
            for row in doc.get("sales_team", []):
                if row.sales_person: 
                    _add_sp_and_ancestors(row.sales_person, affected_emails)
                
            if doc.name and frappe.db.exists("Customer", doc.name):
                old_team = frappe.db.get_all(
                    "Sales Team", 
                    filters={"parent": doc.name, "parenttype": "Customer"}, 
                    fields=["sales_person"]
                )
                for old_row in old_team:
                    if old_row.get("sales_person"):
                        _add_sp_and_ancestors(old_row["sales_person"], affected_emails)

        elif doc.doctype in ["Sales Order", "Sales Invoice", "Payment Entry"]:
            customer_field = doc.party if doc.doctype == "Payment Entry" else doc.customer
            if customer_field:
                sales_team = frappe.db.sql("SELECT sales_person FROM `tabSales Team` WHERE parent=%s AND parenttype='Customer'", (customer_field,), as_dict=True)
                for row in sales_team:
                    if row.sales_person: _add_sp_and_ancestors(row.sales_person, affected_emails)
            if doc.owner and "@" in doc.owner:
                affected_emails.add(doc.owner)

        elif doc.doctype == "Sales Person":
            if doc.employee:
                user_email = frappe.db.get_value("Employee", doc.employee, "user_id")
                if user_email: affected_emails.add(user_email)

        elif doc.doctype in ["Customer Group", "Territory", "Currency", "Tax Category"]:
            affected_emails = _get_all_sales_rep_emails()

        if not affected_emails:
            frappe.log_error(
                title="Nexus Cache: No Emails Found",
                message=f"Doctype: {doc.doctype}, Docname: {doc.name}"
            )
            return

        command = "FORCE_VAULT_SYNC"
        if doc.doctype in ["Customer Group", "Territory", "Currency", "Tax Category"]:
            command = "FORCE_METADATA_REFRESH"
            
        frappe.enqueue(
            "nexus_supply_chain.api.execute_fastapi_webhook",
            queue="short",
            affected_emails=list(affected_emails),
            doctype=doc.doctype,
            docname=doc.name,
            command=command,
            enqueue_after_commit=True
        )
    except Exception as e:
        frappe.log_error(
            title="Nexus Cache Eviction Failed",
            message=f"Doctype: {doc.doctype}, Docname: {doc.name}, Error: {str(e)}"
        )

@frappe.whitelist()
def create_mobile_customer(payload):
    """
    Accepts frontend payload from React Native to dynamically create a customer.
    Stores phone number in custom_phone_number and location text in custom_location.
    """
    if isinstance(payload, str):
        payload = json.loads(payload)
        
    phone_number = payload.get("phone_number")
    location_text = payload.get("location_text") 
    customer_name = payload.get("customer_name")
    
    if not customer_name:
        return {"status": "error", "message": "Customer name is required."}
    
    try:
        doc = frappe.new_doc("Customer")
        doc.customer_name = customer_name
        doc.customer_type = payload.get("customer_type", "Company")
        doc.customer_group = payload.get("customer_group", "Commercial")
        doc.territory = payload.get("territory", "All Territories")
        doc.default_price_list = payload.get("default_price_list", "Standard Selling")
        doc.default_currency = payload.get("billing_currency", "KES")
        doc.tax_id = payload.get("tax_id")
        doc.tax_category = payload.get("tax_category")
        doc.payment_terms = payload.get("payment_terms")
        
        if phone_number:
            doc.mobile_no = phone_number
            doc.custom_phone_number = phone_number
            
        if location_text:
            doc.custom_location = location_text
        
        lat = payload.get("latitude") or payload.get("lat")
        lng = payload.get("longitude") or payload.get("lng")
        
        if lat and lng:
            doc.custom_latitude = str(lat)
            doc.custom_longitude = str(lng)
            
        if payload.get("custom_combined_coordinates"):
            doc.custom_combined_coordinates = payload.get("custom_combined_coordinates")

        if payload.get("google_maps_link"):
            doc.custom_google_maps_link = payload.get("google_maps_link")

        sales_person = payload.get("sales_person") or get_root_sales_person(frappe.session.user)
        if sales_person:
            doc.append("sales_team", {
                "sales_person": sales_person,
                "allocated_percentage": 100
            })
        
        doc.insert(ignore_permissions=True)
        frappe.db.commit()
        
        customer_id = doc.name
        
        return {"status": "success", "customer_id": customer_id, "message": f"Customer {customer_id} created successfully."}
        
    except Exception as e:
        frappe.log_error(title="Mobile Customer Creation Failed", message=str(e))
        frappe.db.rollback()
        return {"status": "error", "message": f"Failed to create customer: {str(e)}"}

@frappe.whitelist()
def update_customer_coordinates(customer, latitude, longitude, custom_combined_coordinates=None, google_maps_link=None):
    """
    Polymorphic injection target: allows direct coordinate saving bypassing web scrapers.
    🚨 FIX: Uses doc.save() instead of set_value to guarantee Redis Cache Eviction hooks fire!
    """
    try:
        if frappe.db.exists("Customer", customer):
            doc = frappe.get_doc("Customer", customer)
            doc.custom_latitude = str(latitude)
            doc.custom_longitude = str(longitude)
            
            if custom_combined_coordinates:
                doc.custom_combined_coordinates = custom_combined_coordinates
                
            if google_maps_link:
                doc.custom_google_maps_link = google_maps_link
                
            doc.flags.ignore_permissions = True
            doc.save()
            
            return {"status": "success", "message": "Location updated successfully.", "lat": latitude, "lng": longitude}
        return {"status": "error", "message": "Customer not found."}
    except Exception as e:
        frappe.log_error(title="Customer Location Update Failed", message=str(e))
        return {"status": "error", "message": str(e)}

@frappe.whitelist()
def register_sales_check_in_correction(visit_id, distance_m):
    """
    Corrects the variance tracking distance after a mobile map update
    """
    try:
        if frappe.db.exists("Nexus Sales Visit", visit_id):
            frappe.db.set_value("Nexus Sales Visit", visit_id, "distance_from_target_meters", float(distance_m))
            return {"status": "success", "message": "Distance corrected successfully."}
        return {"status": "error", "message": "Active visit record not found."}
    except Exception as e:
        frappe.log_error(title="Distance Correction Failed", message=str(e))
        return {"status": "error", "message": str(e)}

def trigger_post_import_cache_eviction(doc, method=None):
    """
    🚨 BULK IMPORT SWEEPER: Fires once after a Frappe v15 Data Import completes.
    Prevents the thundering herd problem by waiting until all rows are saved,
    then issuing a single global sweep command.
    """
    try:
        if doc.status not in ["Success", "Partial Success"]:
            return
            
        target_doctypes = ["Customer", "Item", "Item Price", "Customer Group", "Territory", "Currency", "Tax Category"]
        if doc.reference_doctype not in target_doctypes:
            return

        if not doc.has_value_changed("status"):
            return
            
        affected_emails = _get_all_sales_rep_emails()
        if not affected_emails:
            return
            
        command = "FORCE_VAULT_SYNC"
        if doc.reference_doctype in ["Customer Group", "Territory", "Currency", "Tax Category"]:
            command = "FORCE_METADATA_REFRESH"

        frappe.enqueue(
            "nexus_supply_chain.api.execute_fastapi_webhook",
            queue="short",
            affected_emails=list(affected_emails),
            doctype=doc.reference_doctype,
            docname=f"Bulk_Import_{doc.name}",
            command=command,
            enqueue_after_commit=True
        )
        
        frappe.log_error(title="Nexus Bulk Import Sweep", message=f"Successfully queued global cache eviction for {doc.reference_doctype} import.")
        
    except Exception as e:
        frappe.log_error(title="Nexus Post-Import Eviction Failed", message=f"Error: {str(e)}")

def execute_fastapi_webhook(affected_emails, doctype, docname, command):
    import requests
    import frappe
    import time
    
    time.sleep(3.0)
    
    try:
        fcm_tokens = {}
        if affected_emails:
            format_emails = ','.join(['%s'] * len(affected_emails))
            tokens_data = frappe.db.sql(f"""
                SELECT user, fcm_token 
                FROM `tabNexus FCM Device` 
                WHERE user IN ({format_emails})
            """, tuple(affected_emails), as_dict=True)
            
            for row in tokens_data:
                if row.user not in fcm_tokens:
                    fcm_tokens[row.user] = []
                fcm_tokens[row.user].append(row.fcm_token)

        requests.post(
            "https://crystal-api.crystalapps.dev/api/v1/cache/invalidate",
            json={
                "emails": list(affected_emails), 
                "fcm_tokens": fcm_tokens,
                "doctype": doctype, 
                "docname": docname,
                "command": command
            },
            timeout=5
        )
    except Exception as e:
        frappe.log_error(title="Cache Eviction API Failed", message=str(e))
