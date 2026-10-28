// apps/nexus_supply_chain/nexus_supply_chain/nexus_supply_chain/doctype/vehicle_delivery_manifest/vehicle_delivery_manifest.js
//
// Desk form for Vehicle Delivery Manifest (office use).
// - Trip and stop actions call nexus_supply_chain.driver_api, the same
//   checked code path the driver app uses. Nothing here sets trip_status.
// - The form reloads itself when the trip changes elsewhere (driver app,
//   another desk user), through the "nexus_manifest_update" realtime event.
// - The map is LAZY: nothing map-related downloads until someone presses
//   "Show map". It uses the shared NexusMaps adapter (Google or Hosted, per
//   Nexus App Settings and the user's last choice), shows the planned route,
//   numbered stops by status and, for confirmed stops, the drop-off point
//   against the customer's pin with the distance between them.
// - Driver-side GPS, wake locks and navigation live in the driver app, not here.

const NEXUS_DRIVER_API = "nexus_supply_chain.driver_api";
const NEXUS_MAP_ASSETS = [
	"/assets/nexus_supply_chain/js/nexus_gmaps_loader.js",
	"/assets/nexus_supply_chain/js/nexus_map_adapter.js",
];
/**
 * Loads the shared Nexus scripts once each, stamped with the deployed asset
 * version (production caches /assets for a year; the stamp changes on every
 * deploy). Shared by every page through window.__nexusScripts.
 */
function nexus_load_scripts(urls) {
	const version = (frappe.boot.nexus_maps && frappe.boot.nexus_maps.asset_version) || "1";
	const loaded = (window.__nexusScripts = window.__nexusScripts || {});
	return urls.reduce(
		(chain, url) =>
			chain.then(() => {
				if (!loaded[url]) {
					loaded[url] = new Promise((resolve, reject) => {
						const s = document.createElement("script");
						s.src = `${url}?v=${encodeURIComponent(version)}`;
						s.async = true;
						s.onload = () => resolve();
						s.onerror = () => {
							delete loaded[url];
							reject(new Error(`Could not load ${url}`));
						};
						document.head.appendChild(s);
					});
				}
				return loaded[url];
			}),
		Promise.resolve(),
	);
}

const NEXUS_STATUS_COLORS = {
	Pending: "#2563eb",
	Delivered: "#16a34a",
	"Partially Delivered": "#d97706",
	Failed: "#dc2626",
	Returned: "#6b7280",
};

frappe.ui.form.on("Vehicle Delivery Manifest", {
	setup() {
		nexus_bind_manifest_realtime();
	},

	refresh(frm) {
		nexus_render_map_section(frm);
		nexus_show_trip_summary(frm);
		if (frm.doc.docstatus === 1) {
			nexus_add_trip_buttons(frm);
		}
	},
});

// ---------------------------------------------------------------- realtime
function nexus_bind_manifest_realtime() {
	if (window.__nexus_manifest_realtime_bound) return;
	window.__nexus_manifest_realtime_bound = true;

	frappe.realtime.on("nexus_manifest_update", (data) => {
		const frm = window.cur_frm;
		if (!frm || frm.doctype !== "Vehicle Delivery Manifest" || frm.is_new()) return;
		if (!data || !(data.manifests || []).includes(frm.doc.name)) return;

		if (frm.is_dirty()) {
			frappe.show_alert({ message: __("This trip was updated elsewhere. Reload to see the latest."), indicator: "orange" });
			return;
		}
		frm.reload_doc();
	});
}

// ------------------------------------------------------------- server calls
function nexus_driver_call(frm, method, args, busy_message) {
	return frappe
		.call({
			method: `${NEXUS_DRIVER_API}.${method}`,
			args: args,
			freeze: true,
			freeze_message: busy_message || __("Saving..."),
		})
		.then((r) => {
			const res = r.message || {};
			if (res.status === "success") {
				frappe.show_alert({ message: res.message || __("Saved."), indicator: "green" });
				frm.reload_doc();
				return res;
			}
			frappe.msgprint({ title: __("Not saved"), message: res.message || __("Something went wrong."), indicator: "red" });
			return Promise.reject(res);
		});
}

