// apps/nexus_supply_chain/nexus_supply_chain/nexus_supply_chain/page/nexus_sales_dispatch/nexus_sales_dispatch.js
//
// Nexus Sales Team Command Center.
//
//  - Roster: nexus_sales_dispatch.get_sales_team (unchanged server code).
//  - Live: FastAPI /api/v2/fleet/ws (kind=sales) through NexusDispatchSocket.
//    Status comes from the server and is the same everywhere:
//        Live          recent contact (Traveling / Checked-In shown as the activity)
//        On Stand-by   no contact for a while (replaces "Weak Signal")
//        Offline       signed out or silent for hours: the pin STAYS at the
//                      last ping, greyed, with "last seen …"
//  - Map: NexusMaps adapter (Google or Hosted, toggle top-right); pins glide
//    to new positions and are only redrawn when their look changes.
//  - View Route: the rep's checked-in visits for a day, road geometry from
//    your routing server, numbered customer / lead pins; works on both engines.
//  - Pull Sales Attendance + Excel export: unchanged.
/**
 * Loads the shared Nexus scripts once each, stamped with the deployed asset
 * version (production caches /assets for a year; the stamp changes on every
 * deploy). Shared by every page through window.__nexusScripts.
 */
function nsd_load_scripts(urls) {
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

frappe.pages["nexus_sales_dispatch"].on_page_load = function (wrapper) {
	const page = frappe.ui.make_app_page({
		parent: wrapper,
		title: __("Nexus Sales Team Command Center"),
		single_column: true,
	});

	const METHODS = {
		team: "nexus_supply_chain.nexus_supply_chain.page.nexus_sales_dispatch.nexus_sales_dispatch.get_sales_team",
		route: "nexus_supply_chain.nexus_supply_chain.page.nexus_sales_dispatch.nexus_sales_dispatch.get_sales_person_route",
		attendance: "nexus_supply_chain.nexus_supply_chain.page.nexus_sales_dispatch.nexus_sales_dispatch.get_sales_attendance",
	};
	const ASSETS = [
		"/assets/nexus_supply_chain/js/nexus_gmaps_loader.js",
		"/assets/nexus_supply_chain/js/nexus_map_adapter.js",
		"/assets/nexus_supply_chain/js/nexus_dispatch_socket.js",
	];
	const ACTIVITY_COLORS = { "Checked-In": "#10b981", Traveling: "#3b82f6" };
	const STANDBY_COLOR = "#f59e0b";
	const OFFLINE_COLOR = "#94a3b8";
	const ROUTE_PIN_COLORS = {
		Customer: { fill: "#EA4335", text: "#7f1d1d" },
		Lead: { fill: "#7C3AED", text: "#3b0764" },
	};

	const esc = (s) => frappe.utils.escape_html(String(s ?? ""));
	const $root = $(wrapper).find(".layout-main-section");

	let view = null;
	let socket = null;
	const roster = new Map(); // email -> {email, full_name}
	const entities = new Map(); // email -> socket item
	const cardHtml = new Map(); // email -> last inner html
	const pinLook = new Map(); // email -> look key
	let selected = null;
	let showOffline = true;
	let routeOpen = null; // {email, repName, date}
	const filters = { active: "", standby: "" };

	page.add_inner_button(__("Pull Sales Attendance"), () => {
		try {
			show_attendance_dialog();
		} catch (err) {
			console.error("[Nexus Sales Dispatch] Attendance dialog failed:", err);
			frappe.msgprint({ title: __("Error"), indicator: "red", message: __("Could not open the Attendance dialog.") });
		}
	});

	// ================================================================ layout
	$root.html(`
		<div class="nsd">
			<div class="nsd-col nsd-side">
				<div class="nsd-head">
					<div class="nsd-title"><i class="fa fa-users"></i> ${__("Active Field Team")}</div>
					<span class="nsd-conn" data-state="connecting">${__("Connecting…")}</span>
				</div>
				<input type="search" class="form-control input-sm nsd-search" data-col="active" placeholder="${__("Filter active rep…")}">
				<div class="nsd-list" data-col="active"></div>
			</div>
			<div class="nsd-col nsd-center">
				<div class="nsd-map"></div>
				<div class="nsd-legend">
					<div class="nsd-legend-title">${__("Map")}</div>
					<div><span class="nsd-dot" style="background:#3b82f6"></span>${__("Live · Traveling")}</div>
					<div><span class="nsd-dot" style="background:#10b981"></span>${__("Live · Checked-In")}</div>
					<div><span class="nsd-dot" style="background:${STANDBY_COLOR}"></span>${__("On Stand-by")}</div>
					<div><span class="nsd-dot" style="background:${OFFLINE_COLOR}"></span>${__("Offline (last ping)")}</div>
					<label class="nsd-legend-check"><input type="checkbox" class="nsd-show-offline" checked> ${__("Show offline")}</label>
					<div class="nsd-legend-sep"></div>
					<div class="nsd-legend-title">${__("Route stops")}</div>
					<div><svg class="nsd-legend-pin" width="12" height="17" viewBox="0 0 28 40"><path d="M14 0C6.3 0 0 6.3 0 14c0 10.5 14 26 14 26s14-15.5 14-26c0-7.7-6.3-14-14-14z" fill="#EA4335"/><circle cx="14" cy="14" r="6" fill="#fff"/></svg>${__("Customer visit")}</div>
					<div><svg class="nsd-legend-pin" width="12" height="17" viewBox="0 0 28 40"><path d="M14 0C6.3 0 0 6.3 0 14c0 10.5 14 26 14 26s14-15.5 14-26c0-7.7-6.3-14-14-14z" fill="#7C3AED"/><circle cx="14" cy="14" r="6" fill="#fff"/></svg>${__("Lead visit")}</div>
				</div>
				<div class="nsd-panel" style="display:none"></div>
			</div>
			<div class="nsd-col nsd-side">
				<div class="nsd-head">
					<div class="nsd-title nsd-title-muted"><i class="fa fa-user-times"></i> ${__("Offline Team")}</div>
				</div>
				<input type="search" class="form-control input-sm nsd-search" data-col="standby" placeholder="${__("Filter offline rep…")}">
				<div class="nsd-list" data-col="standby"><div class="nsd-empty"><i class="fa fa-spinner fa-spin"></i> ${__("Loading team…")}</div></div>
			</div>
		</div>
	`);

	if (!document.getElementById("nsd-css")) {
		$("head").append(`<style id="nsd-css">
			.nsd{display:grid;grid-template-columns:minmax(260px,1fr) minmax(0,2fr) minmax(240px,1fr);height:calc(100vh - 150px);min-height:560px;
				border:1px solid var(--border-color);border-radius:10px;overflow:hidden;background:var(--card-bg);margin:12px 0}
			@media (max-width:1100px){.nsd{grid-template-columns:1fr;height:auto}.nsd-center{height:60vh}}
			.nsd-col{display:flex;flex-direction:column;min-height:0}
			.nsd-side{background:#f8fafc;border-right:1px solid var(--border-color)}
			.nsd-side:last-child{border-right:0;border-left:1px solid var(--border-color)}
			.nsd-head{display:flex;justify-content:space-between;align-items:center;padding:12px 14px 8px}
			.nsd-title{font-weight:800;font-size:12px;letter-spacing:.08em;text-transform:uppercase;color:#047857}
			.nsd-title .fa{margin-right:6px}
			.nsd-title-muted{color:#475569}
			.nsd-search{margin:0 14px 10px;width:auto}
			.nsd-list{flex:1;overflow:auto;padding:0 12px 16px}
			.nsd-empty{color:#94a3b8;text-align:center;padding:30px 10px;font-size:13px}
			.nsd-conn{font-size:10px;font-weight:800;padding:3px 8px;border-radius:10px;border:1px solid #cbd5e1;color:#64748b;background:#fff;white-space:nowrap;max-width:60%;overflow:hidden;text-overflow:ellipsis}
			.nsd-conn[data-state=open]{color:#047857;border-color:#10b981;background:#ecfdf5}
			.nsd-conn[data-state=reconnecting],.nsd-conn[data-state=offline]{color:#b45309;border-color:#f59e0b;background:#fffbeb}
			.nsd-conn[data-state=denied]{color:#b91c1c;border-color:#ef4444;background:#fef2f2}
			.nsd-card{background:#fff;border:1px solid #e2e8f0;border-left:5px solid var(--c);border-radius:10px;padding:12px;margin-bottom:10px;cursor:pointer;transition:box-shadow .15s,transform .15s}
			.nsd-card:hover{box-shadow:0 6px 16px rgba(15,23,42,.08);transform:translateY(-1px)}
			.nsd-card.selected{box-shadow:0 0 0 3px rgba(14,165,233,.55)}
			.nsd-card.offline{background:#f8fafc}
			.nsd-top{display:flex;justify-content:space-between;align-items:flex-start;gap:8px}
			.nsd-name{font-size:15px;font-weight:800;color:#1e293b;line-height:1.2;word-break:break-word}
			.nsd-card.offline .nsd-name{color:#475569}
			.nsd-speed{font-family:ui-monospace,Menlo,Consolas,monospace;font-weight:700;font-size:13px;color:#64748b;white-space:nowrap}
			.nsd-email{font-size:11px;color:#64748b;margin:2px 0 8px}
			.nsd-badge{display:inline-block;padding:3px 9px;border-radius:6px;font-size:10px;font-weight:800;text-transform:uppercase;letter-spacing:.06em;color:#fff;background:var(--c);margin-bottom:8px}
			.nsd-meta{font-size:12.5px;color:#475569;display:flex;align-items:center;margin:2px 0;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
			.nsd-meta .fa{width:16px;color:#94a3b8;margin-right:6px}
			.nsd-conn-row{display:flex;align-items:center;font-size:12px;margin-top:8px;padding:6px 8px;background:#f8fafc;border-radius:6px;color:#334155}
			.nsd-sdot{width:9px;height:9px;border-radius:50%;margin-right:7px;flex-shrink:0;display:inline-block}
			.nsd-sdot.live{background:#22c55e;animation:nsdpulse 2s infinite}
			.nsd-sdot.standby{background:${STANDBY_COLOR}}
			.nsd-sdot.offline,.nsd-sdot.none{background:#cbd5e1}
			@keyframes nsdpulse{0%{box-shadow:0 0 0 0 rgba(34,197,94,.55)}70%{box-shadow:0 0 0 7px rgba(34,197,94,0)}100%{box-shadow:0 0 0 0 rgba(34,197,94,0)}}
			.nsd-card .btn{margin-top:8px;font-weight:700}
			.nsd-center{position:relative;background:#e5e7eb}
			.nsd-map{position:absolute;inset:0}
			.nsd-legend{position:absolute;top:12px;left:12px;z-index:4;background:#fff;border-radius:10px;padding:10px 12px;font-size:11.5px;color:#334155;box-shadow:0 4px 14px rgba(15,23,42,.18);min-width:150px}
			.nsd-legend>div{display:flex;align-items:center;margin:3px 0}
			.nsd-legend-title{font-weight:800;text-transform:uppercase;font-size:10px;letter-spacing:.08em;color:#64748b}
			.nsd-legend-sep{height:1px;background:#e2e8f0;margin:6px 0!important}
			.nsd-legend-check{display:flex;align-items:center;gap:6px;margin:4px 0 0;font-weight:600;cursor:pointer}
			.nsd-legend-pin{margin-right:8px}
			.nsd-dot{width:10px;height:10px;border-radius:50%;margin-right:8px;display:inline-block}
			.nsd-panel{position:absolute;left:12px;bottom:12px;z-index:4;background:#fff;border-radius:12px;box-shadow:0 10px 30px rgba(15,23,42,.25);padding:12px 14px;font-size:12.5px;min-width:240px;max-width:calc(100% - 24px)}
			.nsd-panel-head{display:flex;justify-content:space-between;align-items:center;gap:10px;margin-bottom:6px}
			.nsd-x{border:0;background:#f1f5f9;border-radius:50%;width:26px;height:26px;color:#475569;flex-shrink:0}
			.nsd-x:hover{background:#ef4444;color:#fff}
			.nsd-pin{position:relative;width:34px;height:34px}
			.nsd-pin .ring{position:absolute;top:4px;left:4px;width:26px;height:26px;border-radius:50%;background:#fff;border:2px solid var(--c);box-shadow:0 2px 5px rgba(0,0,0,.3);display:flex;align-items:center;justify-content:center}
			.nsd-pin.standby .ring{border-style:dashed;border-width:3px}
			.nsd-pin.offline{opacity:.6;filter:grayscale(1)}
			.nsd-pin .dir{position:absolute;inset:0;transition:transform .6s linear}
			.nsd-pin .tag{position:absolute;top:35px;left:50%;transform:translateX(-50%);background:rgba(15,23,42,.85);color:#fff;font-size:10px;font-weight:700;padding:1px 6px;border-radius:6px;white-space:nowrap;pointer-events:none}
			.nsd-cp{position:relative;width:28px;height:40px}
			.nsd-cp span{position:absolute;top:6px;left:0;width:28px;text-align:center;font-size:10px;font-weight:800}
			.nsd-pop{min-width:200px;font-size:12.5px;line-height:1.6}
			.nsd-pop-title{font-weight:900;font-size:14px;color:#0f172a}
		</style>`);
	}

	// ================================================================ helpers
	function statusOf(item) {
		if (!item) return { cls: "none", label: __("Not seen"), color: OFFLINE_COLOR };
		const ago = NexusMaps.timeAgo(item.last_seen);
		if (item.status === "Live") {
			const act = item.activity === "Checked-In" ? "Checked-In" : "Traveling";
			return { cls: "live", label: act === "Checked-In" ? __("Checked-In") : __("Traveling"), color: ACTIVITY_COLORS[act], line: __("Live") };
		}
		if (item.status === "On Stand-by") {
			return { cls: "standby", label: __("On Stand-by"), color: STANDBY_COLOR, line: __("On Stand-by · last ping {0}", [ago]) };
		}
		return {
			cls: "offline",
			label: __("Offline"),
			color: OFFLINE_COLOR,
			line: item.signed_out ? __("Offline · signed out {0}", [ago]) : __("Offline · last seen {0}", [ago]),
		};
	}

	function columnFor(email) {
		const item = entities.get(email);
		return item && (item.status === "Live" || item.status === "On Stand-by") ? "active" : "standby";
	}

	function displayName(email) {
		const r = roster.get(email);
		const item = entities.get(email);
		return (r && r.full_name) || (item && item.name) || email;
	}

	/** Checked-in customer (also while On Stand-by: weak signal inside a shop is common); "In transit" only when Live. */
	function customerText(item) {
		if (!item || item.status === "Offline") return null;
		const c = item.current_customer;
		if (item.activity === "Checked-In" && c && c !== "None") {
			return `${item.current_visit_type === "Lead" ? __("Lead") : __("Customer")}: ${c}`;
		}
		return item.status === "Live" ? __("In transit") : null;
	}

	function cardInner(email) {
		const item = entities.get(email);
		const st = statusOf(item);
		const cust = customerText(item);
		const visits = item && item.daily_visit_count ? `<div class="nsd-meta"><i class="fa fa-check-square-o"></i>${__("{0} visit(s) today", [item.daily_visit_count])}</div>` : "";
		return `
			<div class="nsd-top"><span class="nsd-name">${esc(displayName(email))}</span>
				<span class="nsd-speed">${item && item.status === "Live" ? `${Math.round((item.spd || 0) * 3.6)} km/h` : "--"}</span></div>
			<div class="nsd-email"><i class="fa fa-envelope-o"></i> ${esc(email)}</div>
			<div class="nsd-badge">● ${esc(st.label)}</div>
			${cust ? `<div class="nsd-meta"><i class="fa fa-building"></i>${esc(cust)}</div>` : ""}
			${visits}
			<div class="nsd-conn-row"><span class="nsd-sdot ${st.cls}"></span>${esc(item ? st.line : __("No location received yet"))}</div>
			<button type="button" class="btn btn-default btn-xs btn-block nsd-route-btn"><i class="fa fa-road"></i> ${__("View Route")}</button>`;
	}

	function allEmails() {
		const set = new Set([...roster.keys(), ...entities.keys()]);
		return [...set].sort((a, b) => displayName(a).toLowerCase().localeCompare(displayName(b).toLowerCase()));
	}

	function layoutCards() {
		const cols = { active: [], standby: [] };
		allEmails().forEach((email) => {
			const html = cardInner(email);
			cardHtml.set(email, html);
			const st = statusOf(entities.get(email));
			const search = `${displayName(email)} ${email}`.toLowerCase();
			cols[columnFor(email)].push(
				`<div class="nsd-card ${st.cls}${selected === email ? " selected" : ""}" data-email="${esc(email)}" data-search="${esc(search)}" style="--c:${st.color}">${html}</div>`,
			);
		});
		$root.find('.nsd-list[data-col="active"]').html(cols.active.join("") || `<div class="nsd-empty">${__("Nobody is live right now.")}</div>`);
		$root.find('.nsd-list[data-col="standby"]').html(cols.standby.join("") || `<div class="nsd-empty">${__("Everyone is live.")}</div>`);
		applyFilters();
	}

	/** Updates one card in place, or moves it to the other column when its status crosses Live/Stand-by ↔ Offline. */
	function renderCard(email) {
		const $card = $root.find(`.nsd-card[data-email="${CSS.escape(email)}"]`);
		if (!$card.length) {
			layoutCards();
			return;
		}
		const col = columnFor(email);
		if ($card.closest(".nsd-list").attr("data-col") !== col) {
			layoutCards();
			return;
		}
		const html = cardInner(email);
		const st = statusOf(entities.get(email));
		$card.removeClass("live standby offline none").addClass(st.cls).css("--c", st.color);
		if (cardHtml.get(email) === html) return;
		cardHtml.set(email, html);
		$card.html(html);
	}

	function applyFilters() {
		["active", "standby"].forEach((col) => {
			const q = filters[col];
			$root.find(`.nsd-list[data-col="${col}"] .nsd-card`).each(function () {
				$(this).toggle(!q || ($(this).attr("data-search") || "").includes(q));
			});
		});
	}

	function setConnState(state, detail) {
		const labels = { connecting: __("Connecting…"), open: __("Live"), reconnecting: __("Reconnecting…"), offline: __("Offline"), denied: __("No access") };
		const $c = $root.find(".nsd-conn");
		$c.attr("data-state", state).text(labels[state] || state);
		const why = detail && (typeof detail === "string" ? detail : detail.error);
		$c.attr("title", why || "");
		if (state === "denied" && !setConnState.shown) {
			setConnState.shown = true;
			frappe.msgprint({ title: __("Live map unavailable"), message: esc(why || ""), indicator: "red" });
		}
	}

	// ================================================================ pins
	function pinElement(item, st, label) {
		const el = document.createElement("div");
		el.className = `nsd-pin ${st.cls}`;
		el.style.setProperty("--c", st.color);
		el.innerHTML = `
			<svg class="dir" width="34" height="34" viewBox="0 0 24 24" style="transform:rotate(${Math.round(item.hdg || 0)}deg)">
				<path d="M12 2L15 8L12 6L9 8L12 2Z" fill="${st.color}" opacity="${st.cls === "live" && item.activity !== "Checked-In" ? 0.7 : 0}"/></svg>
			<div class="ring"><svg width="16" height="16" viewBox="0 0 24 24" fill="${st.color}">
				<path d="M12 12c2.21 0 4-1.79 4-4s-1.79-4-4-4-4 1.79-4 4 1.79 4 4 4zm0 2c-2.67 0-8 1.34-8 4v2h16v-2c0-2.66-5.33-4-8-4z"/></svg></div>
			${label ? `<div class="tag">${esc(label)}</div>` : ""}`;
		return el;
	}

	function renderPin(email, before, instant) {
		const item = entities.get(email);
		if (!view || !item || !NexusMaps.validPoint(item.lat, item.lng)) return;
		const st = statusOf(item);
		const hidden = !!routeOpen || (st.cls === "offline" && !showOffline);
		const label = st.cls === "offline" ? `${displayName(email).split(" ")[0]} · ${NexusMaps.timeAgo(item.last_seen)}` : "";
		const look = `${st.cls}|${st.color}|${item.activity}|${label}`;
		const id = `rep:${email}`;
		const zIndex = st.cls === "live" ? 300 : st.cls === "standby" ? 200 : 100;
		if (!view.hasMarker(id) || pinLook.get(email) !== look) {
			pinLook.set(email, look);
			view.setMarker(id, {
				lat: item.lat,
				lng: item.lng,
				element: pinElement(item, st, label),
				anchor: "center",
				zIndex,
				hidden,
				onClick: () => showRepPopup(email),
			});
			return;
		}
		view.updateMarker(id, { hidden, zIndex });
		const spec = view.markers.get(id);
		const dir = spec && spec.element && spec.element.querySelector(".dir");
		if (dir) dir.style.transform = `rotate(${Math.round(item.hdg || 0)}deg)`;
		if (!before || before.lat !== item.lat || before.lng !== item.lng) {
			if (instant) view.updateMarker(id, { lat: item.lat, lng: item.lng });
			else view.animateMarker(id, item.lat, item.lng, 1200);
		}
	}

	function showRepPopup(email) {
		const item = entities.get(email);
		if (!item || !view) return;
		const st = statusOf(item);
		const cust = customerText(item);
		view.showPopup(
			`<div class="nsd-pop"><div class="nsd-pop-title">${esc(displayName(email))}</div>
				<div><span class="nsd-sdot ${st.cls}"></span>${esc(st.line)}${st.cls === "live" ? ` · ${esc(st.label)}` : ""}</div>
				${cust ? `<div>${esc(cust)}</div>` : ""}
				${item.daily_visit_count ? `<div>${__("{0} visit(s) today", [item.daily_visit_count])}</div>` : ""}</div>`,
			item.lat,
			item.lng,
			{ offset: 20 },
		);
	}

	// ================================================================ live data
	function upsert(item, instant) {
		const email = String(item.email || (item.id || "").replace(/^sales:/, "")).toLowerCase();
		if (!email) return null;
		const before = entities.get(email);
		entities.set(email, { ...item, email });
		renderPin(email, before, instant);
		return email;
	}

	function removeById(id) {
		const email = String(id || "").replace(/^sales:/, "").toLowerCase();
		entities.delete(email);
		pinLook.delete(email);
		if (view) view.removeMarker(`rep:${email}`);
		return email;
	}

	function onSnapshot(items) {
		const seen = new Set();
		items.forEach((item) => {
			const email = upsert(item, true);
			if (email) seen.add(email);
		});
		[...entities.keys()].forEach((email) => !seen.has(email) && removeById(`sales:${email}`));
		layoutCards();
	}

	function onDelta(items) {
		items.forEach((item) => {
			const email = item.removed ? removeById(item.id) : upsert(item, false);
			if (email) renderCard(email);
		});
	}

	// ================================================================ events
	$root.on("input", ".nsd-search", function () {
		filters[$(this).data("col")] = ($(this).val() || "").toLowerCase();
		applyFilters();
	});

	$root.on("change", ".nsd-show-offline", function () {
		showOffline = this.checked;
		entities.forEach((_item, email) => renderPin(email));
	});

	$root.on("click", ".nsd-card", function (e) {
		if ($(e.target).closest("button").length) return;
		const email = $(this).attr("data-email");
		selected = email;
		$root.find(".nsd-card").removeClass("selected");
		$(this).addClass("selected");
		const item = entities.get(email);
		if (item && NexusMaps.validPoint(item.lat, item.lng) && view) {
			view.flyTo(item.lat, item.lng, 16);
			showRepPopup(email);
		} else {
			frappe.show_alert({ message: __("No location has been received from this rep yet."), indicator: "orange" });
		}
	});

	$root.on("click", ".nsd-route-btn", function (e) {
		e.stopPropagation();
		const email = $(this).closest(".nsd-card").attr("data-email");
		show_route_dialog(email, displayName(email));
	});

	$root.on("click", ".nsd-close-route", () => closeRoute());

	// ================================================================ route view
	function show_route_dialog(email, repName) {
		const d = new frappe.ui.Dialog({
			title: __("View Route — {0}", [repName]),
			fields: [{ fieldtype: "Date", fieldname: "route_date", label: __("Date"), default: frappe.datetime.nowdate(), reqd: 1 }],
			primary_action_label: __("View Route"),
			primary_action(values) {
				d.hide();
				loadRoute(email, values.route_date, repName);
			},
		});
		d.show();
	}

	function loadRoute(email, route_date, repName) {
		frappe.show_alert({ message: __("Loading route for {0} on {1}…", [repName, route_date]), indicator: "blue" });
		frappe.call({
			method: METHODS.route,
			args: { sales_person_email: email, route_date: route_date },
			callback(r) {
				if (!r.exc && r.message && r.message.status === "success" && (r.message.checkpoints || []).length) {
					renderRoute(r.message, email, repName, route_date);
				} else {
					frappe.msgprint({ title: __("No Route Data"), indicator: "orange", message: __("No check-in records found for this rep on the selected date.") });
				}
			},
		});
	}

	function strip_microseconds(ts) {
		return ts ? String(ts).split(".")[0] : null;
	}

	function clearRouteLayers() {
		if (!view) return;
		view.markerIds().filter((id) => id.startsWith("cp:")).forEach((id) => view.removeMarker(id));
		view.lineIds().filter((id) => id.startsWith("route:")).forEach((id) => view.removeLine(id));
		view.closePopup();
	}

	function closeRoute() {
		routeOpen = null;
		clearRouteLayers();
		$root.find(".nsd-panel").hide().empty();
		entities.forEach((_item, email) => renderPin(email)); // live pins back
	}

	function renderRoute(data, email, repName, route_date) {
		if (!view) return;
		clearRouteLayers();
		routeOpen = { email, repName, date: route_date };
		entities.forEach((_item, e) => renderPin(e)); // hides live pins while the route is open

		const checkpoints = data.checkpoints || [];
		const latlngs = checkpoints.map((cp) => [cp.lat, cp.lng]);

		// Road geometry from your routing server; a straight dashed connector only if it was unavailable.
		const roadLines = NexusMaps.geojsonLines(data.route_geometry);
		if (roadLines.length) {
			roadLines.forEach((line, i) => view.setLine(`route:${i}`, line, { color: "#3b82f6", width: 5, opacity: 0.85, zIndex: 2 }));
		} else if (latlngs.length > 1) {
			view.setLine("route:straight", latlngs, { color: "#3b82f6", width: 4, opacity: 0.85, dash: [10, 8], zIndex: 2 });
		}

		let totalOrderValue = 0;
		let customerStops = 0;
		let leadStops = 0;
		checkpoints.forEach((cp, idx) => {
			const isLead = cp.visit_type === "Lead";
			const colors = ROUTE_PIN_COLORS[isLead ? "Lead" : "Customer"];
			if (isLead) leadStops++;
			else {
				customerStops++;
				totalOrderValue += cp.order_value || 0;
			}
			const el = document.createElement("div");
			el.className = "nsd-cp";
			el.innerHTML = `<svg width="28" height="40" viewBox="0 0 28 40"><path d="M14 0C6.3 0 0 6.3 0 14c0 10.5 14 26 14 26s14-15.5 14-26c0-7.7-6.3-14-14-14z" fill="${colors.fill}"/>
				<circle cx="14" cy="14" r="6" fill="#fff"/></svg><span style="color:${colors.text}">${idx + 1}</span>`;
			view.setMarker(`cp:${idx}`, {
				lat: cp.lat,
				lng: cp.lng,
				element: el,
				anchor: "bottom",
				zIndex: 400 + idx,
				onClick: () => view.showPopup(checkpointPopup(cp, idx), cp.lat, cp.lng, { offset: 40 }),
			});
		});
		view.fitPoints(latlngs.concat(...roadLines), { padding: 60 });

		const distance =
			data.distance_source === "ors"
				? `${(data.total_km || 0).toFixed(2)} km`
				: data.distance_source === "single_point"
					? "0 km"
					: `<span class="text-danger">${__("Unavailable")}</span>`;
		$root.find(".nsd-panel").html(`
			<div class="nsd-panel-head"><b>${esc(repName)} — ${esc(route_date)}</b>
				<button type="button" class="nsd-x nsd-close-route" title="${__("Close")}"><i class="fa fa-times"></i></button></div>
			<div><i class="fa fa-road text-primary"></i> ${__("Distance travelled")}: <b>${distance}</b></div>
			<div><i class="fa fa-map-marker text-danger"></i> ${__("Checkpoints")}: <b>${checkpoints.length}</b>
				<span class="text-muted">(${customerStops} ${__("customer")} · ${leadStops} ${__("lead")})</span></div>
			<div><i class="fa fa-money text-success"></i> ${__("Total order value")}: <b>Sh ${totalOrderValue.toLocaleString("en-KE", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}</b></div>
			<div class="text-muted small" style="margin-top:4px">${__("Live pins are hidden while a route is open.")}</div>`).show();
	}

	function checkpointPopup(cp, idx) {
		const isLead = cp.visit_type === "Lead";
		const name = esc(cp.party_name || cp.customer_name);
		let detailLine;
		if (isLead) {
			if (cp.closed_by_conversion) {
				detailLine = `<b>${__("Converted to customer by the office during this visit")}</b>`;
			} else if (cp.lead_status_after) {
				const changed = cp.lead_status_before && cp.lead_status_before !== cp.lead_status_after;
				detailLine = `<b>${__("Status set")}: ${esc(cp.lead_status_after)}</b>` + (changed ? `<span class="text-muted"> (${__("was")} ${esc(cp.lead_status_before)})</span>` : "");
			} else {
				detailLine = cp.check_out_time ? `<b>${__("Status")}: ${esc(cp.lead_status_before || __("not recorded"))}</b>` : `<b>${__("Visit still open")}</b>`;
			}
		} else {
			const val = cp.order_value > 0 ? `Sh ${cp.order_value.toLocaleString("en-KE", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}` : __("No orders");
			detailLine = `<b>${__("Order Value")}: ${val}</b>`;
		}
		const leadBadge = isLead
			? `<span style="display:inline-block;margin-left:6px;padding:1px 7px;border-radius:10px;font-size:10px;font-weight:800;background:#ede9fe;color:#6d28d9;border:1px solid #c4b5fd">LEAD</span>`
			: "";
		return `<div class="nsd-pop">
			<div class="nsd-pop-title">${__("Stop")} ${idx + 1}: ${name}${leadBadge}</div>
			<div class="text-muted">${__("Check-In")}: ${esc(strip_microseconds(cp.check_in_time) || "—")}</div>
			<div class="text-muted">${__("Check-Out")}: ${esc(strip_microseconds(cp.check_out_time) || __("Still open"))}</div>
			<div>${detailLine}</div></div>`;
	}

	// ================================================================ attendance (unchanged)
	function show_attendance_dialog() {
		let d = new frappe.ui.Dialog({
			title: "Pull Sales Attendance",
			size: "extra-large",
			fields: [
				{ fieldtype: "Select", fieldname: "date_filter", label: "Date Range", options: ["Today", "Yesterday", "Custom Range"], default: "Today", reqd: 1 },
				{ fieldtype: "Column Break" },
				{ fieldtype: "Link", fieldname: "sales_person", label: "Sales Person", options: "Sales Person", description: "Leave empty to pull data for all active reps." },
				{ fieldtype: "Section Break", fieldname: "custom_dates_sec", depends_on: 'eval:doc.date_filter === "Custom Range"' },
				{ fieldtype: "Date", fieldname: "start_date", label: "Start Date", depends_on: 'eval:doc.date_filter === "Custom Range"', mandatory_depends_on: 'eval:doc.date_filter === "Custom Range"' },
				{ fieldtype: "Column Break", depends_on: 'eval:doc.date_filter === "Custom Range"' },
				{ fieldtype: "Date", fieldname: "end_date", label: "End Date", depends_on: 'eval:doc.date_filter === "Custom Range"', mandatory_depends_on: 'eval:doc.date_filter === "Custom Range"' },
				{ fieldtype: "Section Break" },
				{ fieldtype: "HTML", fieldname: "results_html" },
			],
			primary_action_label: "Pull Data",
			primary_action(values) {
				if (values.date_filter === "Custom Range") {
					if (values.start_date > values.end_date) {
						frappe.msgprint({ title: "Validation Error", indicator: "red", message: "Start Date cannot be greater than End Date." });
						return;
					}
				}

				d.get_field("results_html").$wrapper.html('<div class="text-center p-5"><i class="fa fa-spinner fa-spin fa-2x text-primary"></i><br><span class="text-muted mt-2 d-inline-block">Extracting Visit Data...</span></div>');

				frappe.call({
					method: METHODS.attendance,
					args: { date_filter: values.date_filter, start_date: values.start_date, end_date: values.end_date, sales_person: values.sales_person },
					callback: function (r) {
						if (!r.exc && r.message) {
							d.attendance_data = r.message;
							render_attendance_table(r.message, d);
						} else {
							render_attendance_table([], d);
						}
					},
					error: function () {
						d.get_field("results_html").$wrapper.html('<div class="alert alert-danger text-center mt-3">Connection error. Could not extract records.</div>');
					},
				});
			},
		});

		d.show();

		let $export_btn = $(`<button type="button" class="btn btn-default btn-sm">Export to Excel</button>`);
		$export_btn.on("click", function () {
			if (!d.attendance_data || d.attendance_data.length === 0) {
				frappe.msgprint("No attendance data available to export. Please pull data first.");
				return;
			}
			export_attendance_excel(d.attendance_data);
		});
		d.$wrapper.find(".modal-footer").prepend($export_btn);
	}

	function render_attendance_table(data, d) {
		if (!data || data.length === 0) {
			d.get_field("results_html").$wrapper.html('<div class="alert alert-warning text-center mt-3 mb-0" style="font-size: 14px;">No attendance or visit records found for the selected criteria.</div>');
			return;
		}

		let grand_visits = 0;
		let grand_customer_visits = 0;
		let grand_lead_visits = 0;
		let grand_onsite = 0;
		let grand_offsite = 0;
		let grand_orders = 0;
		let grand_order_val = 0;
		let grand_confirmed_orders = 0;
		let grand_confirmed_val = 0;
		let grand_invoices = 0;
		let grand_invoiced = 0;
		let grand_returns = 0;
		let grand_returned = 0;
		let grand_distance = 0;

		data.forEach((row) => {
			grand_visits += parseInt(row.total_visits || 0);
			grand_customer_visits += parseInt(row.customer_visits || 0);
			grand_lead_visits += parseInt(row.lead_visits || 0);
			grand_onsite += parseInt(row.onsite_visits || 0);
			grand_offsite += parseInt(row.offsite_visits || 0);
			grand_orders += parseInt(row.total_orders || 0);
			grand_order_val += parseFloat(row.total_order_value || 0);
			grand_confirmed_orders += parseInt(row.total_confirmed_orders || 0);
			grand_confirmed_val += parseFloat(row.total_confirmed_value || 0);
			grand_invoices += parseInt(row.total_invoices || 0);
			grand_invoiced += parseFloat(row.invoiced_amount || 0);
			grand_returns += parseInt(row.total_returns || 0);
			grand_returned += parseFloat(row.returned_amount || 0);
			grand_distance += parseFloat(row.distance_recorded_km || 0);
		});

		// Totals-row ratio comes from GRAND totals (total on-site ÷ total visits), never from averaging each rep's percentage.
		const grand_ratio = grand_visits > 0 ? (grand_onsite * 100) / grand_visits : 0;

		const fmt_currency = (val) => "Sh " + parseFloat(val || 0).toLocaleString("en-KE", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
		const fmt_ratio = (val) => `${parseFloat(val || 0).toFixed(1)}%`;
		const fmt_time_only = (time_str) => {
			if (!time_str) return "—";
			const parts = String(time_str).split(":");
			if (parts.length < 2) return time_str;
			let hh = parseInt(parts[0], 10);
			const mm = parts[1];
			const ampm = hh >= 12 ? "PM" : "AM";
			hh = hh % 12;
			if (hh === 0) hh = 12;
			return `${hh}:${mm} ${ampm}`;
		};

		const th = (label, align = "text-center") => `<th class="p-3 text-uppercase text-muted ${align} fw-bold align-middle" style="font-size: 12px;">${label}</th>`;

		let html = `
			<div class="table-responsive border rounded" style="max-height: 500px; overflow-y: auto;">
				<table class="table table-bordered table-hover m-0" style="font-size: 14px; background: #fff; white-space: nowrap;">
					<thead class="table-light position-sticky top-0" style="z-index: 10; border-bottom: 2px solid #e2e8f0;">
						<tr>
							<th class="p-3 text-uppercase text-muted fw-bold align-middle" style="font-size: 12px; min-width:160px;">Sales Person</th>
							<th class="p-3 text-uppercase text-muted align-middle" style="font-size: 12px;">Date</th>
							${th("Total Visits")}
							${th("Customer Visits")}
							${th("Lead Visits")}
							${th("On-Site")}
							${th("Off-Site")}
							${th("On-Site Ratio")}
							${th("Total Orders")}
							${th("Total Order Value", "text-end")}
							${th("Confirmed Orders")}
							${th("Confirmed Value", "text-end")}
							${th("Invoices")}
							${th("Invoiced Value", "text-end")}
							${th("Returns")}
							${th("Returned Value", "text-end")}
							<th class="p-3 text-uppercase text-muted align-middle" style="font-size: 12px;">First Check-In</th>
							<th class="p-3 text-uppercase text-muted align-middle" style="font-size: 12px;">Last Check-In</th>
							${th("Distance Recorded", "text-end")}
						</tr>
					</thead>
					<tbody>
		`;

		const td = (val, align = "text-center", extra = "") => `<td class="p-3 ${align} align-middle text-dark ${extra}" style="font-size: 14px;">${val}</td>`;

		data.forEach((row) => {
			const name = esc(row.sales_person_name || row.email || "Unknown");
			const period_date =
				row.period_start_date === row.period_end_date
					? frappe.datetime.str_to_user(row.period_start_date)
					: `${frappe.datetime.str_to_user(row.period_start_date)} → ${frappe.datetime.str_to_user(row.period_end_date)}`;

			html += `
				<tr>
					<td class="p-3 text-dark align-middle">${name}</td>
					<td class="p-3 align-middle text-dark" style="font-size: 13px;">${period_date}</td>
					${td(parseInt(row.total_visits || 0))}
					${td(parseInt(row.customer_visits || 0))}
					${td(parseInt(row.lead_visits || 0))}
					${td(parseInt(row.onsite_visits || 0))}
					${td(parseInt(row.offsite_visits || 0))}
					${td(fmt_ratio(row.onsite_ratio), "text-center", "fw-semibold")}
					${td(parseInt(row.total_orders || 0))}
					${td(fmt_currency(row.total_order_value), "text-end", "fw-semibold")}
					${td(parseInt(row.total_confirmed_orders || 0))}
					${td(fmt_currency(row.total_confirmed_value), "text-end", "fw-semibold")}
					${td(parseInt(row.total_invoices || 0))}
					${td(fmt_currency(row.invoiced_amount), "text-end", "fw-semibold")}
					${td(parseInt(row.total_returns || 0))}
					${td(fmt_currency(row.returned_amount), "text-end", "fw-semibold")}
					<td class="p-3 align-middle text-dark" style="font-size: 13px;">${fmt_time_only(row.first_visit_time)}</td>
					<td class="p-3 align-middle text-dark" style="font-size: 13px;">${fmt_time_only(row.last_visit_time)}</td>
					${td(`${parseFloat(row.distance_recorded_km || 0).toFixed(2)} km`, "text-end", "fw-semibold")}
				</tr>
			`;
		});

		const tf = (val, align = "text-center") => `<td class="p-3 ${align} text-dark fw-semibold align-middle" style="font-size: 15px;">${val}</td>`;

		html += `
					</tbody>
					<tfoot style="border-top: 3px solid #cbd5e1; background-color: #fef9c3;">
						<tr>
							<td class="p-3 fw-semibold text-dark align-middle" style="font-size: 12px; text-transform: uppercase;">TOTALS</td>
							<td class="p-3 align-middle"></td>
							${tf(grand_visits)}
							${tf(grand_customer_visits)}
							${tf(grand_lead_visits)}
							${tf(grand_onsite)}
							${tf(grand_offsite)}
							${tf(fmt_ratio(grand_ratio))}
							${tf(grand_orders)}
							${tf(fmt_currency(grand_order_val), "text-end")}
							${tf(grand_confirmed_orders)}
							${tf(fmt_currency(grand_confirmed_val), "text-end")}
							${tf(grand_invoices)}
							${tf(fmt_currency(grand_invoiced), "text-end")}
							${tf(grand_returns)}
							${tf(fmt_currency(grand_returned), "text-end")}
							<td colspan="2" class="p-3 text-muted fw-semibold align-middle" style="font-size: 12px; font-style: italic;">
								<i class="fa fa-info-circle me-1"></i> Aggregated period totals
							</td>
							${tf(`${grand_distance.toFixed(2)} km`, "text-end")}
						</tr>
					</tfoot>
				</table>
			</div>
		`;

		d.get_field("results_html").$wrapper.html(html);
	}

	function export_attendance_excel(data) {
		let csv =
			"Sales Person Name,Email,Date,Total Visits,Customer Visits,Lead Visits,On-Site Visits,Off-Site Visits,On-Site Ratio (%),Total Orders,Total Order Value (Sh),Confirmed Orders,Confirmed Order Value (Sh),Total Invoices,Invoiced Amount (Sh),Total Returns,Returned Amount (Sh),First Check-In,Last Check-In,Distance Recorded (km)\n";

		const fmt_time_only_csv = (time_str) => {
			if (!time_str) return "";
			const parts = String(time_str).split(":");
			if (parts.length < 2) return time_str;
			let hh = parseInt(parts[0], 10);
			const mm = parts[1];
			const ampm = hh >= 12 ? "PM" : "AM";
			hh = hh % 12;
			if (hh === 0) hh = 12;
			return `${hh}:${mm} ${ampm}`;
		};
		const quote = (v) => `"${String(v ?? "").replace(/"/g, '""')}"`;
		const money = (v) => parseFloat(v || 0).toFixed(2);
		let grand_visits = 0;
		let grand_onsite = 0;

		data.forEach((row) => {
			const name = row.sales_person_name || "Unknown";
			const email = row.email || "";
			const period_date = row.period_start_date === row.period_end_date ? row.period_start_date || "" : `${row.period_start_date || ""} to ${row.period_end_date || ""}`;

			grand_visits += parseInt(row.total_visits || 0);
			grand_onsite += parseInt(row.onsite_visits || 0);

			csv +=
				[
					quote(name),
					quote(email),
					quote(period_date),
					row.total_visits || 0,
					row.customer_visits || 0,
					row.lead_visits || 0,
					row.onsite_visits || 0,
					row.offsite_visits || 0,
					parseFloat(row.onsite_ratio || 0).toFixed(1),
					row.total_orders || 0,
					money(row.total_order_value),
					row.total_confirmed_orders || 0,
					money(row.total_confirmed_value),
					row.total_invoices || 0,
					money(row.invoiced_amount),
					row.total_returns || 0,
					money(row.returned_amount),
					quote(fmt_time_only_csv(row.first_visit_time)),
					quote(fmt_time_only_csv(row.last_visit_time)),
					parseFloat(row.distance_recorded_km || 0).toFixed(2),
				].join(",") + "\n";
		});

		// Overall ratio from grand totals, never an average of per-rep %.
		const overall_ratio = grand_visits > 0 ? ((grand_onsite * 100) / grand_visits).toFixed(1) : "0.0";
		csv += `"TOTAL ON-SITE RATIO","","",${grand_visits},,,${grand_onsite},,${overall_ratio}\n`;

		// BOM so Excel opens names with accents correctly.
		const blob = new Blob(["﻿" + csv], { type: "text/csv;charset=utf-8;" });
		const url = URL.createObjectURL(blob);
		const link = document.createElement("a");
		link.setAttribute("href", url);
		link.setAttribute("download", `Sales_Attendance_Export_${frappe.datetime.nowdate()}.csv`);
		document.body.appendChild(link);
		link.click();
		document.body.removeChild(link);
		setTimeout(() => URL.revokeObjectURL(url), 1000);
	}

	// ================================================================ start
	nsd_load_scripts(ASSETS).then(async () => {
		// The map loads ALONGSIDE the roster and the live feed, never in front of them:
		// a slow or unreachable tile server must not hold up the rest of the page.
		(async () => {
			try {
				view = await NexusMaps.createView($root.find(".nsd-map")[0], { center: [-1.2921, 36.8219], zoom: 12 });
				view.onMapClick(() => view.closePopup());
				entities.forEach((_item, email) => renderPin(email, null, true)); // pins that arrived meanwhile
			} catch (e) {
				console.error(e);
				$root.find(".nsd-map").html(`<div class="nsd-empty">${__("The map could not load. Check Nexus App Settings → Dispatch Map.")}</div>`);
			}
		})();

		try {
			const r = await frappe.call({ method: METHODS.team, type: "GET" });
			(r.message || []).forEach((rep) => {
				const email = String(rep.email || "").toLowerCase();
				if (email) roster.set(email, { email, full_name: rep.full_name || email });
			});
		} catch (e) {
			console.error("[Nexus Sales Dispatch] roster failed", e);
		}
		layoutCards();

		socket = new NexusDispatchSocket({
			kind: "sales",
			onState: setConnState,
			onSnapshot,
			onDelta,
		});
		socket.start();

		// "last seen x min ago" text and offline tags, every 30 s.
		setInterval(() => {
			if (document.hidden) return;
			allEmails().forEach((email) => renderCard(email));
			entities.forEach((item, email) => item.status === "Offline" && renderPin(email));
		}, 30000);
	}).catch((e) => {
		console.error(e);
		frappe.msgprint({ title: __("Sales Dispatch"), message: __("Couldn't load the map scripts. Run bench build and reload."), indicator: "red" });
	});

	wrapper.nexus_sales_dispatch = { resize: () => view && view.resize() };
};

frappe.pages["nexus_sales_dispatch"].on_page_show = function (wrapper) {
	if (wrapper.nexus_sales_dispatch) setTimeout(() => wrapper.nexus_sales_dispatch.resize(), 50);
};
