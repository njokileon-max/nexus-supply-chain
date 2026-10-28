// Copyright (c) 2026, Crystal Adhesives Ltd

const NEXUS_RELINK_METHOD = "nexus_supply_chain.production.bom_sync.replace_outdated_sub_bom_links";
const NEXUS_PROD_API = "nexus_supply_chain.production.api";

frappe.ui.form.on("Nexus Production Settings", {
	refresh(frm) {
		const leaf = () => ({ filters: { is_group: 0 } });
		// Table MultiSelect fields take their query on the parent fieldname.
		["fg_warehouse", "colourant_warehouse", "cook_target_warehouse", "fill_target_warehouse",
		 "bulk_warehouses", "rm_warehouses"].forEach((f) => frm.set_query(f, leaf));

		frm.add_custom_button(__("BOM Update Logs"), () => frappe.set_route("List", "BOM Update Log"), __("View"));
	},

	test_connection(frm) {
		if (frm.is_dirty()) {
			frappe.msgprint(__("Save the settings first, then test the connection."));
			return;
		}
		frappe.call({
			method: `${NEXUS_PROD_API}.test_connection`,
			freeze: true,
			freeze_message: __("Contacting the engine..."),
		}).then((r) => {
			const m = r.message || {};
			frappe.msgprint({
				title: m.ok ? __("Connected") : __("Connection failed"),
				message: frappe.utils.escape_html(m.message || "")
					+ (m.secret_found ? "" : `<br><br>${__("No secret found in site_config.json (crystal_api_internal_secret).")}`),
				indicator: m.ok ? "green" : "red",
			});
		});
	},

	preview_board(frm) {
		if (frm.is_dirty()) {
			frappe.msgprint(__("Save the settings first, then preview the board."));
			return;
		}
		frappe.prompt(
			{
				fieldname: "mode", fieldtype: "Select", label: __("Orders"),
				options: [
					{ value: "confirmed", label: __("Confirmed orders") },
					{ value: "drafts", label: __("Confirmed + drafts") },
				],
				default: "confirmed",
			},
			(v) => {
				frappe.call({
					method: `${NEXUS_PROD_API}.preview_board`,
					args: { mode: v.mode },
					freeze: true,
					freeze_message: __("Calculating the board from today's data..."),
				}).then((r) => nexus_show_board_preview(r.message || {}));
			},
			__("Preview board"),
			__("Calculate")
		);
	},

	download_snapshot(frm) {
		frappe.prompt(
			{
				fieldname: "mode", fieldtype: "Select", label: __("Orders"),
				options: [
					{ value: "confirmed", label: __("Confirmed orders") },
					{ value: "drafts", label: __("Confirmed + drafts") },
				],
				default: "confirmed",
			},
			(v) => {
				window.open(`/api/method/${NEXUS_PROD_API}.download_snapshot?mode=${encodeURIComponent(v.mode)}`);
			},
			__("Download snapshot"),
			__("Download")
		);
	},

	replace_outdated_links(frm) {
		frappe.call({
			method: NEXUS_RELINK_METHOD,
			args: { dry_run: 1, include_backwards: 0 },
			freeze: true,
			freeze_message: __("Checking every BOM..."),
		}).then((r) => nexus_show_relink_preview(r.message || {}));
	},
});

function nexus_show_relink_preview(s) {
	if (!s.total_pairs) {
		frappe.msgprint({ title: __("Sub-BOM links"), message: __("Every parent BOM already points at the current default BOM."), indicator: "green" });
		return;
	}

	const esc = frappe.utils.escape_html;
	const row = (p) =>
		`<tr><td>${esc(p.item_code)}</td><td>${esc(p.old_bom)}</td><td>${esc(p.new_bom)}</td><td style="text-align:right">${p.parents}</td></tr>`;
	const table = (list) =>
		`<div style="max-height:260px;overflow:auto;border:1px solid var(--border-color);border-radius:6px">
		 <table class="table table-condensed" style="margin:0">
		 <thead><tr><th>${__("Component")}</th><th>${__("Old BOM")}</th><th>${__("Current default")}</th><th style="text-align:right">${__("Parents")}</th></tr></thead>
		 <tbody>${list.map(row).join("")}</tbody></table></div>`;

	const backwards = s.backwards_pairs || [];
	const d = new frappe.ui.Dialog({
		title: __("Replace outdated sub-BOM links"),
		size: "large",
		fields: [
			{
				fieldtype: "HTML",
				options: `<p>${__("{0} links in {1} parent BOMs point at an older version of a component.", [s.selected_pairs, s.parent_boms_affected])}
					${__("Each replacement is queued through ERPNext's BOM Update Tool and also refreshes the parent BOM's cost.")}</p>
					${table(s.pairs || [])}`,
			},
			{
				fieldtype: "HTML",
				options: backwards.length
					? `<p style="margin-top:14px"><b>${__("{0} backwards pair(s) skipped", [backwards.length])}</b>:
						${__("parents use a NEWER version than the current default. Decide which version is right and set it as default first.")}</p>
						${table(backwards)}`
					: "",
			},
			{
				fieldname: "include_backwards",
				fieldtype: "Check",
				label: __("Also replace the backwards pairs (parents move to the older default)"),
				hidden: backwards.length ? 0 : 1,
			},
		],
		primary_action_label: __("Queue replacements"),
		primary_action(values) {
			frappe.call({
				method: NEXUS_RELINK_METHOD,
				type: "POST",
				args: { dry_run: 0, include_backwards: values.include_backwards ? 1 : 0 },
				freeze: true,
			}).then((r) => {
				d.hide();
				const q = (r.message || {}).queued || 0;
				frappe.msgprint({
					title: __("Queued"),
					message: __("{0} replacement(s) queued. Follow progress in BOM Update Log.", [q]),
					indicator: "green",
				});
			});
		},
	});
	d.show();
}