function nexus_request_id() {
	return `desk-${frappe.session.user}-${Date.now()}-${Math.random().toString(36).slice(2, 10)}`;
}

// ---------------------------------------------------------------- summary
function nexus_show_trip_summary(frm) {
	if (frm.doc.docstatus !== 1) return;
	const stops = frm.doc.stops || [];
	const done = stops.filter((s) => s.delivery_status && s.delivery_status !== "Pending").length;
	const on_site = stops.filter((s) => s.is_on_site_dropoff).length;
	const parts = [__("Trip: {0}", [frm.doc.trip_status || "Manifested"]), __("{0} of {1} stops done", [done, stops.length])];
	if (done) parts.push(__("{0} on site", [on_site]));
	const collected = (frm.doc.return_collections || []).filter((r) => (r.back_office_status || "Pending") === "Pending").length;
	if (collected) parts.push(__("{0} earlier-invoice return(s) to process", [collected]));
	if (frm.doc.trip_started_at) parts.push(__("Started {0}", [frappe.datetime.str_to_user(frm.doc.trip_started_at)]));
	if (frm.doc.trip_completed_at) parts.push(__("Completed {0}", [frappe.datetime.str_to_user(frm.doc.trip_completed_at)]));

	const color = { Ready: "blue", Dispatched: "orange", Returning: "yellow", Completed: "green", Cancelled: "red" }[frm.doc.trip_status] || "gray";
	frm.dashboard.set_headline_alert(frappe.utils.escape_html(parts.join(" · ")), color);
}

// ---------------------------------------------------------------- buttons
function nexus_add_trip_buttons(frm) {
	const status = frm.doc.trip_status;
	const can_dispatch = frappe.perm.has_perm("Vehicle Delivery Manifest", 0, "submit");
	const pending = (frm.doc.stops || []).filter((s) => (s.delivery_status || "Pending") === "Pending");

	if (["Ready", "Dispatched", "Returning", "Completed"].includes(status)) {
		frm.add_custom_button(__("Open Live Dispatch"), () => frappe.set_route("nexus_live_dispatch"));
	}
	if (!can_dispatch) return;

	if (status === "Ready") {
		frm.add_custom_button(__("Start Trip"), () => {
			frappe.confirm(__("Record the departure from the yard for <b>{0}</b>?", [frm.doc.name]), () =>
				nexus_driver_call(frm, "set_trip_status_from_desk", {
					manifest: frm.doc.name, status: "Dispatched", client_request_id: nexus_request_id(),
				}, __("Starting trip..."))
			);
		}, __("Trip"));
	}

	if (status === "Dispatched") {
		frm.add_custom_button(__("Mark Returning"), () => {
			const msg = pending.length
				? __("{0} stop(s) are still pending. Mark the truck as heading back anyway?", [pending.length])
				: __("Mark the truck as heading back to the yard?");
			frappe.confirm(msg, () =>
				nexus_driver_call(frm, "set_trip_status_from_desk", {
					manifest: frm.doc.name, status: "Returning", client_request_id: nexus_request_id(),
				})
			);
		}, __("Trip"));
	}

	if (status === "Dispatched" || status === "Returning") {
		frm.add_custom_button(__("Complete Trip"), () => {
			const msg = pending.length
				? __("{0} stop(s) are still pending and will be marked <b>Returned</b>. Complete the trip and set the vehicle to Idle?", [pending.length])
				: __("Confirm the truck is back at the yard? The vehicle becomes Idle.");
			frappe.confirm(msg, () =>
				nexus_driver_call(frm, "set_trip_status_from_desk", {
					manifest: frm.doc.name, status: "Completed", client_request_id: nexus_request_id(),
				}, __("Completing trip..."))
			);
		}, __("Trip"));

		if (pending.length) {
			frm.add_custom_button(__("Confirm a Stop"), () => nexus_confirm_stop_dialog(frm, pending)).addClass("btn-primary");
		}
	}
}

