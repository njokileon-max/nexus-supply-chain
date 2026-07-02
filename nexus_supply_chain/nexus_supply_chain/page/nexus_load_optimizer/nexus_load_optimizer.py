# apps/nexus_supply_chain/nexus_supply_chain/page/nexus_load_optimizer/nexus_load_optimizer.py

import frappe
import requests
import json
from frappe.utils import flt, getdate, add_to_date, now_datetime
from datetime import datetime

from nexus_supply_chain.utils.cost_utils import (
    compute_total_theoretical_cost_for_orders,
    MarketCostEngine,
)

API_URL = "https://crystal-api.crystalapps.dev"  # Nexus Brain FastAPI (Local server-to-server is perfect here)
TARGET_WAREHOUSE = "Finished Goods - CAL"

# ──────────────────────────────────────────────────────────────────────
# 🔒 WORKFLOW GUARD: Resolve Finance-Approved Draft States Dynamically
# ──────────────────────────────────────────────────────────────────────
def _get_finance_approved_draft_states(doctype="Sales Order"):
    """
    Dynamically resolves which workflow_state values represent a Draft Sales
    Order that has PASSED Finance (Accounts Manager) approval.

    Strategy:
      1. Find the active Workflow for the given DocType.
      2. Find all transitions where the allowed role is "Accounts Manager".
      3. Collect the `next_state` values of those transitions — these are
         the states Finance MOVES the document INTO when they approve.
      4. Cross-check against the Workflow Document States table to keep only
         states that are still drafts (doc_status = "0").
      5. Strip any state whose action name contains "reject" — those are
         the reject-path next_states, NOT the approval-path ones.

    Returns:
      list[str] — state names to use in a workflow_state IN (...) filter.
      Returns None if no workflow is active (fall through, no filtering).
    """
    try:
        # 1. Active workflow for the doctype
        workflow_name = frappe.db.get_value(
            "Workflow",
            {"document_type": doctype, "is_active": 1},
            "name"
        )
        if not workflow_name:
            return None  # No workflow active — don't restrict

        # 2. All Accounts Manager transitions
        am_transitions = frappe.get_all(
            "Workflow Transition",
            filters={"parent": workflow_name, "allowed": "Accounts Manager"},
            fields=["action", "next_state"]
        )
        if not am_transitions:
            return None  # AM role not in this workflow

        # 3. Collect only APPROVAL next_states (exclude reject-path actions)
        candidate_states = [
            t.next_state for t in am_transitions
            if "reject" not in (t.action or "").lower()
        ]
        if not candidate_states:
            return None

        # 4. Keep only draft-docstatus states (doc_status = "0")
        draft_states = frappe.db.sql_list("""
            SELECT state
            FROM `tabWorkflow Document State`
            WHERE parent = %s
              AND doc_status = '0'
              AND state IN ({placeholders})
        """.format(placeholders=", ".join(["%s"] * len(candidate_states))),
            [workflow_name] + candidate_states
        )

        return draft_states if draft_states else None

    except Exception:
        frappe.log_error(frappe.get_traceback(), "Nexus: Workflow State Resolution Failed")
        return None  # Safe fallback — don't block the optimizer

