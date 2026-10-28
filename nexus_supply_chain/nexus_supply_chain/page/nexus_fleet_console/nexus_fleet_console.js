// apps/nexus_supply_chain/nexus_supply_chain/nexus_supply_chain/page/nexus_fleet_console/nexus_fleet_console.js
//
// Nexus Fleet Console (System Manager / whoever may edit Nexus App Settings).
//   Phones          every phone registered with FastAPI; Revoke a lost one
//   System health   FastAPI, Redis, ERPNext link, routing server, sweep, counts
//   Legacy traffic  calls to the old pre-1.1.0 paths per day: switch a group
//                   off in FastAPI's .env only after 7 days at zero
frappe.pages["nexus_fleet_console"].on_page_load = function (wrapper) {
	const page = frappe.ui.make_app_page({ parent: wrapper, title: __("Nexus Fleet Console"), single_column: true });
	wrapper.nexus_fleet_console = new NexusFleetConsole(page, wrapper);
};

frappe.pages["nexus_fleet_console"].on_page_show = function (wrapper) {
	if (wrapper.nexus_fleet_console) wrapper.nexus_fleet_console.refresh();
};

const NFC_METHOD = "nexus_supply_chain.nexus_supply_chain.page.nexus_fleet_console.nexus_fleet_console";

class NexusFleetConsole {
	constructor(page, wrapper) {
		this.page = page;
		this.$root = $(wrapper).find(".layout-main-section");
		this.tab = "phones";
		this.filter = "";
		this.phones = [];
		page.set_primary_action(__("Refresh"), () => this.refresh(), "refresh");
		this.render();
		this.bind();
	}

	esc(v) {
		return frappe.utils.escape_html(v == null ? "" : String(v));
	}

	when(ts) {
		if (!ts) return "—";
		const d = new Date(ts * 1000);
		return `${d.toLocaleDateString()} ${d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}`;
	}

	render() {
		this.$root.html(`
			<div class="nfc">
				<div class="nfc-tabs">
					<button class="nfc-tab" data-tab="phones">${__("Phones")}</button>
					<button class="nfc-tab" data-tab="health">${__("System health")}</button>
					<button class="nfc-tab" data-tab="legacy">${__("Legacy traffic")}</button>
				</div>
				<div class="nfc-body"></div>
			</div>`);
		if (!document.getElementById("nfc-css")) {
			$("head").append(`<style id="nfc-css">
				.nfc{margin:12px 0}
				.nfc-tabs{display:flex;gap:6px;border-bottom:1px solid var(--border-color);margin-bottom:14px}
				.nfc-tab{border:0;background:none;padding:8px 14px;font-weight:700;color:var(--text-muted);border-bottom:3px solid transparent}
				.nfc-tab.active{color:var(--text-color);border-bottom-color:var(--primary)}
				.nfc-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(230px,1fr));gap:12px}
				.nfc-tile{border:1px solid var(--border-color);border-radius:10px;padding:12px 14px;background:var(--card-bg)}
				.nfc-tile h6{margin:0 0 6px;font-size:12px;text-transform:uppercase;letter-spacing:.06em;color:var(--text-muted)}
				.nfc-ok{color:#047857;font-weight:800}.nfc-bad{color:#b91c1c;font-weight:800}.nfc-warn{color:#b45309;font-weight:800}
				.nfc-table{width:100%;font-size:13px}
				.nfc-table th{font-size:11px;text-transform:uppercase;color:var(--text-muted);letter-spacing:.05em}
				.nfc-table td,.nfc-table th{padding:7px 8px;border-bottom:1px solid var(--border-color);vertical-align:top}
				.nfc-mono{font-family:ui-monospace,Menlo,Consolas,monospace;font-size:12px}
				.nfc-pill{display:inline-block;padding:1px 8px;border-radius:10px;font-size:11px;font-weight:800}
				.nfc-pill.on{background:#ecfdf5;color:#047857}.nfc-pill.off{background:#fef2f2;color:#b91c1c}
				.nfc-note{color:var(--text-muted);font-size:12px;margin:8px 0}
				.nfc-bar{display:flex;gap:2px;align-items:flex-end;height:26px}
				.nfc-bar span{width:9px;background:#93c5fd;border-radius:2px 2px 0 0}
				.nfc-bar span.zero{background:#e5e7eb;height:3px!important}
			</style>`);
		}
		this.paintTabs();
	}