// ------------------------------------------------------- confirm a stop
function nexus_confirm_stop_dialog(frm, pending) {
	const request_id = nexus_request_id();
	let loaded = { stop: null, items: [], reasons: [] };

	const d = new frappe.ui.Dialog({
		title: __("Confirm Drop-off"),
		fields: [
			{
				label: __("Stop"), fieldname: "stop_row", fieldtype: "Select", reqd: 1,
				options: pending.map((s) => ({
					label: `${s.idx}. ${s.customer || s.customer_id || s.sales_order} · ${format_currency(s.grand_total || 0)}`,
					value: s.name,
				})),
				onchange: () => nexus_toggle_dialog(d),
			},
			{
				label: __("Result"), fieldname: "delivery_status", fieldtype: "Select", reqd: 1,
				options: "Delivered\nPartially Delivered\nFailed", default: "Delivered",
				onchange: () => nexus_toggle_dialog(d),
			},
			{ label: __("Reason"), fieldname: "return_reason", fieldtype: "Select", options: "", hidden: 1 },
			{ fieldname: "returned_items_html", fieldtype: "HTML", hidden: 1 },
			{ fieldtype: "Section Break", label: __("Goods from an earlier invoice (optional)") },
			{
				label: __("Earlier invoice number(s)"), fieldname: "return_invoices", fieldtype: "Small Text",
				description: __("One per line or comma-separated. Recorded only: the back office processes the return under Collected Returns (Earlier Invoices). Separate from today's delivery."),
			},
			{ fieldtype: "Section Break" },
			{ label: __("Notes"), fieldname: "notes", fieldtype: "Small Text" },
			{
				fieldname: "office_note", fieldtype: "HTML",
				options: `<div class="text-muted small">${__("Confirmed from the office: no GPS point is recorded for this drop-off.")}</div>`,
			},
		],
		primary_action_label: __("Confirm"),
		primary_action(values) {
			const returned = [];
			if (values.delivery_status === "Partially Delivered") {
				let invalid = null;
				d.fields_dict.returned_items_html.$wrapper.find("tr[data-item]").each(function () {
					const $row = $(this);
					if (!$row.find(".nexus-ret-check").is(":checked")) return;
					const max = parseFloat($row.attr("data-max"));
					const qty = parseFloat($row.find(".nexus-ret-qty").val());
					if (!(qty > 0) || qty > max) {
						invalid = __("Enter a quantity between 0 and {0} for {1}.", [max, $row.attr("data-item")]);
						return false;
					}
					returned.push({ item_code: $row.attr("data-item"), qty_returned: qty });
				});
				if (invalid) return frappe.msgprint(invalid);
				if (!returned.length) return frappe.msgprint(__("Select at least one returned item."));
			}
			if (values.delivery_status !== "Delivered" && !values.return_reason) {
				return frappe.msgprint(__("Choose the reason."));
			}

			const invoices = String(values.return_invoices || "")
				.split(/[\n,]+/)
				.map((v) => v.trim())
				.filter(Boolean);
			const stop = pending.find((s) => s.name === values.stop_row) || {};
			const invoice_note = invoices.length
				? "<br>" + __("Earlier-invoice returns to record: {0}", [frappe.utils.escape_html(invoices.join(", "))])
				: "";
			frappe.confirm(
				__("Record <b>{0}</b> for <b>{1}</b>? This can't be undone.", [
					values.delivery_status, frappe.utils.escape_html(stop.customer || stop.sales_order || ""),
				]) + invoice_note,
				() => {
					d.get_primary_btn().prop("disabled", true);
					nexus_driver_call(frm, "confirm_stop_from_desk", {
						manifest: frm.doc.name,
						stop_row: values.stop_row,
						delivery_status: values.delivery_status,
						returned_items: JSON.stringify(returned),
						return_reason: values.delivery_status === "Delivered" ? "" : values.return_reason,
						notes: values.notes || "",
						return_invoices: JSON.stringify(invoices),
						client_request_id: request_id,
					}, __("Confirming drop-off..."))
						.then(() => d.hide())
						.catch(() => d.get_primary_btn().prop("disabled", false));
				}
			);
		},
	});

	function nexus_toggle_dialog(dialog) {
		const status = dialog.get_value("delivery_status");
		const needs_reason = status === "Partially Delivered" || status === "Failed";
		const is_partial = status === "Partially Delivered";
		dialog.set_df_property("return_reason", "hidden", !needs_reason);
		dialog.set_df_property("return_reason", "reqd", needs_reason);
		dialog.set_df_property("returned_items_html", "hidden", !is_partial);
		if (needs_reason && dialog.get_value("stop_row")) nexus_load_stop(dialog);
	}

	/** Order lines AND the server's list of return reasons (the same list the driver app uses). */
	function nexus_load_stop(dialog) {
		const stop_row = dialog.get_value("stop_row");
		const $wrap = dialog.fields_dict.returned_items_html.$wrapper;
		if (loaded.stop === stop_row) {
			nexus_render_items($wrap);
			return;
		}
		$wrap.html(`<div class="text-muted small">${__("Loading order lines...")}</div>`);
		frappe
			.call({
				method: `${NEXUS_DRIVER_API}.get_stop_items`,
				args: { manifest: frm.doc.name, stop_row: stop_row },
			})
			.then((r) => {
				const res = r.message || {};
				if (res.status !== "success") {
					$wrap.html(`<div class="text-danger small">${frappe.utils.escape_html(res.message || __("Could not load the order lines."))}</div>`);
					return;
				}
				loaded = { stop: stop_row, items: res.items || [], reasons: res.reasons || [] };
				const current = dialog.get_value("return_reason");
				dialog.set_df_property("return_reason", "options", ["", ...loaded.reasons].join("\n"));
				if (current && loaded.reasons.includes(current)) dialog.set_value("return_reason", current);
				nexus_render_items($wrap);
			});
	}

	function nexus_render_items($wrap) {
		const rows = loaded.items
			.map((it) => {
				const code = frappe.utils.escape_html(it.item_code);
				const max = it.max_qty ?? it.qty;
				return `<tr data-item="${code}" data-max="${max}">
					<td class="text-center"><input type="checkbox" class="nexus-ret-check"></td>
					<td><b>${code}</b><br><span class="text-muted">${frappe.utils.escape_html(it.item_name || "")}</span></td>
					<td class="text-right">${max}</td>
					<td><input type="number" class="form-control input-xs nexus-ret-qty" min="0" max="${max}" step="any" disabled></td>
				</tr>`;
			})
			.join("");
		$wrap.html(`<table class="table table-bordered table-sm small">
			<thead><tr><th>${__("Return")}</th><th>${__("Item")}</th><th class="text-right">${__("Ordered")}</th><th>${__("Qty Returned")}</th></tr></thead>
			<tbody>${rows}</tbody></table>`);
		$wrap.find(".nexus-ret-check").on("change", function () {
			const $qty = $(this).closest("tr").find(".nexus-ret-qty");
			$qty.prop("disabled", !this.checked).val(this.checked ? 1 : "");
		});
	}

	d.show();
}

