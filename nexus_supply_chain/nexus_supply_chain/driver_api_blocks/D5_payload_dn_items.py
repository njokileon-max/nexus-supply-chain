	dn_names = tuple({s.get("delivery_note_id") for s in stops if s.get("delivery_note_id")})
	dn_items = {}
	if dn_names:
		for it in frappe.db.sql("""
			SELECT parent, item_code, MAX(item_name) AS item_name, SUM(qty) AS qty, MAX(uom) AS uom, MIN(idx) AS first_idx
			FROM `tabDelivery Note Item`
			WHERE parent IN %(dns)s
			GROUP BY parent, item_code
			ORDER BY parent, first_idx
		""", {"dns": dn_names}, as_dict=True):
			dn_items.setdefault(it.parent, []).append({
				"item_code": it.item_code, "item_name": it.item_name,
				"qty": flt(it.qty), "max_qty": flt(it.qty), "uom": it.uom,
			})