function nexus_show_board_preview(p) {
	const esc = frappe.utils.escape_html;
	const num = (v, d = 0) => (v === null || v === undefined ? "" : format_number(v, null, d));
	const s = p.summary || {};
	const meta = p.meta || {};
	const snap = meta.snapshot || {};
	const pc = s.priority_counts || {};
	const pn = p.priority_names || {};
	const box = (inner) =>
		`<div style="max-height:260px;overflow:auto;border:1px solid var(--border-color);border-radius:6px;margin-bottom:14px">${inner}</div>`;
	const tbl = (head, rows) =>
		box(`<table class="table table-condensed" style="margin:0"><thead><tr>${head.map((h) => `<th>${h}</th>`).join("")}</tr></thead>
		<tbody>${rows.join("") || `<tr><td colspan="${head.length}" class="text-muted">${__("None")}</td></tr>`}</tbody></table>`);

	const banner = meta.stale
		? `<div class="alert alert-warning">${__("The engine failed, so this is the last good board from {0}.", [esc(meta.calculated_at || "")])}<br>${esc(meta.error || "")}</div>`
		: "";

	const facts = [
		[__("Calculated at"), esc(meta.calculated_at || "")],
		[__("Engine"), `${esc(meta.engine_version || "")} &middot; ${num(meta.timing_ms)} ms`],
		[__("Snapshot"), __("{0} items, {1} BOMs, {2} orders ({3} lines), {4} drafts, {5} work orders",
			[snap.items, snap.boms, snap.orders, snap.order_lines, snap.drafts, snap.work_orders])],
		[__("Kg to cook"), num(s.kg_to_cook)],
		[__("Units to fill / tint"), num(s.units_to_fill)],
		[__("Cards"), __("{0} bulk, {1} stainer, {2} fill, {3} tint", [s.bulk_cards, s.stainer_cards, s.fill_cards, s.tint_cards])],
		[__("Priorities"), Object.keys(pn).map((k) => `${esc(pn[k])}: <b>${pc["P" + k] || 0}</b>`).join(" &middot; ")],
		[__("Materials short"), __("{0}, worth KES {1}", [s.materials_short, num(s.shortage_value)])],
		[__("Orders counted"), __("{0} confirmed, {1} ready from stock", [s.orders_counted, s.orders_ready_from_stock])],
		[__("Left out (too old)"), __("{0} orders, {1} work orders", [p.older_orders, p.older_work_orders])],
	];

	const html =
		banner +
		tbl([__("What"), __("Value")], facts.map(([k, v]) => `<tr><td style="width:190px">${k}</td><td>${v}</td></tr>`)) +
		`<h5>${__("Needs Setup")}</h5>` +
		tbl([__("Check"), __("Items"), __("In today's plan")], (p.needs_setup || []).map((r) =>
			`<tr><td><span class="indicator-pill ${r.severity === "error" ? "red" : r.severity === "warning" ? "orange" : "gray"}">${esc(r.severity)}</span> ${esc(r.title)}</td>
			 <td style="text-align:right">${r.count}</td><td style="text-align:right">${r.in_plan}</td></tr>`)) +
		`<h5>${__("Top 10 cards")}</h5>` +
		tbl([__("Priority"), __("Item"), __("Route"), __("Group"), __("Make"), __("Short")], (p.top_cards || []).map((c) =>
			`<tr><td>P${c.priority}</td><td><b>${esc(c.item_code)}</b><br><span class="text-muted">${esc(c.item_name || "")}</span></td>
			 <td>${esc(c.route || "")}</td><td>${esc(c.group || "")}</td>
			 <td style="text-align:right">${num(c.make, 1)} ${esc(c.uom || "")}</td><td style="text-align:right">${num(c.short, 1)}</td></tr>`)) +
		`<h5>${__("Top 10 materials short")}</h5>` +
		tbl([__("Material"), __("Kind"), __("Short"), __("Value (KES)")], (p.top_materials || []).map((m) =>
			`<tr><td><b>${esc(m.item_code)}</b><br><span class="text-muted">${esc(m.item_name || "")}</span></td><td>${esc(m.kind || "")}</td>
			 <td style="text-align:right">${num(m.short, 2)} ${esc(m.uom || "")}</td><td style="text-align:right">${num(m.shortage_value)}</td></tr>`));

	const d = new frappe.ui.Dialog({ title: __("Production board preview"), size: "extra-large", fields: [{ fieldtype: "HTML", options: html }] });
	d.show();
}