// ------------------------------------------------------------------ map
function nexus_stop_point(stop) {
	const text = (stop.combined_coordinates || "").trim();
	if (text.includes(",")) {
		const [lat, lng] = text.split(",").map((v) => parseFloat(v));
		if (isFinite(lat) && isFinite(lng) && !(lat === 0 && lng === 0)) return [lat, lng];
	}
	const lat = parseFloat(stop.latitude);
	const lng = parseFloat(stop.longitude);
	return isFinite(lat) && isFinite(lng) && !(lat === 0 && lng === 0) ? [lat, lng] : null;
}

function nexus_dropoff_point(stop) {
	const lat = parseFloat(stop.dropoff_latitude);
	const lng = parseFloat(stop.dropoff_longitude);
	return isFinite(lat) && isFinite(lng) && !(lat === 0 && lng === 0) ? [lat, lng] : null;
}

/** "Show map" placeholder; the map itself loads only on demand (and stays open for this form until it is closed). */
function nexus_render_map_section(frm) {
	const field = frm.get_field("route_map_html");
	if (!field) return;
	const stops = frm.doc.stops || [];
	const located = stops.filter((s) => nexus_stop_point(s)).length;
	const has_route = !!frm.doc.route_geojson;

	if (frm.__nexus_map_view) {
		try {
			frm.__nexus_map_view.destroy();
		} catch (e) {
			/* already gone */
		}
		frm.__nexus_map_view = null;
	}

	if (!has_route && !located) {
		field.$wrapper.html(`<div class="text-muted small" style="margin-top:10px">${__("No planned route or stop locations yet.")}</div>`);
		return;
	}

	if (frm.__nexus_map_open === frm.doc.name) {
		nexus_open_map(frm);
		return;
	}

	const parts = [];
	if (has_route) parts.push(__("planned route"));
	parts.push(__("{0} of {1} stops located", [located, stops.length]));
	field.$wrapper.html(`
		<div class="nexus-map-placeholder" style="margin-top:10px;border:1px dashed var(--border-color);border-radius:8px;padding:14px;display:flex;align-items:center;justify-content:space-between;gap:10px">
			<div class="text-muted small"><i class="fa fa-map-o"></i> ${frappe.utils.escape_html(parts.join(" · "))}</div>
			<button class="btn btn-default btn-sm nexus-show-map"><i class="fa fa-map"></i> ${__("Show map")}</button>
		</div>`);
	field.$wrapper.find(".nexus-show-map").on("click", () => {
		frm.__nexus_map_open = frm.doc.name;
		nexus_open_map(frm);
	});
}

