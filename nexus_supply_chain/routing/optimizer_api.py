import frappe
from frappe import _
from frappe.utils import flt  # <-- Added global import here

@frappe.whitelist()
def get_vehicle_routing_economics(vehicle_type=None, truck_number=None):
    """
    Fetches vehicle specifications and fuel economics.
    Prioritizes vehicle_type if provided, otherwise looks up the vehicle_type from the associated truck_number.
    """
    if not vehicle_type and not truck_number:
        frappe.throw(_("Please provide either a Vehicle Type or a Truck Number"))

    # Resolve vehicle_type from truck_number if the frontend only knows the specific truck
    if not vehicle_type and truck_number:
        # Check standard Vehicle doctype by license_plate
        vehicle = frappe.db.get_value(
            "Vehicle", 
            {"license_plate": truck_number}, 
            "vehicle_type"
        )
        
        # Fallback to checking by document name (if license_plate isn't the primary key used)
        if not vehicle:
            vehicle = frappe.db.get_value("Vehicle", truck_number, "vehicle_type")
        
        if vehicle:
            vehicle_type = vehicle
        else:
            frappe.throw(_("Could not find a registered Vehicle Type for Truck Number: {0}").format(truck_number))

    if not vehicle_type:
        frappe.throw(_("Vehicle Type could not be resolved."))

    # Fetch the economics from the standard Vehicle Type doctype
    vt_data = frappe.db.get_value(
        "Vehicle Type", 
        vehicle_type, 
        ["name", "max_tonnage", "fuel_type", "litre_cost", "consumption_km_per_ltr"], 
        as_dict=True
    )

    if not vt_data:
        frappe.throw(_("Vehicle Type '{0}' not found in the system.").format(vehicle_type))

    return {
        "vehicle_type": vt_data.name,
        "max_tonnage": flt(vt_data.max_tonnage),
        "fuel_type": vt_data.fuel_type or "Unknown",
        "litre_cost": flt(vt_data.litre_cost),
        "consumption_km_per_ltr": flt(vt_data.consumption_km_per_ltr)
    }


@frappe.whitelist()
def calculate_trip_fuel_cost(distance_km, vehicle_type=None, truck_number=None):
    """
    Computes the exact theoretical fuel cost given a distance input, 
    strictly utilizing the standard Vehicle Type doctype parameters.
    """
    try:
        distance_km = flt(distance_km)
    except (ValueError, TypeError):
        frappe.throw(_("Distance must be a valid numerical value."))

    # Extract global domain economics parameters
    economics = get_vehicle_routing_economics(vehicle_type=vehicle_type, truck_number=truck_number)

    consumption = economics.get("consumption_km_per_ltr")
    litre_cost = economics.get("litre_cost")

    # Safety checks to prevent ZeroDivisionError or bad math
    if not consumption or consumption <= 0:
        frappe.throw(_("Fuel consumption (Km/L) is not configured correctly for Vehicle Type '{0}'. Please update the Master Data.").format(economics.get("vehicle_type")))
    
    if not litre_cost or litre_cost <= 0:
        frappe.msgprint(_("Warning: Litre Cost for Vehicle Type '{0}' is zero or not set. Computed cost will be 0.").format(economics.get("vehicle_type")), alert=True)

    # Perform the economic calculation
    total_litres = distance_km / consumption
    total_cost = total_litres * litre_cost

    return {
        "status": "success",
        "distance_km": distance_km,
        "total_litres_required": round(total_litres, 2),
        "estimated_fuel_cost": round(total_cost, 2),
        "currency": frappe.defaults.get_global_default("default_currency") or "KES",
        "economics_used": economics
    }