@frappe.whitelist()
def run_optimizer(filters=None):
    """
    Acts as a Data Aggregator:
    1. Fetches unplanned Sales Orders and raw Stock levels.
    2. Retrieves Regional Optimization Radii from the database.
    3. Offloads all clustering, utilization math, and wait-time logic to the External API.
    """
    if isinstance(filters, str):
        filters = frappe.parse_json(filters) or {}
    elif filters is None:
        filters = {}

    company_name = filters.get("company")
    vehicle_type = filters.get("vehicle_type")
    
    if not company_name or not vehicle_type:
        frappe.throw("Company and Vehicle Type are mandatory to run optimization.")

    # 1. Strict Dynamic Origin Validation (No Fallbacks)
    factory_lat = flt(frappe.db.get_value("Company", company_name, "custom_latitude"))
    factory_lng = flt(frappe.db.get_value("Company", company_name, "custom_longitude"))

    if not factory_lat or not factory_lng:
        frappe.throw(
            msg=f"Missing GPS Coordinates for Company '{company_name}'. Please set 'custom_latitude' and 'custom_longitude' in the Company settings.",
            title="Routing Configuration Error"
        )

    # 2. Fetch Regional Radii Map for "Strict Mode" Validation
    regions = frappe.get_all("Delivery Region", fields=["name", "custom_optimization_radius"])
    radius_map = {r.name: flt(r.custom_optimization_radius) for r in regions}

    # 3. Define Base Conditions (Unplanned Orders for specific Company)
    # 3. Define Base Conditions (Unplanned Orders for specific Company)
    conditions = [
        ["company", "=", company_name]
    ]

    # Dynamically assign docstatus based on the requested order status
    so_status = filters.get("sales_order_status")
    
    if so_status == "Draft":
        # Drafts are always unsubmitted
        conditions.append(["docstatus", "=", 0])
        conditions.append(["status", "=", "Draft"])

        # ── WORKFLOW GUARD ──────────────────────────────────────────────
        # Only surface Drafts that Finance (Accounts Manager) has already
        # approved. This excludes:
        #   • "Proceed To Order"          — not yet sent to Finance
        #   • "Pending Finance Approval"  — Finance hasn't acted yet
        #   • Any Finance-Rejected state  — rejected via workflow action
        #
        # The helper queries the live Workflow definition so state names
        # are never hardcoded here — safe against workflow renames.
        # ────────────────────────────────────────────────────────────────
        finance_approved_states = _get_finance_approved_draft_states("Sales Order")
        if finance_approved_states:
            conditions.append(["workflow_state", "in", finance_approved_states])
        else:
            # No active workflow or AM role not found.
            # Log a one-time notice and proceed without the guard so the
            # optimizer is never silently broken.
            frappe.log_error(
                "No active Sales Order workflow found for Accounts Manager role. "
                "Draft filter is running WITHOUT finance-approval guard.",
                "Nexus Load Optimizer — Workflow Guard Skipped"
            )
    else:
        # 'To Deliver', 'To Deliver and Bill', etc., are submitted documents
        conditions.append(["docstatus", "=", 1])
        if so_status:
            conditions.append(["status", "=", so_status])

    # Exclude Sales Orders already assigned to active Load Plans
    planned_sos = frappe.db.sql("""
        SELECT so.sales_order 
        FROM `tabNexus Load Plan Sales Order` so
        INNER JOIN `tabNexus Load Plan` lp ON so.parent = lp.name
        WHERE lp.docstatus < 2
    """, as_dict=True)
    
    if planned_sos:
        planned_so_names = [d.sales_order for d in planned_sos]
        conditions.append(["name", "not in", planned_so_names])

    # Also exclude SOs that already have submitted Delivery Notes —
    # these are dispatched regardless of whether they were on a Load Plan.
    dispatched_via_dnote = frappe.db.sql("""
        SELECT DISTINCT dni.against_sales_order
        FROM `tabDelivery Note Item` dni
        INNER JOIN `tabDelivery Note` dn ON dn.name = dni.parent
        WHERE dn.docstatus = 1
          AND dni.against_sales_order IS NOT NULL
          AND dni.against_sales_order != ''
    """, as_dict=True)

    if dispatched_via_dnote:
        dnote_so_names = [d.against_sales_order for d in dispatched_via_dnote]
        conditions.append(["name", "not in", dnote_so_names])

    # Apply UI Filters
    if filters.get("customer"): conditions.append(["customer", "=", filters.get("customer")])
    # NOTE: sales_order_status is intentionally NOT re-applied here.
    # docstatus + status were already set in the if/else block above.
    # Re-applying it would create a duplicate condition and could conflict
    # with the workflow_state guard on Draft orders.
    if filters.get("territory"): conditions.append(["territory", "=", filters.get("territory")])
    if filters.get("delivery_region"): conditions.append(["custom_delivery_region", "=", filters.get("delivery_region")])
    
    if filters.get("from_date"):
        try: conditions.append(["transaction_date", ">=", getdate(filters["from_date"])])
        except: pass
    if filters.get("to_date"):
        try: conditions.append(["transaction_date", "<", add_to_date(getdate(filters["to_date"]), days=1)])
        except: pass

    # Fetch raw Sales Order data
    sos = frappe.get_all(
        "Sales Order", 
        filters=conditions, 
        fields=["name", "customer", "customer_name", "status", "grand_total", "creation", "payment_terms_template", "custom_delivery_region"]
    )

    if not sos:
        return {"groups": [], "message": "No matching un-planned Sales Orders found."}

    # 4. Batch Fetch Customer Coordinates
    customer_ids = list(set([so.customer for so in sos]))
    customers_data = frappe.get_all(
        "Customer", 
        filters={"name": ("in", customer_ids)}, 
        fields=["name", "custom_latitude", "custom_longitude"]
    )
    customer_coords_map = {
        c.name: {
            "lat": flt(c.custom_latitude), 
            "lng": flt(c.custom_longitude)
        } for c in customers_data
    }

    # 5. Build Payload for External API
    # 5. Build Payload for External API
    payload_orders = []
    item_name_map = {}  # 🚨 NEW: Store names locally to bypass API data stripping

    for so in sos:
        region = so.custom_delivery_region
        if not region:
            frappe.throw(f"Sales Order <b>{so.name}</b> has no Delivery Region assigned.")
        
        radius = radius_map.get(region)
        if not radius or radius <= 0:
            frappe.throw(f"The region <b>{region}</b> (assigned to {so.name}) has no Optimization Radius set.")

        so_doc = frappe.get_doc("Sales Order", so.name)
        items = []
        
        for item in so_doc.items:
            # 🚨 NEW: Safely map the master item name in Python ONCE per unique item
            if item.item_code not in item_name_map:
                master_name = frappe.db.get_value("Item", item.item_code, "item_name")
                item_name_map[item.item_code] = master_name or item.item_code

            weight = flt(frappe.db.get_value("Item", item.item_code, "weight_per_unit") or 0)
            price_list_rate = flt(frappe.db.get_value("Item Price", {"item_code": item.item_code, "price_list": "Standard Selling"}, "price_list_rate") or item.rate)
            stock = flt(frappe.db.get_value("Bin", {"item_code": item.item_code, "warehouse": TARGET_WAREHOUSE}, "actual_qty") or 0)

            items.append({
                "item_code": item.item_code,
                "qty": flt(item.qty),
                "rate": flt(item.rate),
                "weight_per_unit": weight,
                "default_pl_rate": price_list_rate,
                "net_available": stock
            })

        coords = customer_coords_map.get(so.customer, {"lat": 0.0, "lng": 0.0})

        payload_orders.append({
            "sales_order": so.name,
            "creation": so.creation.isoformat(),
            "customer": so.customer,
            "customer_name": so.customer_name or "",
            "payment_terms": so.payment_terms_template or "Standard / Cash", 
            "latitude": coords["lat"],
            "longitude": coords["lng"],
            "delivery_region": region,
            "optimization_radius": radius,
            "items": items,
            "amount": flt(so.grand_total or 0),
            "so_status": so.status
        })

    vt_doc = frappe.get_doc("Vehicle Type", vehicle_type)
    max_tonnage = flt(vt_doc.max_tonnage) or 1000.0

    payload = {
        "sales_orders": payload_orders,
        "vehicle_max_tonnage": max_tonnage,
        "is_on_collection": filters.get("transport_mode") == "On-Collection"
    }

    # 6. Execute Remote Optimization
    try:
        resp = requests.post(f"{API_URL}/optimize", json=payload, timeout=60)
        resp.raise_for_status()
        data = resp.json()
        
        groups = data.get("groups", [])
        
        # 🚨 NEW: Intercept API response and re-inject the actual Item Names
        for group in groups:
            for so in group.get("sales_orders", []):
                for item in so.get("items", []):
                    ic = item.get("item_code")
                    if ic:
                        item["item_name"] = item_name_map.get(ic, ic)

        return {
            "groups": groups,
            "factory_lat": factory_lat,
            "factory_lng": factory_lng,
            "debug": data.get("debug", {})
        }

    except Exception as e:
        frappe.log_error(str(e), "Nexus Load Optimizer External API Error")
        return {"error": f"The Optimization Engine is currently unreachable: {str(e)}", "groups": []}