function nexus_open_map(frm) {
	const field = frm.get_field("route_map_html");
	const map_id = `nexus-manifest-map-${frappe.utils.get_random(8)}`;
	field.$wrapper.html(`
		<div style="margin-top:10px">
			<div style="display:flex;justify-content:flex-end;margin-bottom:6px">
				<button class="btn btn-xs btn-default nexus-hide-map"><i class="fa fa-times"></i> ${__("Hide map")}</button>
			</div>
			<div id="${map_id}" style="height:440px;border-radius:8px;border:1px solid var(--border-color);position:relative;overflow:hidden">
				<div class="text-muted small" style="padding:20px"><i class="fa fa-spinner fa-spin"></i> ${__("Loading map...")}</div>
			</div>
			<div class="text-muted small nexus-map-legend" style="margin-top:6px"></div>
		</div>`);
	field.$wrapper.find(".nexus-hide-map").on("click", () => {
		frm.__nexus_map_open = null;
		nexus_render_map_section(frm);
	});

	nexus_load_scripts(NEXUS_MAP_ASSETS).then(async () => {
		const el = document.getElementById(map_id);
		if (!el) return; // the form moved on
		el.innerHTML = "";
		let view;
		try {
			view = await NexusMaps.createView(el, { center: [-1.2921, 36.8219], zoom: 11 });
		} catch (e) {
			console.error(e);
			el.innerHTML = `<div class="text-muted small" style="padding:20px">${__("The map could not load. Check Nexus App Settings → Dispatch Map.")}</div>`;
			return;
		}
		if (!document.body.contains(el)) {
			view.destroy();
			return;
		}
		frm.__nexus_map_view = view;
		view.onMapClick(() => view.closePopup());
		nexus_draw_manifest(frm, view, field);
	}).catch((e) => {
		console.error(e);
		const el = document.getElementById(map_id);
		if (el) el.innerHTML = `<div class="text-muted small" style="padding:20px">${__("Couldn't load the map scripts. Run bench build and reload.")}</div>`;
	});
}