	bind() {
		const self = this;
		this.$root.on("click", ".nfc-tab", function () {
			self.tab = $(this).data("tab");
			self.paintTabs();
			self.refresh();
		});
		this.$root.on("input", ".nfc-filter", function () {
			self.filter = ($(this).val() || "").toLowerCase();
			self.renderPhones();
		});
		this.$root.on("click", ".nfc-revoke", function () {
			self.revoke($(this).data("device"));
		});
	}

	paintTabs() {
		this.$root.find(".nfc-tab").each((_, el) => $(el).toggleClass("active", $(el).data("tab") === this.tab));
	}

	async call(method, args) {
		try {
			const r = await frappe.call({ method: `${NFC_METHOD}.${method}`, args, type: method === "revoke_phone" ? "POST" : "GET" });
			return r.message || {};
		} catch (e) {
			return { status: "error", message: __("The request failed.") };
		}
	}

	async refresh() {
		const $b = this.$root.find(".nfc-body").html(`<div class="nfc-note"><i class="fa fa-spinner fa-spin"></i> ${__("Loading…")}</div>`);
		if (this.tab === "phones") {
			const res = await this.call("get_phones");
			if (res.status !== "success") return $b.html(this.errorBox(res));
			this.phones = res.phones || [];
			this.renderPhones();
		} else if (this.tab === "health") {
			this.renderHealth(await this.call("get_health"));
		} else {
			this.renderLegacy(await this.call("get_legacy_usage", { days: 14 }));
		}
	}

	errorBox(res) {
		return `<div class="nfc-tile"><div class="nfc-bad">${this.esc(res.message || __("Unavailable"))}</div>
			${res.hint ? `<div class="nfc-note">${this.esc(res.hint)}</div>` : ""}</div>`;
	}

	// ── Phones ──
	renderPhones() {
		const q = this.filter;
		const rows = this.phones.filter((p) =>
			!q || [p.device_id, p.model, p.user, p.user_name, p.registered_by, p.last_vehicle, p.app_version].join(" ").toLowerCase().includes(q),
		);
		const active = this.phones.filter((p) => !p.revoked).length;
		const versions = {};
		this.phones.filter((p) => !p.revoked).forEach((p) => (versions[p.app_version || "?"] = (versions[p.app_version || "?"] || 0) + 1));
		const versionText = Object.entries(versions).map(([v, n]) => `${this.esc(v)}: ${n}`).join(" · ");
		this.$root.find(".nfc-body").html(`
			<div class="nfc-note">${__("{0} active phone(s)", [active])}${versionText ? ` · ${__("app versions")}: ${versionText}` : ""}</div>
			<input type="search" class="form-control input-sm nfc-filter" style="max-width:320px;margin-bottom:10px" placeholder="${__("Filter driver, model, vehicle…")}" value="${this.esc(this.filter)}">
			<div class="table-responsive"><table class="nfc-table">
				<thead><tr><th>${__("Phone")}</th><th>${__("Signed-in driver")}</th><th>${__("Last vehicle")}</th><th>${__("App")}</th><th>${__("Registered")}</th><th></th></tr></thead>
				<tbody>${rows.map((p) => `
					<tr style="${p.revoked ? "opacity:.5" : ""}">
						<td><b>${this.esc(p.model || __("Unknown model"))}</b><div class="nfc-mono">${this.esc(p.device_id)}</div><div class="nfc-note" style="margin:0">${this.esc(p.platform)}</div></td>
						<td>${p.user ? `${this.esc(p.user_name || p.user)}<div class="nfc-note" style="margin:0">${this.esc(p.user)} · ${__("since")} ${this.when(p.bound_at)}</div>` : `<span class="nfc-note">${__("No one signed in (vehicle's phone)")}</span>`}</td>
						<td>${this.esc(p.last_vehicle || "—")}</td>
						<td>${this.esc(p.app_version || "—")}</td>
						<td>${this.when(p.registered_at)}<div class="nfc-note" style="margin:0">${__("by")} ${this.esc(p.registered_by_name || p.registered_by || "—")}</div></td>
						<td>${p.revoked ? `<span class="nfc-pill off">${__("Revoked")}</span>` : `<button class="btn btn-xs btn-danger nfc-revoke" data-device="${this.esc(p.device_id)}">${__("Revoke")}</button>`}</td>
					</tr>`).join("") || `<tr><td colspan="6" class="nfc-note">${__("No phones registered yet. Phones appear here once a driver signs in on app 1.1.0+.")}</td></tr>`}
				</tbody></table></div>
			<div class="nfc-note">${__("Revoke a phone that is lost, stolen or replaced: its tracking key stops working at once and the app is signed out. Signing in on that phone again issues a new key.")}</div>`);
	}

