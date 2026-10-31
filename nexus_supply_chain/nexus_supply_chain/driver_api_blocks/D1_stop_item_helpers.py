def _delivery_note_items(delivery_note):
	"""item_code -> {item_code, item_name, qty, uom} from a Delivery Note: what was
	actually loaded for the stop (Batch 12). Same shape as _sales_order_items."""
	items = OrderedDict()
	if not delivery_note:
		return items
	for r in frappe.db.sql("""
		SELECT item_code, MAX(item_name) AS item_name, SUM(qty) AS qty, MAX(uom) AS uom, MIN(idx) AS first_idx
		FROM `tabDelivery Note Item`
		WHERE parent = %s
		GROUP BY item_code
		ORDER BY first_idx
	""", (delivery_note,), as_dict=True):
		items[r.item_code] = {"item_code": r.item_code, "item_name": r.item_name, "qty": flt(r.qty), "uom": r.uom}
	return items


def _stop_delivery_note(stop_name):
	"""The stop's Delivery Note (Batch 12 field), or None on an older stop / before migrate."""
	if not stop_name or not frappe.db.has_column(STOP, "delivery_note_id"):
		return None
	return frappe.db.get_value(STOP, stop_name, "delivery_note_id")


def _stop_items(sales_order, stop_name=None):
	"""Lines a stop delivers: the Delivery Note's when the stop has one (SO 20 units,
	DN 15 -> 15), otherwise the Sales Order's (stops made before Batch 12)."""
	dn = _stop_delivery_note(stop_name)
	if dn:
		items = _delivery_note_items(dn)
		if items:
			return items
	return _sales_order_items(sales_order)