function nexus_draw_manifest(frm, view, field) {
	const esc = (v) => frappe.utils.escape_html(v == null ? "" : String(v));
	const points = [];

	NexusMaps.geojsonLines(frm.doc.route_geojson).forEach((line, i) => {
		view.setLine(`planned:${i}`, line, { color: "#2563eb", width: 5, opacity: 0.8, zIndex: 1 });
		line.forEach((p) => points.push(p));
	});

	let dropoffs = 0;
	(frm.doc.stops || []).forEach((stop) => {
		const pin = nexus_stop_point(stop);
		const status = stop.delivery_status || "Pending";
		const color = NEXUS_STATUS_COLORS[status] || NEXUS_STATUS_COLORS.Pending;
		const drop = status !== "Pending" ? nexus_dropoff_point(stop) : null;
		const distance = stop.dropoff_distance_from_target_m != null ? Math.round(stop.dropoff_distance_from_target_m) : null;
		const distance_text = distance != null ? NexusMaps.formatDistance(distance) : null;

		const popup = () => {
			const lines = [
				`<div style="font-weight:800;font-size:14px">${stop.idx}. ${esc(stop.customer || stop.customer_id || "")}</div>`,
				esc(stop.sales_order || ""),
				`<span style="color:${color};font-weight:800">● ${esc(status)}</span>`,
			];
			if (status !== "Pending") {
				if (stop.dropoff_gps_status === "Unavailable" || (!drop && stop.dropoff_gps_status !== "Captured")) {
					lines.push(__("Drop-off: no GPS (confirmed from the office or no fix)"));
				} else if (distance_text) {
					lines.push(__("Drop-off: <b>{0}</b> from the customer's pin ({1})", [distance_text, stop.is_on_site_dropoff ? __("on site") : __("off site")]));
				} else if (drop) {
					lines.push(__("Drop-off recorded (the customer had no pin)"));
				}
				if (stop.dropoff_gps_status === "Mock Detected") lines.push(`<b style="color:#dc2626">${__("Mock location detected")}</b>`);
				if (stop.dropoff_accuracy_m) lines.push(__("GPS accuracy ±{0} m", [Math.round(stop.dropoff_accuracy_m)]));
			}
			return `<div style="min-width:210px;line-height:1.55;font-size:12.5px">${lines.join("<br>")}</div>`;
		};

		if (pin) {
			const el = document.createElement("div");
			el.style.cssText = `background:${color};color:#fff;border-radius:50%;width:26px;height:26px;display:flex;align-items:center;justify-content:center;font-weight:800;font-size:11px;border:2px solid #fff;box-shadow:0 0 4px rgba(0,0,0,.45)`;
			el.textContent = stop.idx;
			view.setMarker(`stop:${stop.name}`, {
				lat: pin[0], lng: pin[1], element: el, anchor: "center", zIndex: 200,
				onClick: () => view.showPopup(popup(), pin[0], pin[1], { offset: 16 }),
			});
			points.push(pin);
		}

		if (drop) {
			dropoffs += 1;
			// The map engine owns the root element's transform; the label offset goes on an inner element.
			const el = document.createElement("div");
			el.style.cssText = "width:12px;height:12px;position:relative";
			el.innerHTML = `<span style="position:absolute;inset:0;border-radius:50%;background:${color};border:2px solid #111827"></span>
				${distance_text ? `<span style="position:absolute;left:16px;top:-2px;background:rgba(15,23,42,.85);color:#fff;font-size:10px;font-weight:800;padding:1px 6px;border-radius:6px;white-space:nowrap">#${stop.idx} · ${esc(distance_text)}</span>` : ""}`;
			view.setMarker(`drop:${stop.name}`, {
				lat: drop[0], lng: drop[1], element: el, anchor: "center", zIndex: 150,
				onClick: () => view.showPopup(popup(), drop[0], drop[1], { offset: 10 }),
			});
			if (pin) view.setLine(`drop:${stop.name}`, [pin, drop], { color: "#6b7280", width: 2, opacity: 0.9, dash: [4, 4], zIndex: 2 });
			points.push(drop);
		}
	});

	if (points.length) view.fitPoints(points, { padding: 40, maxZoom: 16 });

	const legend = Object.entries(NEXUS_STATUS_COLORS)
		.map(([s, c]) => `<span style="margin-right:10px"><span style="display:inline-block;width:9px;height:9px;border-radius:50%;background:${c};margin-right:4px"></span>${__(s)}</span>`)
		.join("");
	field.$wrapper.find(".nexus-map-legend").html(
		legend + (dropoffs ? `<span style="margin-left:6px">${__("Small dot + dashed line = where the driver confirmed, with its distance from the customer's pin.")}</span>` : ""),
	);
}