	revoke(deviceId) {
		const phone = this.phones.find((p) => p.device_id === deviceId) || {};
		const d = new frappe.ui.Dialog({
			title: __("Revoke {0}?", [phone.model || deviceId]),
			fields: [
				{
					fieldtype: "HTML",
					options: `<div class="nfc-note">${__("Phone")}: <span class="nfc-mono">${this.esc(deviceId)}</span><br>${__("Signed-in driver")}: ${this.esc(phone.user_name || phone.user || __("none"))}</div>`,
				},
				{ fieldtype: "Small Text", fieldname: "reason", label: __("Reason (for the audit log)"), reqd: 1 },
				{ fieldtype: "Check", fieldname: "remove_pin", label: __("Also remove its pin from the live map"), description: __("Leave unticked to keep its last position as history.") },
			],
			primary_action_label: __("Revoke phone"),
			primary_action: async (values) => {
				d.get_primary_btn().prop("disabled", true);
				const res = await this.call("revoke_phone", { device_id: deviceId, reason: values.reason, remove_pin: values.remove_pin ? 1 : 0 });
				d.get_primary_btn().prop("disabled", false);
				if (res.status !== "success") {
					frappe.msgprint({ title: __("Not revoked"), message: this.esc(res.message || ""), indicator: "red" });
					return;
				}
				d.hide();
				frappe.show_alert({ message: __("Phone revoked."), indicator: "green" });
				this.refresh();
			},
		});
		d.show();
	}

	// ── System health ──
	renderHealth(res) {
		const e = res.erpnext || {};
		const yes = (v, okText, badText) => (v ? `<span class="nfc-ok">${okText}</span>` : `<span class="nfc-bad">${badText}</span>`);
		const erpTile = `
			<div class="nfc-tile"><h6>${__("This ERPNext")}</h6>
				<div>${__("FastAPI")}: <span class="nfc-mono">${this.esc(e.fastapi_url || "")}</span></div>
				<div>${__("Internal secret")}: ${yes(e.secret_configured, __("set"), __("missing"))}</div>
				<div>${__("Pushes to FastAPI")}: ${e.pushes_enabled ? `<span class="nfc-ok">${__("on")}</span>` : `<span class="nfc-warn">${__("off (test copy)")}</span>`}</div>
				<div>${__("Scheduler")}: ${yes(e.scheduler_enabled, __("running"), __("disabled"))}</div>
				<div>${__("Round trip")}: ${this.esc(e.round_trip_ms)} ms</div>
			</div>`;
		if (res.status !== "success") {
			this.$root.find(".nfc-body").html(`<div class="nfc-grid">${erpTile}${this.errorBox(res)}</div>`);
			return;
		}
		const f = res.fastapi || {};
		const part = (label, p) => `
			<div class="nfc-tile"><h6>${label}</h6>
				<div>${yes(p && p.ok, __("OK"), __("Down"))}${p && p.ms != null ? ` · ${p.ms} ms` : ""}${p && p.http ? ` · HTTP ${p.http}` : ""}</div>
				${p && p.error ? `<div class="nfc-note">${this.esc(p.error)}</div>` : ""}
			</div>`;
		const fleet = f.fleet || {};
		const counts = fleet.counts || {};
		const countText = Object.entries(counts).filter(([k]) => k !== "total").map(([k, v]) => `${this.esc(k)}: ${v}`).join("<br>");
		const legacy = f.legacy || {};
		this.$root.find(".nfc-body").html(`
			<div class="nfc-note">${__("FastAPI")} ${this.esc(f.version || "")} · ${__("overall")}: ${f.status === "ok" ? `<span class="nfc-ok">OK</span>` : `<span class="nfc-bad">${this.esc(f.status || "?")}</span>`}</div>
			<div class="nfc-grid">
				${erpTile}
				${part(__("Redis (tracking store)"), f.redis)}
				${part(__("FastAPI → ERPNext"), f.erpnext)}
				${part(__("Routing server (ORS)"), f.ors)}
				<div class="nfc-tile"><h6>${__("Tracking engine")}</h6>
					<div>${__("Last status sweep")}: ${fleet.sweep_age_s != null ? `${this.esc(fleet.sweep_age_s)} s ${__("ago")}` : "—"} ${fleet.sweep_age_s > 60 ? `<span class="nfc-bad">${__("stale")}</span>` : ""}</div>
					<div>${__("Trip data age")}: ${fleet.context_age_s != null ? `${this.esc(Math.round(fleet.context_age_s))} s` : "—"}</div>
					<div>${__("Stand-by after")}: ${this.esc((fleet.settings || {}).standby_threshold_seconds)} s · ${__("Offline after")}: ${this.esc((fleet.settings || {}).offline_threshold_minutes)} min</div>
				</div>
				<div class="nfc-tile"><h6>${__("Tracked now")} (${this.esc(counts.total || 0)})</h6><div class="nfc-note" style="margin:0">${countText || "—"}</div></div>
				<div class="nfc-tile"><h6>${__("Live connections (one worker)")}</h6>
					<div>${__("Dispatch pages")}: ${this.esc((f.sockets_this_worker || {}).dispatch_clients)}</div>
					<div>${__("Driver phones")}: ${this.esc((f.sockets_this_worker || {}).device_clients)}</div>
					<div>${__("Event listener")}: ${yes((f.sockets_this_worker || {}).listener_ok, __("OK"), __("down"))}</div>
				</div>
				<div class="nfc-tile"><h6>${__("Legacy switches")}</h6>
					<div>${__("Old driver app API")}: <span class="nfc-pill ${legacy.driver_api === false ? "off" : "on"}">${legacy.driver_api === false ? __("off") : __("on")}</span></div>
					<div>${__("Old dispatch feeds")}: <span class="nfc-pill ${legacy.dispatch_feeds === false ? "off" : "on"}">${legacy.dispatch_feeds === false ? __("off") : __("on")}</span></div>
				</div>
			</div>`);
	}