@frappe.whitelist()
def create_load_plan(group, action, route_geojson=None, vehicle_type=None, transport_mode=None, company=None, delivery_region=None):
    """
    Creates a physical Nexus Load Plan document from optimized group data.
    Server-side authoritative route generation ensures 100% accuracy regardless of UI state.
    """
    if isinstance(group, str):
        group = frappe.parse_json(group)

    plan = frappe.new_doc("Nexus Load Plan")
    plan.company = company or frappe.defaults.get_user_default("Company")
    
    # Safety Check: Ensure we have a company before proceeding
    if not plan.company:
        frappe.throw("A valid Company is required to create a Load Plan.")

    plan.vehicle_type = vehicle_type or group.get("vehicle_type")
    plan.transport_mode = transport_mode
    plan.delivery_region = delivery_region or group.get("delivery_region")
    plan.total_tonnage = flt(group.get("total_tonnage", 0))
    plan.total_amount = flt(group.get("total_amount", 0))
    plan.utilization = flt(group.get("utilization", 0))
    plan.max_capacity = flt(group.get("max_capacity", 0))
    plan.reservation_status = "Soft"

    # 🚨 FIX: Server-Side Map Orchestration (Bypass the frontend payload entirely)
    factory_lat = flt(frappe.db.get_value("Company", plan.company, "custom_latitude"))
    factory_lng = flt(frappe.db.get_value("Company", plan.company, "custom_longitude"))

    if not factory_lat or not factory_lng:
        frappe.throw(f"Missing GPS Coordinates for Company '{plan.company}'. Please configure 'custom_latitude' and 'custom_longitude' in the Company record.")

    # Construct coordinate array: Start at Factory -> Stops -> End at Factory
    FACTORY_COORDS = [factory_lng, factory_lat]
    route_coords = [FACTORY_COORDS]

    for so in group.get("sales_orders", []):
        lat = flt(so.get("latitude", 0.0))
        lng = flt(so.get("longitude", 0.0))
        
        # Build the document child table
        plan.append("sales_orders", {
            "sales_order": so.get("sales_order"),
            "customer": so.get("customer"),
            "customer_name": so.get("customer_name"),
            "wait_time": so.get("wait_time"),
            "revenue_state": so.get("revenue_state"),
            "readiness": so.get("readiness"),
            "payment_terms": so.get("payment_terms"), 
            "total_weight": flt(so.get("total_weight", 0)),
            "amount": flt(so.get("amount", 0)),
            "latitude": lat,
            "longitude": lng
        })
        
        if lat and lng:
            route_coords.append([lng, lat])

    route_coords.append(FACTORY_COORDS)

    approximate_total_distance_km = 0.0
    approximate_fuel_consumption_ltrs = 0.0
    approximate_fuel_cost = 0.0

    # Automatically fetch and lock the route from VROOM
    if len(route_coords) >= 3:
        try:
            resp = requests.post(
                f"{API_URL}/calculate-route", 
                json={"coordinates": route_coords}, 
                timeout=15
            )
            if resp.status_code == 200:
                data = resp.json()
                if "features" in data:
                    plan.route_geojson = json.dumps(data)
                    
                    # 🚚 Extract precise distance in meters from MapLibre/ORS and convert to Kilometers
                    try:
                        distance_m = flt(data["features"][0]["properties"]["summary"]["distance"])
                        approximate_total_distance_km = distance_m / 1000.0
                    except (KeyError, IndexError):
                        pass
        except Exception as e:
            frappe.log_error(str(e), "Load Plan Route Generation Failed")

    # ⛽ Calculate Fuel Economics dynamically based on the specific Vehicle Type
    if plan.vehicle_type:
        try:
            vt = frappe.get_doc("Vehicle Type", plan.vehicle_type)
            km_per_ltr = flt(vt.consumption_km_per_ltr)
            litre_cost = flt(vt.litre_cost)
            
            if km_per_ltr > 0:
                approximate_fuel_consumption_ltrs = approximate_total_distance_km / km_per_ltr
                approximate_fuel_cost = approximate_fuel_consumption_ltrs * litre_cost
        except Exception as e:
            frappe.log_error(str(e), "Load Plan Fuel Economic Calculation Failed")

    # Inject fuel estimates into the physical document
    plan.approximate_total_distance_km = approximate_total_distance_km
    plan.approximate_fuel_consumption_ltrs = approximate_fuel_consumption_ltrs
    plan.approximate_fuel_cost = approximate_fuel_cost

    # ------------------------------------------------------------------
    # 🚨 PHASE 2: Dynamic Margin Analytics (Ledger-Backed)
    # ------------------------------------------------------------------
    sales_orders_data = group.get("sales_orders", [])
    total_order_value = flt(group.get("total_amount", 0))
    if not total_order_value and sales_orders_data:
        total_order_value = sum(flt(so.get("amount", 0)) for so in sales_orders_data)

    # 1. Total theoretical production cost (BOM rollup)
    total_theoretical_cost = compute_total_theoretical_cost_for_orders(sales_orders_data)

    # 2. Dynamic Absorbed Overhead from Cost Allocation Period
    # 2. Fixed Overhead Absorption (Company Standard: KES 26M OH / KES 80M Revenue)
    # These are the board-approved monthly standard rates used for all pre-dispatch
    # margin projections. Update MONTHLY_STANDARD_OVERHEADS and MONTHLY_STANDARD_REVENUE
    # here when the board revises the benchmarks.
    MONTHLY_STANDARD_OVERHEADS = 26_000_000.0   # KES 26,000,000 per month
    MONTHLY_STANDARD_REVENUE   = 80_000_000.0   # KES 80,000,000 per month
    overhead_ratio = MONTHLY_STANDARD_OVERHEADS / MONTHLY_STANDARD_REVENUE  # = 0.325 (32.5%)
    absorbed_overhead = total_order_value * overhead_ratio

    # 3. Profit Cascades
    gross_profit = total_order_value - total_theoretical_cost
    gross_margin_pct = (gross_profit / total_order_value * 100) if total_order_value > 0 else 0.0
    
    net_profit = gross_profit - absorbed_overhead - approximate_fuel_cost
    net_margin_pct = (net_profit / total_order_value * 100) if total_order_value > 0 else 0.0
    
    profitability_status = "Profitable" if net_profit >= 0 else "Loss"

    # 4. Assign to the document
    # Field mapping per Nexus Load Plan DocType:
    # margin_percentage  → Gross Margin (%) — projected gross
    # daily_overhead_allocated → Absorbed overhead (repurposed field)
    # profit_loss        → Estimated trip net contribution (pre-dispatch)
    # net_margin         → Net Margin % (after overhead + fuel)
    # profitability_status → Profitable / Loss
    plan.total_theoretical_cost = total_theoretical_cost
    plan.daily_overhead_allocated = absorbed_overhead
    plan.margin_percentage = gross_margin_pct          # Projected Gross Margin %
    plan.profit_loss = net_profit                      # Estimated Trip Net Contribution
    plan.net_margin = net_margin_pct                   # Net Margin % after overhead + fuel
    plan.profitability_status = profitability_status
    # ------------------------------------------------------------------

    plan.insert(ignore_permissions=True)

    if action == "soft":
        frappe.db.commit()
        return {"status": "success", "message": f"Load Plan {plan.name} created successfully with optimized routing."}

    # action == "reserve": Create a Draft Inventory Reservation linked to this plan
    res_doc = frappe.new_doc("Nexus Inventory Reservation")
    res_doc.nexus_load_plan = plan.name
    
    expiry_hours = flt(frappe.db.get_single_value("Reservation Settings", "reservation_expiry_hours")) or 24
    res_doc.expiry_date = add_to_date(now_datetime(), hours=expiry_hours)

    for so in group.get("sales_orders", []):
        so_name = so.get("sales_order")
        for item in so.get("items", []):
            res_doc.append("items", {
                "item_code": item.get("item_code"),
                "sales_order": so_name,
                "required_qty": flt(item.get("qty", 0)),
                "reservation_date": now_datetime()
            })

    res_doc.insert(ignore_permissions=True)
    frappe.db.commit()
    
    return {
        "status": "success", 
        "reservation_id": res_doc.name, 
        "message": f"Plan {plan.name} and Draft Reservation {res_doc.name} created."
    }


