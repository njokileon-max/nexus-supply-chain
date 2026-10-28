// apps/nexus_supply_chain/nexus_supply_chain/nexus_supply_chain/page/nexus_live_dispatch/nexus_live_dispatch.js
//
// Nexus Fleet Command Center (Live Dispatch).
//
//  Data
//   - Board (vehicles + active trips with stop progress): ONE server call,
//     nexus_live_dispatch.get_dispatch_board, refreshed when a trip changes
//     ("manifests" socket message or the desk realtime event).
//   - Live positions: FastAPI /api/v2/fleet/ws (kind=driver) through
//     NexusDispatchSocket: one snapshot, then only what changed.
//
//  Map (NexusMaps adapter: Google or Hosted, toggle top-right)
//   - One pin per PHONE (device-keyed: "dev:<id>"), never per driver string,
//     so a driver switching trucks or signing out can't leave ghost pins.
//   - Status from the server: Live / On Stand-by / Offline. Offline pins stay
//     at the last ping, greyed, with "last seen" (toggle in the legend).
//   - Pins glide to new positions; cards and pins are only touched when
//     something they show actually changed.
//
//  Per vehicle: Route (planned route + stops), ETA (road route from the
//  truck through the remaining stops on your routing server, with arrival
//  times), Open trip.
frappe.pages["nexus_live_dispatch"].on_page_load = function (wrapper) {
	const page = frappe.ui.make_app_page({
		parent: wrapper,
		title: __("Nexus Fleet Command Center"),
		single_column: true,
	});
	wrapper.nexus_live_dispatch = new NexusLiveDispatch(page, wrapper);
};

frappe.pages["nexus_live_dispatch"].on_page_show = function (wrapper) {
	if (wrapper.nexus_live_dispatch) wrapper.nexus_live_dispatch.onShow();
};

const NLD_METHODS = {
	board: "nexus_supply_chain.nexus_supply_chain.page.nexus_live_dispatch.nexus_live_dispatch.get_dispatch_board",
	manifestMap: "nexus_supply_chain.nexus_supply_chain.page.nexus_live_dispatch.nexus_live_dispatch.get_manifest_map",
	eta: "nexus_supply_chain.nexus_supply_chain.page.nexus_live_dispatch.nexus_live_dispatch.get_eta_route",
};

/**
 * Loads the shared Nexus scripts once each, stamped with the deployed asset
 * version (production caches /assets for a year; the stamp changes on every
 * deploy). Shared by every page through window.__nexusScripts.
 */