	// ── Legacy traffic ──
	renderLegacy(res) {
		if (res.status !== "success") {
			this.$root.find(".nfc-body").html(this.errorBox(res));
			return;
		}
		const labels = {
			telemetry_driver_login: "/telemetry/driver-login",
			telemetry_ping: "/telemetry/ping",
			telemetry_driver_logout: "/telemetry/driver-logout",
			driver_vault_v1: "/api/v1/sync/driver-vault",
			fleet_log_fuel_v1: "/api/v1/fleet/log-fuel",
			ws_telemetry_fleet: "/telemetry/ws",
			ws_telemetry_sales: "/telemetry/sales-ws",
			telemetry_fleet_status: "/telemetry/fleet-status",
		};
		const groups = { driver_api: __("Old driver app (switch: NEXUS_LEGACY_DRIVER_API)"), dispatch_feeds: __("Old dispatch-page feeds (switch: NEXUS_LEGACY_DISPATCH_FEEDS)") };
		const days = [...(res.days || [])].reverse(); // oldest -> newest for the bars
		let html = "";
		Object.keys(groups).forEach((group) => {
			const on = (res.switches || {})[group] !== false;
			const paths = Object.entries(res.paths || {}).filter(([, p]) => p.group === group);
			const ready = paths.every(([, p]) => p.zero_days_streak >= 7);
			html += `<div class="nfc-tile" style="margin-bottom:12px"><h6>${groups[group]} · <span class="nfc-pill ${on ? "on" : "off"}">${on ? __("on") : __("off")}</span></h6>
				${on ? (ready
					? `<div class="nfc-ok">${__("No calls for 7+ days on every path: safe to switch off.")}</div>`
					: `<div class="nfc-warn">${__("Still in use: keep it on.")}</div>`) : `<div class="nfc-note">${__("Switched off: callers get 'update required'.")}</div>`}
				<table class="nfc-table" style="margin-top:8px"><thead><tr><th>${__("Path")}</th><th>${__("Last 14 days")}</th><th>${__("Total")}</th><th>${__("Days at zero")}</th><th>${__("Last call")}</th></tr></thead><tbody>
				${paths.map(([name, p]) => {
					const max = Math.max(1, ...days.map((d) => p.by_day[d] || 0));
					const bars = days.map((d) => `<span title="${this.esc(d)}: ${p.by_day[d] || 0}" class="${p.by_day[d] ? "" : "zero"}" style="height:${Math.max(3, Math.round(((p.by_day[d] || 0) / max) * 26))}px"></span>`).join("");
					return `<tr><td class="nfc-mono">${this.esc(labels[name] || name)}</td><td><div class="nfc-bar">${bars}</div></td>
						<td>${p.total}</td><td>${p.zero_days_streak}${p.zero_days_streak >= days.length ? "+" : ""}</td>
						<td>${p.last_at ? `${this.when(p.last_at)}<div class="nfc-note" style="margin:0">${this.esc(p.last_caller || "")}</div>` : "—"}</td></tr>`;
				}).join("")}
				</tbody></table></div>`;
		});
		html += `<div class="nfc-note">${__("To switch a group off: set its variable to off in FastAPI's .env and restart FastAPI. To undo, set it back to on.")}</div>`;
		this.$root.find(".nfc-body").html(html);
	}
}