@frappe.whitelist()
def reanalyze_load_plan(load_plan_name):
    """
    Refreshes the stock readiness of an existing Load Plan.
    """
    plan = frappe.get_doc("Nexus Load Plan", load_plan_name)
    
    # Do not reanalyze if a reservation is already submitted (Docstatus 1)
    existing_res = frappe.db.get_value("Nexus Inventory Reservation", 
        {"nexus_load_plan": plan.name, "docstatus": 1}, "name")
    
    if existing_res:
        return {"status": "exists", "message": "A submitted reservation already exists for this plan."}

    # Clear old drafts
    frappe.db.sql("DELETE FROM `tabNexus Inventory Reservation` WHERE nexus_load_plan = %s AND docstatus = 0", (load_plan_name,))

    # Re-create draft from current Sales Order items
    res_doc = frappe.new_doc("Nexus Inventory Reservation")
    res_doc.nexus_load_plan = plan.name
    
    for so in plan.sales_orders:
        so_doc = frappe.get_doc("Sales Order", so.sales_order)
        for item in so_doc.items:
            res_doc.append("items", {
                "item_code": item.item_code, 
                "sales_order": so.sales_order, 
                "required_qty": item.qty, 
                "reservation_date": now_datetime()
            })
    
    expiry_hours = flt(frappe.db.get_single_value("Reservation Settings", "reservation_expiry_hours")) or 24
    res_doc.expiry_date = add_to_date(now_datetime(), hours=expiry_hours)
    res_doc.insert(ignore_permissions=True)
    
    # Sync visual status for UI
    from nexus_supply_chain.reservation_hooks import sync_load_plan_status
    sync_load_plan_status(plan.name)
    
    frappe.db.commit()
    return {"status": "success", "message": "Reanalysis complete. Fresh stock check applied."}