function nld_load_scripts(urls) {
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

const NLD_ASSETS = [
	"/assets/nexus_supply_chain/js/nexus_gmaps_loader.js",
	"/assets/nexus_supply_chain/js/nexus_map_adapter.js",
	"/assets/nexus_supply_chain/js/nexus_dispatch_socket.js",
];

// group -> [label, theme colour, column]
const NLD_GROUPS = {
	en_route: [__("En Route"), "#10b981", "active"],
	returning: [__("Returning"), "#f59e0b", "active"],
	loading: [__("Loading / Ready"), "#3b82f6", "active"],
	idle: [__("Idle"), "#94a3b8", "standby"],
	maintenance: [__("Maintenance"), "#f43f5e", "standby"],
	phones: [__("Phones without a vehicle"), "#64748b", "standby"],
};
const NLD_GROUP_ORDER = ["en_route", "returning", "loading", "idle", "maintenance", "phones"];
const NLD_TRIP_GROUP = { Dispatched: "en_route", Returning: "returning", Ready: "loading" };
const NLD_STOP_COLORS = {
	Pending: "#2563eb",
	Delivered: "#16a34a",
	"Partially Delivered": "#d97706",
	Failed: "#dc2626",
	Returned: "#6b7280",
};
const NLD_STATUS_RANK = { Live: 0, "On Stand-by": 1, Offline: 2 };

class NexusLiveDispatch {
	constructor(page, wrapper) {
		this.page = page;
		this.wrapper = wrapper;
		this.$root = $(wrapper).find(".layout-main-section");
		this.vehicles = new Map(); // name -> vehicle
		this.trips = new Map(); // vehicle -> manifest row
		this.entities = new Map(); // entity id -> socket item
		this.cardHtml = new Map(); // card key -> last rendered html
		this.pinLook = new Map(); // entity id -> last look key
		this.selected = null; // card key
		this.routeView = null; // {manifest, vehicle, data, eta}
		this.settings = {};
		this.showOffline = true;
		this.filters = { active: "", standby: "" };
		this.boardLoaded = false;

		this.renderLayout();
		this.bindUi();
		this.init();
	}

	esc(v) {
		return frappe.utils.escape_html(v == null ? "" : String(v));
	}

	// ------------------------------------------------------------- startup
	async init() {
		try {
			await nld_load_scripts(NLD_ASSETS);
		} catch (e) {
			console.error(e);
			frappe.msgprint({ title: __("Live Dispatch"), message: __("Couldn't load the map scripts. Run bench build and reload."), indicator: "red" });
			return;
		}

		// The map loads ALONGSIDE the fleet list and the live feed, never in front of
		// them: a slow or unreachable tile server must not hold up the rest of the page.
		this.initMap();
		await this.loadBoard();

		this.socket = new NexusDispatchSocket({
			kind: "driver",
			onState: (state, detail) => this.setConnState(state, detail),
			onSettings: (s) => (this.settings = { ...this.settings, ...s }),
			onSnapshot: (items) => this.applySnapshot(items),
			onDelta: (items) => this.applyDelta(items),
			onManifests: () => this.scheduleBoardReload(),
		});
		this.socket.start();

		// Desk realtime (Batch 2): any trip change reloads the board too.
		frappe.realtime.on("nexus_manifest_update", (data) => {
			this.scheduleBoardReload();
			if (this.routeView && data && (data.manifests || []).includes(this.routeView.manifest)) {
				this.openRoute(this.routeView.vehicle, { keepCamera: true, keepEta: true });
			}
		});

		// "last seen x min ago" text, every 30 s (cheap: only changed cards are rewritten).
		this.clock = setInterval(() => !document.hidden && this.renderAllCards(), 30000);
	}

	async initMap() {
		try {
			this.view = await NexusMaps.createView(this.$root.find(".nld-map")[0], { center: [-1.2921, 36.8219], zoom: 11 });
			this.view.onMapClick(() => this.view.closePopup());
			// Pins for everything that arrived while the map was loading.
			this.entities.forEach((item) => this.renderPin(item, null, true));
		} catch (e) {
			console.error(e);
			this.$root.find(".nld-map").html(`<div class="nld-map-error">${__("The map could not load. Check Nexus App Settings → Dispatch Map.")}</div>`);
		}
	}

	onShow() {
		if (this.view) setTimeout(() => this.view.resize(), 50);
	}

	// ------------------------------------------------------------- layout
	renderLayout() {
		this.$root.html(`
			<div class="nld">
				<div class="nld-col nld-side">
					<div class="nld-head">
						<div class="nld-title"><i class="fa fa-truck"></i> ${__("Active Fleet")}</div>
						<span class="nld-conn" data-state="connecting">${__("Connecting…")}</span>
					</div>
					<input type="search" class="form-control input-sm nld-search" data-col="active" placeholder="${__("Filter plate, driver or trip…")}">
					<div class="nld-list" data-col="active"><div class="nld-empty"><i class="fa fa-spinner fa-spin"></i> ${__("Loading fleet…")}</div></div>
				</div>
				<div class="nld-col nld-center">
					<div class="nld-map"></div>
					<div class="nld-legend">
						<div class="nld-legend-title">${__("Map")}</div>
						<div><span class="nld-dot" style="background:#10b981"></span>${__("En route")}</div>
						<div><span class="nld-dot" style="background:#f59e0b"></span>${__("Returning")}</div>
						<div><span class="nld-dot" style="background:#3b82f6"></span>${__("Loading / Ready")}</div>
						<div><span class="nld-dot" style="background:#94a3b8"></span>${__("No active trip")}</div>
						<div class="nld-legend-sep"></div>
						<div><span class="nld-ring nld-ring-live"></span>${__("Live")}</div>
						<div><span class="nld-ring nld-ring-standby"></span>${__("On Stand-by")}</div>
						<div><span class="nld-ring nld-ring-offline"></span>${__("Offline (last ping)")}</div>
						<label class="nld-legend-check"><input type="checkbox" class="nld-show-offline" checked> ${__("Show offline")}</label>
						<div class="nld-legend-sep"></div>
						<div><span class="nld-line" style="border-top:3px dashed #94a3b8"></span>${__("Planned route")}</div>
						<div><span class="nld-line" style="border-top:4px solid #2563eb"></span>${__("ETA route")}</div>
					</div>
					<div class="nld-panel" style="display:none"></div>
				</div>
				<div class="nld-col nld-side">
					<div class="nld-head">
						<div class="nld-title nld-title-muted"><i class="fa fa-parking"></i> ${__("Standby Fleet")}</div>
					</div>
					<input type="search" class="form-control input-sm nld-search" data-col="standby" placeholder="${__("Filter standby…")}">
					<div class="nld-list" data-col="standby"></div>
				</div>
			</div>
		`);

		if (!document.getElementById("nld-css")) {
			$("head").append(`<style id="nld-css">
				.nld{display:grid;grid-template-columns:minmax(260px,1fr) minmax(0,2fr) minmax(240px,1fr);height:calc(100vh - 150px);min-height:560px;
					border:1px solid var(--border-color);border-radius:10px;overflow:hidden;background:var(--card-bg);margin:12px 0}
				@media (max-width:1100px){.nld{grid-template-columns:1fr;height:auto}.nld-center{height:60vh}}
				.nld-col{display:flex;flex-direction:column;min-height:0}
				.nld-side{background:#f8fafc;border-right:1px solid var(--border-color)}
				.nld-side:last-child{border-right:0;border-left:1px solid var(--border-color)}
				.nld-head{display:flex;justify-content:space-between;align-items:center;padding:12px 14px 8px}
				.nld-title{font-weight:800;font-size:12px;letter-spacing:.08em;text-transform:uppercase;color:#047857}
				.nld-title-muted{color:#475569}
				.nld-title .fa{margin-right:6px}
				.nld-search{margin:0 14px 10px;width:auto}
				.nld-list{flex:1;overflow:auto;padding:0 12px 16px}
				.nld-empty{color:#94a3b8;text-align:center;padding:30px 10px;font-size:13px}
				.nld-group{font-size:11px;font-weight:800;text-transform:uppercase;letter-spacing:.1em;color:#64748b;margin:14px 2px 8px;display:flex;align-items:center}
				.nld-group:after{content:"";flex:1;height:1px;background:#e2e8f0;margin-left:10px}
				.nld-group .nld-count{margin-left:6px;color:#94a3b8}
				.nld-conn{font-size:10px;font-weight:800;padding:3px 8px;border-radius:10px;border:1px solid #cbd5e1;color:#64748b;background:#fff;white-space:nowrap;max-width:60%;overflow:hidden;text-overflow:ellipsis}
				.nld-conn[data-state=open]{color:#047857;border-color:#10b981;background:#ecfdf5}
				.nld-conn[data-state=reconnecting],.nld-conn[data-state=offline]{color:#b45309;border-color:#f59e0b;background:#fffbeb}
				.nld-conn[data-state=denied]{color:#b91c1c;border-color:#ef4444;background:#fef2f2}
				.nld-card{background:#fff;border:1px solid #e2e8f0;border-left:5px solid var(--c);border-radius:10px;padding:12px 12px 10px;margin-bottom:10px;cursor:pointer;
					transition:box-shadow .15s,transform .15s}
				.nld-card:hover{box-shadow:0 6px 16px rgba(15,23,42,.08);transform:translateY(-1px)}
				.nld-card.selected{box-shadow:0 0 0 3px rgba(14,165,233,.55)}
				.nld-card-top{display:flex;justify-content:space-between;align-items:baseline}
				.nld-plate{font-size:16px;font-weight:900;color:#0f172a;letter-spacing:.3px}
				.nld-speed{font-family:ui-monospace,Menlo,Consolas,monospace;font-weight:700;color:#334155;font-size:13px}
				.nld-badge{display:inline-block;font-size:10px;font-weight:800;text-transform:uppercase;letter-spacing:.06em;padding:2px 8px;border-radius:6px;color:#fff;background:var(--c);margin:6px 0}
				.nld-meta{font-size:12.5px;color:#475569;margin:2px 0;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
				.nld-meta .fa{width:16px;color:#94a3b8}
				.nld-progress{height:5px;background:#f1f5f9;border-radius:3px;overflow:hidden;margin:6px 0 2px}
				.nld-progress>div{height:5px;background:var(--c)}
				.nld-conn-row{display:flex;align-items:center;font-size:12px;margin-top:8px;padding:6px 8px;background:#f8fafc;border-radius:6px;color:#334155}
				.nld-conn-row .nld-sdot{width:9px;height:9px;border-radius:50%;margin-right:7px;flex-shrink:0}
				.nld-sdot.live{background:#22c55e;box-shadow:0 0 0 0 rgba(34,197,94,.6);animation:nldpulse 2s infinite}
				.nld-sdot.standby{background:#f59e0b}
				.nld-sdot.offline,.nld-sdot.none{background:#cbd5e1}
				@keyframes nldpulse{0%{box-shadow:0 0 0 0 rgba(34,197,94,.55)}70%{box-shadow:0 0 0 7px rgba(34,197,94,0)}100%{box-shadow:0 0 0 0 rgba(34,197,94,0)}}
				.nld-actions{display:flex;gap:6px;margin-top:8px}
				.nld-actions .btn{flex:1;font-weight:700}
				.nld-center{position:relative;background:#e5e7eb}
				.nld-map{position:absolute;inset:0}
				.nld-map-error{padding:40px;text-align:center;color:#64748b}
				.nld-legend{position:absolute;top:12px;left:12px;z-index:4;background:#fff;border-radius:10px;padding:10px 12px;font-size:11.5px;color:#334155;
					box-shadow:0 4px 14px rgba(15,23,42,.18);min-width:150px}
				.nld-legend>div{display:flex;align-items:center;margin:3px 0}
				.nld-legend-title{font-weight:800;text-transform:uppercase;font-size:10px;letter-spacing:.08em;color:#64748b}
				.nld-legend-sep{height:1px;background:#e2e8f0;margin:6px 0!important}
				.nld-legend-check{display:flex;align-items:center;gap:6px;margin:4px 0 0;font-weight:600;cursor:pointer}
				.nld-dot{width:10px;height:10px;border-radius:50%;margin-right:8px;display:inline-block}
				.nld-ring{width:12px;height:12px;border-radius:50%;margin-right:8px;display:inline-block;border:3px solid}
				.nld-ring-live{border-color:#22c55e}.nld-ring-standby{border-color:#f59e0b;border-style:dashed}.nld-ring-offline{border-color:#cbd5e1}
				.nld-line{width:18px;height:0;margin-right:8px;display:inline-block}
				.nld-panel{position:absolute;left:12px;right:12px;bottom:12px;z-index:4;background:#fff;border-radius:12px;box-shadow:0 10px 30px rgba(15,23,42,.25);
					padding:12px 14px;max-height:45%;overflow:auto;font-size:12.5px}
				@media (min-width:1300px){.nld-panel{right:auto;width:420px}}
				.nld-panel-head{display:flex;justify-content:space-between;align-items:flex-start;gap:8px}
				.nld-panel-title{font-weight:900;font-size:14px;color:#0f172a}
				.nld-panel-sub{color:#64748b;font-size:12px;margin-top:2px}
				.nld-x{border:0;background:#f1f5f9;border-radius:50%;width:26px;height:26px;color:#475569;flex-shrink:0}
				.nld-x:hover{background:#ef4444;color:#fff}
				.nld-eta-row{display:flex;justify-content:space-between;padding:6px 0;border-bottom:1px solid #f1f5f9}
				.nld-eta-row b{color:#0f172a}
				.nld-eta-time{font-family:ui-monospace,Menlo,Consolas,monospace;font-weight:800;color:#1d4ed8;white-space:nowrap;margin-left:10px}
				.nld-note{color:#94a3b8;font-size:11.5px;margin-top:6px}
				.nld-pin{position:relative;width:30px;height:30px}
				.nld-pin .ring{position:absolute;inset:0;border-radius:50%;background:#fff;border:3px solid var(--c);box-shadow:0 2px 6px rgba(0,0,0,.35)}
				.nld-pin.standby .ring{border-style:dashed}
				.nld-pin.offline{opacity:.55;filter:grayscale(1)}
				.nld-pin .arrow{position:absolute;inset:0;display:flex;align-items:center;justify-content:center;transition:transform .6s linear}
				.nld-pin .tag{position:absolute;top:31px;left:50%;transform:translateX(-50%);background:rgba(15,23,42,.85);color:#fff;font-size:10px;font-weight:800;
					padding:1px 6px;border-radius:6px;white-space:nowrap;pointer-events:none}
				.nld-pin.live .ring{animation:nldpulse 2s infinite}
				.nld-stop{display:flex;align-items:center;justify-content:center;width:26px;height:26px;border-radius:50%;color:#fff;font-weight:800;font-size:11px;
					border:2px solid #fff;box-shadow:0 1px 5px rgba(0,0,0,.45)}
				.nld-pop{min-width:200px;font-size:12.5px;line-height:1.5}
				.nld-pop-title{font-weight:900;font-size:14px;margin-bottom:2px;color:#0f172a}
				.nld-pop .nld-actions{margin-top:8px}
			</style>`);
		}
	}

	bindUi() {
		const self = this;
		this.$root.on("input", ".nld-search", function () {
			self.filters[$(this).data("col")] = ($(this).val() || "").toLowerCase();
			self.applyFilters();
		});
		this.$root.on("change", ".nld-show-offline", function () {
			self.showOffline = this.checked;
			self.entities.forEach((item) => self.renderPin(item));
		});
		this.$root.on("click", ".nld-card", function (e) {
			if ($(e.target).closest("button, a").length) return;
			self.selectCard($(this).attr("data-key"), true);
		});
		this.$root.on("click", "[data-act]", function (e) {
			e.preventDefault();
			e.stopPropagation();
			const act = $(this).attr("data-act");
			const vehicle = $(this).attr("data-vehicle");
			if (act === "route") self.openRoute(vehicle);
			else if (act === "eta") self.openRoute(vehicle, { withEta: true });
			else if (act === "open") frappe.set_route("Form", "Vehicle Delivery Manifest", $(this).attr("data-manifest"));
			else if (act === "close-route") self.closeRoute();
			else if (act === "refresh-eta") self.loadEta();
		});
	}

	setConnState(state, detail) {
		const labels = {
			connecting: __("Connecting…"),
			open: __("Live"),
			reconnecting: __("Reconnecting…"),
			offline: __("Offline"),
			denied: __("No access"),
		};
		const $c = this.$root.find(".nld-conn");
		$c.attr("data-state", state).text(labels[state] || state);
		const why = detail && (typeof detail === "string" ? detail : detail.error);
		$c.attr("title", why || "");
		if (state === "denied" && !this._deniedShown) {
			this._deniedShown = true;
			frappe.msgprint({ title: __("Live map unavailable"), message: this.esc(why || ""), indicator: "red" });
		}
	}

	// ------------------------------------------------------------- board
	async loadBoard() {
		try {
			const r = await frappe.call({ method: NLD_METHODS.board, type: "GET" });
			const data = r.message || {};
			this.vehicles = new Map((data.vehicles || []).map((v) => [v.name, v]));
			this.trips = new Map();
			(data.manifests || []).forEach((m) => {
				// One active trip per vehicle; if the data ever holds two, the one on the road wins.
				const current = this.trips.get(m.vehicle);
				const rank = (t) => ({ Dispatched: 0, Returning: 1, Ready: 2 })[t.trip_status] ?? 3;
				if (!current || rank(m) < rank(current)) this.trips.set(m.vehicle, m);
			});
			this.boardLoaded = true;
			this.layoutCards();
			this.entities.forEach((item) => this.renderPin(item));
		} catch (e) {
			console.error("[Live Dispatch] board failed", e);
			this.$root.find('.nld-list[data-col="active"]').html(`<div class="nld-empty">${__("Couldn't load the fleet. Refresh the page.")}</div>`);
		}
	}

	scheduleBoardReload() {
		clearTimeout(this._boardTimer);
		this._boardTimer = setTimeout(() => this.loadBoard(), 800);
	}

	/** The phone (entity) that best represents a vehicle: Live first, then the most recent contact. */
	entityForVehicle(vehicle) {
		let best = null;
		this.entities.forEach((item) => {
			if (item.vehicle !== vehicle) return;
			if (
				!best ||
				(NLD_STATUS_RANK[item.status] ?? 3) < (NLD_STATUS_RANK[best.status] ?? 3) ||
				((NLD_STATUS_RANK[item.status] ?? 3) === (NLD_STATUS_RANK[best.status] ?? 3) && (item.last_seen || 0) > (best.last_seen || 0))
			) {
				best = item;
			}
		});
		return best;
	}

	groupForVehicle(v) {
		const trip = this.trips.get(v.name);
		if (trip && NLD_TRIP_GROUP[trip.trip_status]) return NLD_TRIP_GROUP[trip.trip_status];
		if (/maint/i.test(v.status || "")) return "maintenance";
		return "idle";
	}

	/** Phones that aren't attached to a known vehicle get their own card. */
	orphanEntities() {
		const out = [];
		this.entities.forEach((item) => {
			if (!item.vehicle || item.vehicle === "Idle" || !this.vehicles.has(item.vehicle)) out.push(item);
		});
		return out;
	}

	layoutCards() {
		const groups = {};
		NLD_GROUP_ORDER.forEach((g) => (groups[g] = []));
		this.vehicles.forEach((v) => groups[this.groupForVehicle(v)].push({ key: `veh:${v.name}`, vehicle: v }));
		this.orphanEntities().forEach((item) => groups.phones.push({ key: `ent:${item.id}`, entity: item }));

		const lists = { active: [], standby: [] };
		NLD_GROUP_ORDER.forEach((g) => {
			const items = groups[g];
			if (!items.length) return;
			items.sort((a, b) => this.cardSortKey(a).localeCompare(this.cardSortKey(b)));
			const [label, color, col] = NLD_GROUPS[g];
			lists[col].push(`<div class="nld-group" data-group="${g}">${label}<span class="nld-count">${items.length}</span></div>`);
			items.forEach((c) => {
				const html = this.cardInner(c);
				this.cardHtml.set(c.key, html);
				lists[col].push(
					`<div class="nld-card${this.selected === c.key ? " selected" : ""}" data-key="${this.esc(c.key)}" style="--c:${color}" data-search="${this.esc(this.cardSearch(c))}">${html}</div>`,
				);
			});
		});
		const $active = this.$root.find('.nld-list[data-col="active"]');
		const $standby = this.$root.find('.nld-list[data-col="standby"]');
		$active.html(lists.active.join("") || `<div class="nld-empty">${__("No vehicle is loading or on the road.")}</div>`);
		$standby.html(lists.standby.join("") || `<div class="nld-empty">${__("Nothing on standby.")}</div>`);
		this._layoutSignature = this.layoutSignature();
		this.applyFilters();
	}

	/** Changes when a card must move to another group (then the lists are rebuilt). */
	layoutSignature() {
		const parts = [];
		this.vehicles.forEach((v) => parts.push(`${v.name}:${this.groupForVehicle(v)}`));
		this.orphanEntities().forEach((i) => parts.push(`ent:${i.id}`));
		return parts.sort().join("|");
	}

	cardSortKey(c) {
		return c.vehicle ? (c.vehicle.plate || c.vehicle.name || "").toUpperCase() : (c.entity.driver_name || c.entity.id || "");
	}

	cardSearch(c) {
		if (c.entity) return `${c.entity.driver_name || ""} ${c.entity.driver || ""} ${c.entity.device_id || ""}`.toLowerCase();
		const v = c.vehicle;
		const trip = this.trips.get(v.name);
		return `${v.name} ${v.plate || ""} ${v.driver_name || ""} ${v.driver || ""} ${trip ? trip.name : ""}`.toLowerCase();
	}

	connText(item) {
		if (!item) return { cls: "none", text: __("No phone seen yet") };
		const ago = NexusMaps.timeAgo(item.last_seen);
		if (item.status === "Live") return { cls: "live", text: __("Live") };
		if (item.status === "On Stand-by") return { cls: "standby", text: __("On Stand-by · last ping {0}", [ago]) };
		return { cls: "offline", text: item.signed_out ? __("Offline · signed out {0}", [ago]) : __("Offline · last seen {0}", [ago]) };
	}

	cardInner(c) {
		if (c.entity) {
			const item = c.entity;
			const conn = this.connText(item);
			return `
				<div class="nld-card-top"><span class="nld-plate">${this.esc(item.driver_name || item.driver || __("Unassigned phone"))}</span>
					<span class="nld-speed">${item.status === "Live" ? `${Math.round((item.spd || 0) * 3.6)} km/h` : ""}</span></div>
				<div class="nld-meta"><i class="fa fa-mobile"></i> ${this.esc(item.device_id || item.id)}${item.app_version ? ` · v${this.esc(item.app_version)}` : ""}</div>
				<div class="nld-conn-row"><span class="nld-sdot ${conn.cls}"></span>${this.esc(conn.text)}${item.bat != null ? ` · 🔋 ${Math.round(item.bat)}%` : ""}</div>`;
		}
		const v = c.vehicle;
		const trip = this.trips.get(v.name);
		const item = this.entityForVehicle(v.name);
		const conn = this.connText(item);
		const group = this.groupForVehicle(v);
		const label = NLD_GROUPS[group][0];
		const driver = (item && item.driver_signed_in && (item.driver_name || item.driver)) || v.driver_name || v.driver || __("Unassigned");
		let tripHtml = `<div class="nld-meta" style="opacity:.7"><i class="fa fa-file-text-o"></i> ${__("No active trip")}</div>`;
		let actions = "";
		if (trip) {
			const done = trip.stops_total - trip.stops_pending;
			const pct = trip.stops_total ? Math.round((done / trip.stops_total) * 100) : 0;
			tripHtml = `
				<div class="nld-meta"><i class="fa fa-file-text-o"></i> ${this.esc(trip.name)} · ${done}/${trip.stops_total} ${__("stops")}</div>
				<div class="nld-progress"><div style="width:${pct}%"></div></div>`;
			const onRoad = trip.trip_status === "Dispatched" || trip.trip_status === "Returning";
			actions = `<div class="nld-actions">
				<button class="btn btn-xs btn-default" data-act="route" data-vehicle="${this.esc(v.name)}"><i class="fa fa-map-signs"></i> ${__("Route")}</button>
				${onRoad && trip.stops_pending ? `<button class="btn btn-xs btn-default" data-act="eta" data-vehicle="${this.esc(v.name)}"><i class="fa fa-clock-o"></i> ${__("ETA")}</button>` : ""}
				<button class="btn btn-xs btn-default" data-act="open" data-manifest="${this.esc(trip.name)}"><i class="fa fa-external-link"></i> ${__("Open")}</button>
			</div>`;
		}
		return `
			<div class="nld-card-top"><span class="nld-plate">${this.esc(v.plate || v.name)}</span>
				<span class="nld-speed">${item && item.status === "Live" ? `${Math.round((item.spd || 0) * 3.6)} km/h` : ""}</span></div>
			<span class="nld-badge">● ${this.esc(label)}</span>
			<div class="nld-meta"><i class="fa fa-user"></i> ${this.esc(driver)}</div>
			${tripHtml}
			<div class="nld-conn-row"><span class="nld-sdot ${conn.cls}"></span>${this.esc(conn.text)}${item && item.bat != null ? ` · 🔋 ${Math.round(item.bat)}%` : ""}</div>
			${actions}`;
	}

	/** Rewrites only the cards whose content changed. */
	renderAllCards() {
		if (!this.boardLoaded) return;
		if (this.layoutSignature() !== this._layoutSignature) {
			this.layoutCards();
			return;
		}
		this.vehicles.forEach((v) => this.renderCard({ key: `veh:${v.name}`, vehicle: v }));
		this.orphanEntities().forEach((item) => this.renderCard({ key: `ent:${item.id}`, entity: item }));
	}

	renderCard(c) {
		const html = this.cardInner(c);
		if (this.cardHtml.get(c.key) === html) return;
		this.cardHtml.set(c.key, html);
		this.$root.find(`.nld-card[data-key="${CSS.escape(c.key)}"]`).html(html);
	}

	applyFilters() {
		["active", "standby"].forEach((col) => {
			const q = this.filters[col];
			const $list = this.$root.find(`.nld-list[data-col="${col}"]`);
			$list.find(".nld-card").each(function () {
				$(this).toggle(!q || ($(this).attr("data-search") || "").includes(q));
			});
		});
	}

	selectCard(key, focus) {
		this.selected = key;
		this.$root.find(".nld-card").removeClass("selected");
		this.$root.find(`.nld-card[data-key="${CSS.escape(key)}"]`).addClass("selected");
		if (!focus || !this.view) return;
		const item = key.startsWith("ent:") ? this.entities.get(key.slice(4)) : this.entityForVehicle(key.slice(4));
		if (item && NexusMaps.validPoint(item.lat, item.lng)) {
			this.view.flyTo(item.lat, item.lng, 15);
			this.showEntityPopup(item);
		} else if (key.startsWith("veh:") && this.trips.get(key.slice(4))) {
			this.openRoute(key.slice(4));
		} else {
			frappe.show_alert({ message: __("No position for this vehicle yet."), indicator: "orange" });
		}
	}

	// ------------------------------------------------------------- live data
	applySnapshot(items) {
		const seen = new Set();
		items.forEach((item) => {
			seen.add(item.id);
			this.upsertEntity(item, true);
		});
		[...this.entities.keys()].forEach((id) => !seen.has(id) && this.removeEntity(id));
		this.renderAllCards();
	}

	applyDelta(items) {
		let touchedLayout = false;
		items.forEach((item) => {
			if (item.removed) {
				this.removeEntity(item.id);
				touchedLayout = true;
			} else {
				const before = this.entities.get(item.id);
				if (!before || before.vehicle !== item.vehicle) touchedLayout = true;
				this.upsertEntity(item, false);
			}
		});
		if (touchedLayout) this.renderAllCards();
		else {
			const keys = new Set();
			items.forEach((item) => {
				if (item.vehicle && this.vehicles.has(item.vehicle)) keys.add(`veh:${item.vehicle}`);
				else keys.add(`ent:${item.id}`);
			});
			keys.forEach((key) => {
				if (key.startsWith("veh:")) this.renderCard({ key, vehicle: this.vehicles.get(key.slice(4)) });
				else if (this.entities.get(key.slice(4))) this.renderCard({ key, entity: this.entities.get(key.slice(4)) });
			});
		}
	}

	upsertEntity(item, instant) {
		const before = this.entities.get(item.id);
		this.entities.set(item.id, item);
		this.renderPin(item, before, instant);
	}

	removeEntity(id) {
		this.entities.delete(id);
		this.pinLook.delete(id);
		if (this.view) this.view.removeMarker(id);
	}

	tripColor(item) {
		const trip = item.vehicle ? this.trips.get(item.vehicle) : null;
		return (trip && NLD_GROUPS[NLD_TRIP_GROUP[trip.trip_status]] && NLD_GROUPS[NLD_TRIP_GROUP[trip.trip_status]][1]) || "#94a3b8";
	}

	pinLabel(item) {
		const v = item.vehicle && this.vehicles.get(item.vehicle);
		return (v && (v.plate || v.name)) || item.vehicle_plate || (item.vehicle !== "Idle" && item.vehicle) || item.driver_name || "";
	}

	pinElement(item, color, cls, label) {
		const el = document.createElement("div");
		el.className = `nld-pin ${cls}`;
		el.style.setProperty("--c", color);
		el.innerHTML = `<div class="ring"></div>
			<div class="arrow" style="transform:rotate(${Math.round(item.hdg || 0)}deg)">
				<svg width="14" height="14" viewBox="0 0 24 24"><path d="M12 2L21 21L12 16.5L3 21Z" fill="${color}"/></svg>
			</div>
			${label ? `<div class="tag">${this.esc(label)}</div>` : ""}`;
		return el;
	}

	renderPin(item, before, instant) {
		if (!this.view) return;
		if (!NexusMaps.validPoint(item.lat, item.lng)) return;
		const cls = item.status === "Live" ? "live" : item.status === "On Stand-by" ? "standby" : "offline";
		const hidden = cls === "offline" && !this.showOffline;
		const color = this.tripColor(item);
		const label = this.pinLabel(item);
		const look = `${cls}|${color}|${label}`;
		const zIndex = cls === "live" ? 300 : cls === "standby" ? 200 : 100;

		if (!this.view.hasMarker(item.id) || this.pinLook.get(item.id) !== look) {
			this.pinLook.set(item.id, look);
			this.view.setMarker(item.id, {
				lat: item.lat,
				lng: item.lng,
				element: this.pinElement(item, color, cls, label),
				anchor: "center",
				zIndex,
				hidden,
				onClick: () => this.showEntityPopup(this.entities.get(item.id)),
			});
			return;
		}
		this.view.updateMarker(item.id, { hidden, zIndex });
		const spec = this.view.markers.get(item.id);
		if (spec && spec.element) {
			const arrow = spec.element.querySelector(".arrow");
			if (arrow) arrow.style.transform = `rotate(${Math.round(item.hdg || 0)}deg)`;
		}
		if (!before || before.lat !== item.lat || before.lng !== item.lng) {
			if (instant) this.view.updateMarker(item.id, { lat: item.lat, lng: item.lng });
			else this.view.animateMarker(item.id, item.lat, item.lng, 1200);
		}
	}

	showEntityPopup(item) {
		if (!item || !this.view) return;
		const conn = this.connText(item);
		const v = item.vehicle && this.vehicles.get(item.vehicle);
		const trip = v ? this.trips.get(v.name) : null;
		const el = document.createElement("div");
		el.className = "nld-pop";
		el.innerHTML = `
			<div class="nld-pop-title">${this.esc(this.pinLabel(item) || __("Phone"))}</div>
			<div>${this.esc(item.driver_name || item.driver || __("No driver signed in"))}</div>
			<div><span class="nld-sdot ${conn.cls}" style="display:inline-block;width:8px;height:8px;border-radius:50%;margin-right:5px"></span>${this.esc(conn.text)}</div>
			${item.status === "Live" ? `<div>${__("Speed")}: <b>${Math.round((item.spd || 0) * 3.6)} km/h</b></div>` : ""}
			${item.acc ? `<div>${__("GPS accuracy")}: ±${Math.round(item.acc)} m</div>` : ""}
			${item.bat != null ? `<div>${__("Battery")}: ${Math.round(item.bat)}%${item.chg ? " ⚡" : ""}</div>` : ""}
			${trip ? `<div>${__("Trip")}: <b>${this.esc(trip.name)}</b> (${this.esc(trip.trip_status)})</div>` : ""}
			${trip ? `<div class="nld-actions">
				<button class="btn btn-xs btn-default" data-act="route" data-vehicle="${this.esc(v.name)}">${__("Route")}</button>
				${trip.stops_pending && (trip.trip_status === "Dispatched" || trip.trip_status === "Returning") ? `<button class="btn btn-xs btn-default" data-act="eta" data-vehicle="${this.esc(v.name)}">${__("ETA")}</button>` : ""}
				<button class="btn btn-xs btn-default" data-act="open" data-manifest="${this.esc(trip.name)}">${__("Open")}</button>
			</div>` : ""}`;
		// Buttons in the popup live outside the page root (map engine DOM): wire them directly.
		el.querySelectorAll("[data-act]").forEach((btn) =>
			btn.addEventListener("click", (e) => {
				e.preventDefault();
				e.stopPropagation();
				const act = btn.getAttribute("data-act");
				if (act === "route") this.openRoute(btn.getAttribute("data-vehicle"));
				else if (act === "eta") this.openRoute(btn.getAttribute("data-vehicle"), { withEta: true });
				else frappe.set_route("Form", "Vehicle Delivery Manifest", btn.getAttribute("data-manifest"));
			}),
		);
		this.view.showPopup(el, item.lat, item.lng, { offset: 20 });
	}

	// ------------------------------------------------------------- route view
	clearRouteLayers() {
		if (!this.view) return;
		this.view.markerIds().filter((id) => id.startsWith("stop:")).forEach((id) => this.view.removeMarker(id));
		this.view.lineIds().filter((id) => id.startsWith("planned:") || id === "eta").forEach((id) => this.view.removeLine(id));
	}

	closeRoute() {
		this.routeView = null;
		this.clearRouteLayers();
		this.$root.find(".nld-panel").hide().empty();
	}

	async openRoute(vehicle, options) {
		options = options || {};
		const trip = this.trips.get(vehicle);
		if (!trip || !this.view) return;
		this.selectCard(`veh:${vehicle}`, false);
		let data;
		try {
			const r = await frappe.call({ method: NLD_METHODS.manifestMap, args: { manifest: trip.name }, type: "GET" });
			data = r.message;
		} catch (e) {
			return;
		}
		if (!data) return;
		const keepEta = options.keepEta && this.routeView && this.routeView.manifest === trip.name ? this.routeView.eta : null;
		this.clearRouteLayers();
		this.routeView = { manifest: trip.name, vehicle, data, eta: keepEta };

		const points = [];
		NexusMaps.geojsonLines(data.route_geojson).forEach((line, i) => {
			this.view.setLine(`planned:${i}`, line, { color: "#94a3b8", width: 4, opacity: 0.85, dash: [8, 6], zIndex: 1 });
			line.forEach((p) => points.push(p));
		});
		data.stops.forEach((s) => {
			if (s.lat == null) return;
			const el = document.createElement("div");
			el.className = "nld-stop";
			el.style.background = NLD_STOP_COLORS[s.status] || NLD_STOP_COLORS.Pending;
			el.textContent = s.idx;
			this.view.setMarker(`stop:${s.name}`, {
				lat: s.lat,
				lng: s.lng,
				element: el,
				anchor: "center",
				zIndex: s.status === "Pending" ? 150 : 120,
				onClick: () => this.showStopPopup(s),
			});
			points.push([s.lat, s.lng]);
		});
		const truck = this.entityForVehicle(vehicle);
		if (truck && NexusMaps.validPoint(truck.lat, truck.lng)) points.push([truck.lat, truck.lng]);
		if (!options.keepCamera) {
			if (points.length) this.view.fitPoints(points, { padding: 70 });
			else frappe.show_alert({ message: __("This trip has no planned route or stop locations yet."), indicator: "orange" });
		}
		if (keepEta) this.drawEta(keepEta);
		this.renderRoutePanel();
		if (options.withEta) this.loadEta();
	}

	showStopPopup(s) {
		const el = document.createElement("div");
		el.className = "nld-pop";
		const dropoff =
			s.status !== "Pending" && s.dropoff_distance_m != null
				? `<div>${__("Drop-off")}: ${NexusMaps.formatDistance(s.dropoff_distance_m)} ${__("from pin")} (${s.on_site ? __("on site") : __("off site")})</div>`
				: s.status !== "Pending" && s.dropoff_gps_status === "Unavailable"
					? `<div>${__("Drop-off")}: ${__("no GPS")}</div>`
					: "";
		el.innerHTML = `
			<div class="nld-pop-title">${s.idx}. ${this.esc(s.customer || s.sales_order)}</div>
			<div style="color:${NLD_STOP_COLORS[s.status] || "#334155"};font-weight:800">● ${this.esc(s.status)}</div>
			<div>${this.esc(s.sales_order)} · ${format_currency(s.grand_total || 0)}</div>
			${dropoff}
			${s.completion_time ? `<div>${__("Confirmed")}: ${this.esc(frappe.datetime.str_to_user(s.completion_time))}</div>` : ""}`;
		this.view.showPopup(el, s.lat, s.lng, { offset: 16 });
	}

	async loadEta() {
		const rv = this.routeView;
		if (!rv) return;
		const truck = this.entityForVehicle(rv.vehicle);
		if (!truck || !NexusMaps.validPoint(truck.lat, truck.lng)) {
			frappe.show_alert({ message: __("No live position for this truck yet."), indicator: "orange" });
			return;
		}
		if (truck.status === "Offline") {
			frappe.show_alert({ message: __("This truck is offline; the ETA starts from its last ping ({0}).", [NexusMaps.timeAgo(truck.last_seen)]), indicator: "orange" }, 8);
		}
		rv.etaLoading = true;
		this.renderRoutePanel();
		let res;
		try {
			const r = await frappe.call({ method: NLD_METHODS.eta, args: { manifest: rv.manifest, latitude: truck.lat, longitude: truck.lng } });
			res = r.message || {};
		} catch (e) {
			res = { status: "error", message: __("The ETA couldn't be calculated.") };
		}
		if (this.routeView !== rv) return;
		rv.etaLoading = false;
		if (res.status !== "success") {
			rv.etaError = res.message || __("The ETA couldn't be calculated.");
			this.renderRoutePanel();
			return;
		}
		rv.etaError = null;
		rv.eta = { ...res, computedAt: Date.now(), from: [truck.lat, truck.lng], fromStatus: truck.status };
		this.drawEta(rv.eta);
		this.renderRoutePanel();
	}

	drawEta(eta) {
		const line = NexusMaps.decodePolyline(eta.polyline, 5);
		this.view.setLine("eta", line, { color: "#2563eb", width: 5, opacity: 0.95, zIndex: 3 });
		this.view.fitPoints(line, { padding: 70 });
	}

	renderRoutePanel() {
		const rv = this.routeView;
		const $p = this.$root.find(".nld-panel");
		if (!rv) {
			$p.hide().empty();
			return;
		}
		const d = rv.data;
		const pending = d.stops.filter((s) => s.status === "Pending").length;
		const noPin = d.stops.filter((s) => s.lat == null).length;
		let etaHtml = "";
		if (rv.etaLoading) {
			etaHtml = `<div class="nld-note"><i class="fa fa-spinner fa-spin"></i> ${__("Calculating the road route…")}</div>`;
		} else if (rv.etaError) {
			etaHtml = `<div class="nld-note" style="color:#b91c1c">${this.esc(rv.etaError)}</div>`;
		} else if (rv.eta) {
			const eta = rv.eta;
			let cumulative = 0;
			const rows = eta.waypoints
				.map((w, i) => {
					const leg = eta.legs[i] || {};
					cumulative += Number(leg.duration_s) || 0;
					const at = new Date(eta.computedAt + cumulative * 1000);
					return `<div class="nld-eta-row"><span>${w.kind === "yard" ? "🏁 " : ""}<b>${this.esc(w.label)}</b>
						<span style="color:#94a3b8"> · ${NexusMaps.formatDistance(leg.distance_m)}</span></span>
						<span class="nld-eta-time">${at.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}</span></div>`;
				})
				.join("");
			etaHtml = `
				<div style="margin:8px 0 4px"><b>${NexusMaps.formatDistance(eta.distance_m)}</b> · <b>${NexusMaps.formatDuration(eta.duration_s)}</b> ${__("driving")}
					<span style="color:#94a3b8">· ${__("calculated {0}", [new Date(eta.computedAt).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })])}</span></div>
				${rows}
				<div class="nld-note">${__("Arrival times are driving time from the truck's position; time spent at each stop is not included.")}</div>
				${eta.skipped_without_pin && eta.skipped_without_pin.length ? `<div class="nld-note">${__("Not routed (no saved location)")}: ${this.esc(eta.skipped_without_pin.join(", "))}</div>` : ""}
				${eta.truncated ? `<div class="nld-note">${__("Only the next 24 stops are routed.")}</div>` : ""}`;
		}
		const onRoad = d.trip_status === "Dispatched" || d.trip_status === "Returning";
		$p.html(`
			<div class="nld-panel-head">
				<div><div class="nld-panel-title">${this.esc(d.name)} · ${this.esc((this.vehicles.get(d.vehicle) || {}).plate || d.vehicle)}</div>
				<div class="nld-panel-sub">${this.esc(d.trip_status)} · ${d.stops.length - pending}/${d.stops.length} ${__("stops done")}${noPin ? ` · ${noPin} ${__("without location")}` : ""}</div></div>
				<button class="nld-x" data-act="close-route" title="${__("Close")}"><i class="fa fa-times"></i></button>
			</div>
			${etaHtml}
			<div class="nld-actions">
				${onRoad && pending ? `<button class="btn btn-xs btn-primary" data-act="refresh-eta"><i class="fa fa-clock-o"></i> ${rv.eta ? __("Refresh ETA") : __("ETA")}</button>` : ""}
				<button class="btn btn-xs btn-default" data-act="open" data-manifest="${this.esc(d.name)}"><i class="fa fa-external-link"></i> ${__("Open trip")}</button>
			</div>`).show();
	}
}
