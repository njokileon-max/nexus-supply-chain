import frappe
from frappe import _
from frappe.utils import cint, flt, now_datetime

TITLE = _("Nexus Production")


def validate_item(doc, method=None):
    """
    Item save rules for the production flags. Hooked on Item.validate.

      * Is Stainer requires Is BIP (a stainer is a bulk that colours).
      * Is Colourant cannot be a bulk (colourants are concentrates added per unit).
      * Is BIP cannot be ticked on an item counted in a whole-number unit (Nos, Pcs):
        a bulk is kept in kg or litres, and a packed base is recognised from its BOM.
      * Monitor Shelf Level is for finished goods only, and needs MRL >= 0 and
        Maximum Shelf Capacity above MRL.
      * Minimum Production Level cannot be negative.
      * Linked BIP must point to a different item that is ticked Is BIP, and
        only finished goods may carry one.
      * A bulk never carries a route override.
    """
    is_bip = cint(doc.get("custom_is_bip"))
    is_stainer = cint(doc.get("custom_is_stainer"))
    is_colourant = cint(doc.get("custom_is_colourant"))

    if is_stainer and not is_bip:
        frappe.throw(_("Is Stainer can only be ticked on a bulk. Tick Is BIP as well, or untick Is Stainer."), title=TITLE)

    if is_colourant and is_bip:
        frappe.throw(_("An item cannot be both Is BIP and Is Colourant. Bulks that colour other products should be ticked Is BIP + Is Stainer instead."), title=TITLE)

    if is_bip and (doc.is_new() or doc.has_value_changed("custom_is_bip") or doc.has_value_changed("stock_uom")):
        # checked only when Is BIP or the unit changes: an item tagged wrongly before this rule
        # can still be edited, and the board lists it under Needs Setup until it is fixed
        uom = doc.get("stock_uom")
        if uom and cint(frappe.get_cached_value("UOM", uom, "must_be_whole_number")):
            frappe.throw(
                _("Is BIP is for bulk in kg or litres; a packed base is recognised from its BOM. "
                  "{0} is counted in {1}, a whole-number unit. Untick Is BIP here and tick it on "
                  "the bulk inside this item's BOM.").format(frappe.bold(doc.item_code or doc.name), frappe.bold(uom)),
                title=TITLE,
            )

    if flt(doc.get("custom_minimum_production_level")) < 0:
        frappe.throw(_("Minimum Production Level cannot be negative."), title=TITLE)

    if cint(doc.get("custom_monitor_shelf_level")):
        if is_bip or is_colourant:
            frappe.throw(_("Monitor Shelf Level is for finished goods only. Untick it on bulks and colourants."), title=TITLE)
        mrl = flt(doc.get("custom_minimum_reorder_level"))
        max_shelf = flt(doc.get("custom_maximum_shelf_capacity"))
        if mrl < 0:
            frappe.throw(_("Minimum Reorder Level cannot be negative."), title=TITLE)
        if max_shelf <= mrl:
            frappe.throw(
                _("Maximum Shelf Capacity ({0}) must be greater than Minimum Reorder Level ({1}).").format(max_shelf, mrl),
                title=TITLE,
            )

    linked = doc.get("custom_linked_bip")
    if linked:
        if is_bip or is_colourant:
            frappe.throw(_("Linked BIP is only used on finished goods. Clear it on bulks and colourants."), title=TITLE)
        if linked == doc.name:
            frappe.throw(_("An item cannot be its own Linked BIP."), title=TITLE)
        if not cint(frappe.db.get_value("Item", linked, "custom_is_bip")):
            frappe.throw(_("Linked BIP {0} is not ticked Is BIP.").format(frappe.bold(linked)), title=TITLE)

    if is_bip and doc.get("custom_production_route_override"):
        doc.custom_production_route_override = None


def stamp_confirmed_on(doc, method=None):
    """
    Sales Order before_submit: record when the order was confirmed. In your
    workflow the submit IS the "Order Confirmed" transition. The field is
    no_copy, so an amended order gets its own stamp.
    """
    if not doc.get("custom_confirmed_on"):
        doc.custom_confirmed_on = now_datetime()