# ----------------------------------------------------------------------
# 🚨 PHASE 2: Dynamic Margin Analysis for UI Rendering
# ----------------------------------------------------------------------

@frappe.whitelist()
def get_group_margin_data(group, route_geojson=None, vehicle_type=None):
    """
    Receives a load group and dynamic UI parameters to return:
    - total_order_value
    - total_theoretical_cost
    - gross_profit & gross_margin_percentage
    - overhead_ratio & absorbed_overhead
    - approximate fuel metrics
    - net_profit & net_margin_percentage
    """
    if isinstance(group, str):
        group = frappe.parse_json(group)

    sales_orders = group.get("sales_orders", [])
    if not sales_orders:
        return {"error": "No sales orders in this group."}

    # 1. Total order value
    total_order_value = flt(group.get("total_amount", 0))
    if not total_order_value:
        total_order_value = sum(flt(so.get("amount", 0)) for so in sales_orders)

    # 2. Compute total theoretical production cost
    total_theoretical_cost = compute_total_theoretical_cost_for_orders(sales_orders)
    
    # 3. Gross Profit Calculation
    gross_profit = total_order_value - total_theoretical_cost
    gross_margin_pct = (gross_profit / total_order_value * 100) if total_order_value > 0 else 0.0

    # 4. Dynamic Overhead Absorption
    # 4. Fixed Overhead Absorption (Company Standard: KES 26M OH / KES 80M Revenue)
    MONTHLY_STANDARD_OVERHEADS = 26_000_000.0   # KES 26,000,000 per month
    MONTHLY_STANDARD_REVENUE   = 80_000_000.0   # KES 80,000,000 per month
    overhead_ratio_decimal = MONTHLY_STANDARD_OVERHEADS / MONTHLY_STANDARD_REVENUE   # 0.325
    overhead_ratio = overhead_ratio_decimal * 100                                     # 32.5%
    absorbed_overhead = total_order_value * overhead_ratio_decimal

    # 5. Extract Distance and Compute Fuel Metrics
    approximate_total_distance_km = 0.0
    if route_geojson:
        try:
            if isinstance(route_geojson, str):
                route_data = json.loads(route_geojson)
            else:
                route_data = route_geojson
                
            distance_m = flt(route_data.get("features", [{}])[0].get("properties", {}).get("summary", {}).get("distance", 0))
            approximate_total_distance_km = distance_m / 1000.0
        except Exception:
            pass

    approximate_fuel_consumption_ltrs = 0.0
    approximate_fuel_cost = 0.0

    if vehicle_type:
        try:
            vt = frappe.get_doc("Vehicle Type", vehicle_type)
            km_per_ltr = flt(vt.consumption_km_per_ltr)
            litre_cost = flt(vt.litre_cost)
            
            if km_per_ltr > 0:
                approximate_fuel_consumption_ltrs = approximate_total_distance_km / km_per_ltr
                approximate_fuel_cost = approximate_fuel_consumption_ltrs * litre_cost
        except Exception:
            pass

    # 6. Final Net Profit Calculation
    net_profit = gross_profit - absorbed_overhead - approximate_fuel_cost
    net_margin_pct = (net_profit / total_order_value * 100) if total_order_value > 0 else 0.0

    return {
        "total_order_value": total_order_value,
        "total_theoretical_cost": total_theoretical_cost,
        "gross_profit": round(gross_profit, 2),
        "gross_margin_percentage": round(gross_margin_pct, 2),
        "overhead_ratio_percentage": round(overhead_ratio, 2),        # 32.5
        "overhead_standard_revenue": MONTHLY_STANDARD_REVENUE,        # 80,000,000 — shown in UI
        "overhead_standard_overheads": MONTHLY_STANDARD_OVERHEADS,    # 26,000,000 — shown in UI
        "absorbed_overhead": round(absorbed_overhead, 2),
        "approximate_total_distance_km": round(approximate_total_distance_km, 2),
        "approximate_fuel_consumption_ltrs": round(approximate_fuel_consumption_ltrs, 2),
        "approximate_fuel_cost": round(approximate_fuel_cost, 2),
        "net_profit": round(net_profit, 2),
        "net_margin_percentage": round(net_margin_pct, 2),
        "currency": frappe.defaults.get_user_default("currency") or "KES"
    }