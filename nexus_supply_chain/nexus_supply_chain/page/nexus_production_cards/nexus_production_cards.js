// Nexus Production Command
// Displays the board from nexus_supply_chain.production.api.get_board, which the FastAPI
// engine calculates. This page never calculates: it only shows, filters and links.

frappe.pages["nexus_production_cards"].on_page_load = function (wrapper) {
	const page = frappe.ui.make_app_page({
		parent: wrapper,
		title: __("Nexus Production Command"),
		single_column: true,
	});
	wrapper.nexus_board = new nexus_production.Board(page, wrapper);
};

frappe.pages["nexus_production_cards"].on_page_show = function (wrapper) {
	if (wrapper.nexus_board) wrapper.nexus_board.on_show();
};

frappe.provide("nexus_production");

nexus_production.Board = class NexusProductionBoard {
	constructor(page, wrapper) {
		this.page = page;
		this.API = "nexus_supply_chain.production.api";
		this.ROUTE = "nexus_production_cards";
		this.LIVE_EVENT = "nexus_production_changed";
		this.MIN_GAP_MS = 20000;
		this.PREF_KEY = "Nexus Production Cards"; // per-user page preferences (Frappe user settings)
		this.state = {
			mode: this.remember("mode") || "confirmed",
			from: null, // null = the default rolling window from Settings
			to: null,
			tab: "bulk",
			open: null,
			q: "",
			show_covered: false,
			rm_sort: "value",
			group: this.remember("group_" + frappe.session.user) || "", // "" = All Item Groups
		};
		this.result = null;
		this.loading = false;
		this.next_at = null;
		this.server_offset = 0;
		this.loaded_at = 0;
		this.changed = null; // the last live signal not yet loaded
		// Pending tab: loaded on its own, only when the tab is opened
		this.pend = { data: null, loading: false, error: false, stale: false, loaded_at: 0, q: "", flagged_only: true };
		this.make();
		this.load_prefs();
		this.load(false);
		this.timer = setInterval(() => this.tick(), 1000);
		this.live_bind();
	}

	// ================================================================ helpers
	remember(key, value) {
		try {
			if (value === undefined) return localStorage.getItem("nexus_production_" + key);
			localStorage.setItem("nexus_production_" + key, value);
		} catch (e) {
			return null;
		}
	}
	esc(v) {
		return frappe.utils.escape_html(v === null || v === undefined ? "" : String(v));
	}
	n(v, dp = 0) {
		return Number(v || 0).toLocaleString("en-KE", { minimumFractionDigits: 0, maximumFractionDigits: dp });
	}
	kg(v) {
		const x = Number(v || 0);
		return this.n(x, Math.abs(x - Math.round(x)) < 0.005 ? 0 : 2);
	}
	minus(v, fmt) {
		return Number(v || 0) > 0 ? "−" + fmt(v) : fmt(0);
	}
	kes(v) {
		return "KES " + this.n(v, 0);
	}
	u(uom) {
		if (!uom) return "";
		return /^kgs?$/i.test(uom) ? "kg" : uom;
	}
	q(v, uom, dp) {
		const val = dp === undefined ? this.kg(v) : this.n(v, dp);
		return `${val} ${this.esc(this.u(uom))}`.trim();
	}
	now() {
		return new Date(Date.now() + this.server_offset);
	}
	age_h(at) {
		const t = at ? frappe.datetime.str_to_obj(at) : null;
		return t ? Math.max(0, (this.now() - t) / 36e5) : 0;
	}
	fmt_age(h) {
		return h >= 48 ? `${Math.floor(h / 24)}d ${Math.round(h % 24)}h` : `${Math.floor(h)}h ${Math.round((h % 1) * 60)}m`;
	}
	when(at) {
		return at ? frappe.datetime.str_to_user(at).replace(/:\d\d$/, "") : "";
	}
	link(doctype, name, label) {
		if (!name) return "";
		return `<a href="/app/${frappe.router.slug(doctype)}/${encodeURIComponent(name)}" target="_blank" rel="noopener">${this.esc(label || name)}</a>`;
	}
	so_line(r, qty, uom) {
		const q = qty === undefined || qty === null ? "" : ` · ${this.n(qty, 2)}${uom ? " " + this.esc(this.u(uom)) : ""}`;
		return `<span class="sol">${this.link("Sales Order", r.sales_order)}${q} <span class="age">${this.fmt_age(this.age_h(r.at))}</span>${r.urgent ? ' <span class="urg">URGENT</span>' : ""}${r.draft ? ' <span class="age">draft</span>' : ""}</span>`;
	}
	pri(p) {
		return "P" + (p === undefined || p === null ? 3 : p);
	}
	wo_btn(code, label, primary) {
		if (this.state.mode === "drafts") return "";
		return `<button class="btn ${primary ? "primary" : "sm wo"}" data-wo="${this.esc(code)}">${this.esc(label)}</button>`;
	}
	mr_btn(codes, label, primary) {
		if (this.state.mode === "drafts") return "";
		return `<button class="btn ${primary ? "primary" : "sm"}" data-mr="${this.esc(codes)}">${this.esc(label)}</button>`;
	}
	MAT_TABS() {
		return { rm: (m) => m.kind !== "Packaging", pack: (m) => m.kind === "Packaging" };
	}
	mats_for(tab) {
		return this.mats.filter(this.MAT_TABS()[tab]);
	}
	in_group(c) {
		const g = this.state.group;
		return !g || c.group === g || (c.also_groups || []).includes(g);
	}
	// ---------------------------------------------------------------- per-user preferences
	load_prefs() {
		// localStorage gives the last choice instantly; the server copy follows the user to any device
		frappe.call({
			method: "frappe.model.utils.user_settings.get",
			args: { doctype: this.PREF_KEY },
			callback: (r) => {
				let prefs = {};
				try { prefs = JSON.parse(r.message || "{}") || {}; } catch (e) { prefs = {}; }
				const g = typeof prefs.item_group === "string" ? prefs.item_group : null;
				if (g === null || g === this.state.group) return;
				this.state.group = g;
				this.remember("group_" + frappe.session.user, g);
				if (this.result) this.render_group_filter(true);
			},
		});
	}
	save_group(g) {
		this.state.group = g;
		this.remember("group_" + frappe.session.user, g);
		frappe.call({
			method: "frappe.model.utils.user_settings.save",
			args: { doctype: this.PREF_KEY, user_settings: JSON.stringify({ item_group: g }) },
		});
	}
	// ---------------------------------------------------------------- date quick choices
	ymd(d) {
		const pad = (x) => String(x).padStart(2, "0");
		return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
	}
	presets() {
		// "Last N days" counts exactly like the server's default window: today − N up to today
		const now = this.now(), t = this.ymd(now);
		const back = (days) => { const d = new Date(now); d.setDate(d.getDate() - days); return this.ymd(d); };
		const y = now.getFullYear(), mo = now.getMonth();
		return [
			{ k: "today", label: __("Today"), from: t, to: t },
			{ k: "7", label: __("Last 7 days"), from: back(7), to: t },
			{ k: "14", label: __("Last 14 days"), from: back(14), to: t },
			{ k: "30", label: __("Last 30 days"), from: back(30), to: t },
			{ k: "month", label: __("This month"), from: this.ymd(new Date(y, mo, 1)), to: t },
			{ k: "last_month", label: __("Last month"), from: this.ymd(new Date(y, mo - 1, 1)), to: this.ymd(new Date(y, mo, 0)) },
			{ k: "90", label: __("Last 90 days"), from: back(90), to: t },
		];
	}
	apply_preset(k) {
		if (k === "custom") return;
		const p = this.presets().find((x) => x.k === k);
		this.state.from = p ? p.from : null; // anything else = the default window from Settings
		this.state.to = p ? p.to : null;
		this.state.open = null;
		this.load(false);
	}
	paint_range() {
		const pg = (this.result || {}).page || {};
		const span = (a, b) => Math.round((Date.parse(b) - Date.parse(a)) / 864e5);
		const days = pg.default_from && pg.default_to ? span(pg.default_from, pg.default_to) : 0;
		const is_default = !this.state.from || (this.state.from === pg.default_from && this.state.to === pg.default_to);
		// the choice that equals the default window is shown once, as the default
		const list = this.presets().filter((x) => !(days && x.k === String(days)));
		const hit = is_default ? null : list.find((x) => x.from === this.state.from && x.to === this.state.to);
		const k = is_default ? "default" : hit ? hit.k : "custom";
		this.el.range.html(
			`<option value="default">${days ? __("Last {0} days (default)", [days]) : __("Default window")}</option>`
			+ list.map((x) => `<option value="${x.k}">${x.label}</option>`).join("")
			+ (k === "custom" ? `<option value="custom">${__("Custom dates")}</option>` : ""));
		this.el.range.val(k);
	}
	// ---------------------------------------------------------------- item group filter
	render_group_filter(rerender) {
		const groups = (this.result.board.groups || []).slice();
		const g = this.state.group;
		const missing = g && !groups.includes(g);
		this.el.group.html(`<option value="">${__("All Item Groups")}</option>`
			+ groups.map((x) => `<option value="${this.esc(x)}">${this.esc(x)}</option>`).join("")
			+ (missing ? `<option value="${this.esc(g)}">${this.esc(g)} (${__("nothing today")})</option>` : ""));
		this.el.group.val(g);
		this.el.group.toggleClass("active", !!g);
		if (rerender) {
			this.render_tabs();
			this.render_content();
		}
	}

    	tab_of(c) {
		// every card lives on exactly one tab
		if (!c) return null;
		if (c.cook !== undefined) return c.is_stainer ? "paste" : "bulk";
		return c.tab === "tinting" ? "tint" : "fg";
	}
	feeds_chip(b) {
		const groups = [b.group].concat(b.also_groups || []).filter((g) => g && g !== "Other");
		if (!groups.length) return "";
		const many = groups.length > 1;
		const tip = many
			? __("This bulk is filled into more than one Item Group. If that is not intended, see Needs Setup for the BOMs involved.")
			: __("Item Groups of the finished goods this bulk is used for");
		return `<span class="chip ${many ? "c-amber" : "c-grey"}" title="${tip}">${__("Feeds")}: ${this.esc(groups.join(", "))}</span>`;
	}
	ordered(card) {
		// units on Sales Orders for this FG itself (tints that use it as a base are not orders for it)
		return ((card && card.rows) || []).filter((r) => r.sales_order && !r.via).reduce((a, r) => a + (r.qty || 0), 0);
	}
    
	wo_on() {
		const s = ((this.result || {}).board || {}).settings_used || {};
		return s.include_work_orders === undefined || !!s.include_work_orders;
	}
	po_on() {
		const s = ((this.result || {}).board || {}).settings_used || {};
		return s.include_purchase_orders === undefined || !!s.include_purchase_orders;
	}
	free(st) {
		// never show negative stock: a negative free figure is shown as 0 plus the extra the Work Orders need
		return Math.max(0, Number((st || {}).free || 0));
	}
	neg_chip(st) {
		return st && st.negative_stock ? `<span class="chip c-amber" title="${__("A Bin below zero counts as 0 on the board. Post the missing receipt or stock reconciliation.")}">⚠ ${__("Negative stock in ERPNext")}</span>` : "";
	}
	route_chip(route) {
		if (!route) return "";
		const cls = /Tint from Bulk/.test(route) ? "c-cyan" : /Packed Base/.test(route) ? "c-blue" : /Stainer|Colourant|Blend/.test(route) ? "c-violet"
			: /Needs Setup/.test(route) ? "c-red" : "c-grey";
		return `<span class="chip ${cls}">${this.esc(route)}</span>`;
	}

	// ================================================================ layout
	make() {
		this.$root = $(`
			<div class="npc">
				<div class="topbar">
					<div class="sync"><span class="dot" data-el="dot"></span><span data-el="live">${__("Loading")}</span></div>
					<div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap">
						<button class="btn" data-action="refresh">${__("Refresh Now")}</button>
						<button class="btn" data-action="settings">⚙ ${__("Settings")}</button>
					</div>
				</div>
				<div class="filters">
					<label>${__("Orders confirmed between")} <input type="date" class="inp" data-el="from"></label>
					<label>${__("and")} <input type="date" class="inp" data-el="to"></label>
					<select class="sel" data-el="range" aria-label="${__("Quick date range")}"></select>
					<div class="seg" role="group" aria-label="${__("Orders")}">
						<button data-mode="confirmed">${__("Confirmed")}</button>
						<button data-mode="drafts">${__("+ Drafts")}</button>
					</div>
				</div>
				<div data-el="banner"></div>
				<div class="summary" data-el="summary"></div>
				<div class="tabs" data-el="tabs" role="tablist"></div>
				<div class="tabdesc" data-el="tabdesc"></div>
				<div class="toolbar">
					<input class="search" type="search" data-el="search" placeholder="${__("Search item code, name, sales order, raw material")}" aria-label="${__("Search")}">
					<select class="sel" data-el="group" aria-label="${__("Item Group")}"><option value="">${__("All Item Groups")}</option></select>
					<label class="toggle"><input type="checkbox" data-el="covered"> <span>${__("Show covered (P3)")}</span></label>
					<select class="sel" data-el="rmsort" style="display:none" aria-label="${__("Sort")}">
						<option value="value">${__("Sort: Shortfall value (KES)")}</option>
						<option value="risk">${__("Sort: Sales value at risk")}</option>
						<option value="pct">${__("Sort: % of requirement short")}</option>
					</select>
				</div>
				<div class="legend" data-el="legend">
					<span class="pri P0">P0</span> ${__("Urgent order short")}
					<span class="pri P1">P1</span> ${__("Order short · oldest confirmed first")}
					<span class="pri P2">P2</span> ${__("Shelf refill / batch top-up only")}
					<span class="pri P3">P3</span> ${__("Covered by stock / Work Orders")}
					<span style="margin-left:10px" class="t1">${__("Tier 1")}</span> ${__("Orders")}
					<span class="t2">${__("Tier 2")}</span> ${__("Shelf refill")}
					<span class="t3">${__("Tier 3")}</span> ${__("Excess from min batch")}
				</div>
				<div data-el="content"><div class="skeleton">${__("Calculating the production board...")}</div></div>
				<div class="npc-overlay" data-el="overlay" data-action="close-drawer"></div>
				<aside class="npc-drawer" data-el="drawer" aria-label="${__("Production settings")}"></aside>
			</div>`).appendTo(this.page.main);
		this.el = {};
		this.$root.find("[data-el]").each((_, node) => (this.el[node.dataset.el] = $(node)));
		this.paint_mode();
		this.bind();
	}

	bind() {
		const on = (sel, fn) => this.$root.on("click", sel, (e) => fn(e, e.currentTarget));
		this.el.search.on("input", frappe.utils.debounce((e) => {
			this.state.q = (e.target.value || "").trim().toLowerCase();
			this.state.open = null;
			this.render_content();
		}, 200));
		this.el.covered.on("change", (e) => {
			this.state.show_covered = e.target.checked;
			this.render_tabs();
			this.render_content();
		});
		this.el.rmsort.on("change", (e) => {
			this.state.rm_sort = e.target.value;
			this.render_content();
		});
		const date_changed = () => {
			const f = this.el.from.val(), t = this.el.to.val();
			if (!f || !t) return;
			this.state.from = f;
			this.state.to = t;
			this.state.open = null;
			this.load(false);
		};
		this.el.from.on("change", date_changed);
		this.el.to.on("change", date_changed);
		this.el.range.on("change", (e) => this.apply_preset(e.target.value));
		this.el.group.on("change", (e) => {
			this.save_group(e.target.value || "");
			this.state.open = null;
			this.render_group_filter(true);
		});
		on("[data-action='widen']", () => this.apply_preset("90"));
		on("[data-action='refresh']", () => this.load(true));
		on("[data-action='settings']", () => this.open_drawer());
		on("[data-action='close-drawer']", () => this.close_drawer());
		on("[data-action='open-settings-form']", () => frappe.set_route("Form", "Nexus Production Settings"));
		on("[data-mode]", (e, t) => {
			if (t.dataset.mode === this.state.mode) return;
			this.state.mode = t.dataset.mode;
			this.remember("mode", this.state.mode);
			this.paint_mode();
			this.load(false);
		});
		on("[data-tab]", (e, t) => this.set_tab(t.dataset.tab));
		on(".ghead", (e, t) => t.parentElement.classList.toggle("collapsed"));
		on("[data-toggle]", (e, t) => {
			const inner = e.target.closest("a, button, [data-goto], [data-rm-for], [data-toggle]");
			if (inner && inner !== t) return; // a nested control handles its own click
			e.stopPropagation();
			this.toggle(t.dataset.toggle);
		});
		on("[data-goto]", (e, t) => {
			e.stopPropagation();
			this.goto_card(t.dataset.goto);
		});
		on("[data-rm-for]", (e, t) => {
			e.stopPropagation();
			// raw materials first; a straight fill with only packaging opens the Packaging tab
			const card = this.cards[t.dataset.rmFor] || {};
			const used = (card.materials || []).map((m) => this.mat_by[m.item_code]).filter(Boolean);
			this.state.tab = !used.length || used.some(this.MAT_TABS().rm) ? "rm" : "pack";
			this.state.open = null;
			this.state.q = t.dataset.rmFor.toLowerCase();
			this.el.search.val(t.dataset.rmFor);
			this.render_tabs();
			this.render_content();
		});
		on("[data-action='retry']", () => this.load(true));
		on("[data-pend-flag]", (e, t) => {
			this.pend.flagged_only = t.dataset.pendFlag === "1";
			this.load_pending();
		});
		on("[data-pend-more]", (e, t) => this.load_pending(t.dataset.pendMore));
		on("[data-action='pend-retry']", () => {
			this.pend.error = false;
			this.load_pending();
		});
		on("[data-wo]", (e, t) => {
			e.stopPropagation();
			this.action_wo(t.dataset.wo);
		});
		on("[data-mr]", (e, t) => {
			e.stopPropagation();
			this.action_mr(t.dataset.mr === "*" ? null : t.dataset.mr.split(","));
		});
		this.$root.on("keydown", ".chead", (e) => {
			if (e.key === "Enter" || e.key === " ") {
				e.preventDefault();
				this.toggle(e.currentTarget.dataset.toggle);
			}
		});
		$(document).on("keydown.nexus_production", (e) => {
			if (e.key === "Escape") this.close_drawer();
		});
	}

	paint_mode() {
		this.$root.find("[data-mode]").each((_, b) => b.setAttribute("aria-pressed", b.dataset.mode === this.state.mode));
	}

	// ================================================================ data
	load(refresh) {
		if (this.loading) return;
		this.loading = true;
		this.paint_live();
		frappe.call({
			method: `${this.API}.get_board`,
			args: { mode: this.state.mode, refresh: refresh ? 1 : 0, from_date: this.state.from, to_date: this.state.to },
			freeze: false,
			callback: (r) => {
				if (!r.message) return;
				this.result = r.message;
				this.pend.stale = true;
				const st = frappe.datetime.str_to_obj((r.message.page || {}).server_time);
				this.server_offset = st ? st - new Date() : 0;
				this.index();
				if (this.state.open && !this.cards[this.state.open] && !this.mat_by[this.state.open]) this.state.open = null;
				this.render();
			},
			error: () => {
				if (!this.result) this.render_failure();
			},
			always: () => {
				this.loading = false;
				this.loaded_at = Date.now();
				this.changed = null;
				this.schedule();
				// the server is recalculating in the background: it will signal when done;
				// look again in 20 s anyway in case the realtime connection is down
				const m = (this.result && this.result.meta) || {};
				if (m.refreshing) this.next_at = Math.min(this.next_at, Date.now() + 20000);
				this.paint_live();
			},
		});
	}

	index() {
		const b = this.result.board;
		this.bulks = b.bulks || [];
		this.items = b.items || [];
		this.mats = b.materials || [];
		this.issues = b.needs_setup || [];
		this.cards = {};
		this.bulks.forEach((c) => (this.cards[c.item_code] = c));
		this.items.forEach((c) => (this.cards[c.item_code] = c));
		this.mat_by = {};
		this.mats.forEach((m) => (this.mat_by[m.item_code] = m));
		this.setup_by = {};
		this.issues.forEach((i) => { if (i.item) (this.setup_by[i.item] = this.setup_by[i.item] || []).push(i); });
	}

	// ================================================================ refresh timer
	interval_ms() {
		const p = (this.result && this.result.page) || {};
		let minutes = p.refresh_minutes || 5;
		if (p.night_start && p.night_end) {
			const hm = (s) => { const [h, m] = String(s).split(":").map(Number); return h * 60 + (m || 0); };
			const d = this.now(), cur = d.getHours() * 60 + d.getMinutes();
			const a = hm(p.night_start), z = hm(p.night_end);
			if (a <= z ? cur >= a && cur < z : cur >= a || cur < z) minutes = p.night_refresh_minutes || minutes;
		}
		return Math.max(1, minutes) * 60000;
	}
	schedule() {
		this.next_at = Date.now() + this.interval_ms();
	}
	visible() {
		return !document.hidden && frappe.get_route()[0] === this.ROUTE;
	}
	busy_ui() {
		// never reload under someone who is in the middle of a dialog
		return document.querySelector(".modal.show") !== null;
	}
	due() {
		return this.next_at && Date.now() >= this.next_at && !this.loading && !this.busy_ui();
	}
	tick() {
		if (!this.visible()) return; // hidden tab or another page: no requests at all
		if (this.due()) this.load(false);
		this.paint_live();
	}
	on_show() {
		if (this.due()) this.load(false);
	}

	// ================================================================ live signals (Batch 6)
	// The server sends "nexus_production_changed" after a relevant document is committed
	// (at most every 30 s). The signal never loads directly: it only brings the next refresh
	// forward, so the timer's rules (visible page, no open dialog, one request at a time) apply.
	live_bind() {
		if (!frappe.realtime || typeof frappe.realtime.on !== "function") return;
		this.on_signal = (msg) => this.signal(msg || {});
		frappe.realtime.on(this.LIVE_EVENT, this.on_signal);
	}
	live_connected() {
		const socket = frappe.realtime && frappe.realtime.socket;
		return !!(socket && socket.connected);
	}
	signal(msg) {
		if (Array.isArray(msg.modes) && !msg.modes.includes(this.state.mode)) return;
		this.changed = msg;
		// a random 2-8 s spread so every open screen does not ask in the same second,
		// and never sooner than MIN_GAP after the last load
		const at = Math.max(Date.now() + 2000 + Math.random() * 6000, this.loaded_at + this.MIN_GAP_MS);
		if (!this.next_at || at < this.next_at) this.next_at = at;
		if (this.visible()) this.paint_live();
	}
	paint_live() {
		const dot = this.el.dot.removeClass("stale busy");
		if (this.loading) {
			dot.addClass("busy");
			this.el.live.text(this.result ? __("Refreshing...") : __("Calculating..."));
			return;
		}
		if (!this.result) return this.el.live.text(__("Not loaded"));
		const m = this.result.meta || {};
		if (m.stale) dot.addClass("stale");
		const left = Math.max(0, Math.round(((this.next_at || Date.now()) - Date.now()) / 1000));
		const at = m.calculated_at ? frappe.datetime.str_to_obj(m.calculated_at) : null;
		const hhmm = at ? `${String(at.getHours()).padStart(2, "0")}:${String(at.getMinutes()).padStart(2, "0")}` : "";
		const clock = `${Math.floor(left / 60)}:${String(left % 60).padStart(2, "0")}`;
		const state = m.stale ? __("Stale") : m.refreshing ? __("Updating in the background")
			: this.live_connected() ? __("Live") : __("Auto refresh");
		const next = this.changed
			? (this.busy_ui() ? __("changes waiting until the dialog closes") : `${__("changes detected, updating in")} ${clock}`)
			: `${__("next refresh in")} ${clock}`;
		this.el.live.text(`${state} · ${__("last sync")} ${hhmm} · ${next}`);
		this.el.dot.toggleClass("changed", !!this.changed || (!m.stale && !!m.refreshing));
	}

	// ================================================================ render
	render() {
		const m = this.result.meta || {}, w = m.window || {}, page = this.result.page || {};
		this.el.from.val(w.from_date || page.default_from || "");
		this.el.to.val(w.to_date || page.default_to || "");
		this.paint_range();
		this.render_group_filter(false);
		const banners = [];
		if (m.stale) {
			banners.push(`<div class="banner stale"><b>${__("Showing the last good board from {0}.", [this.esc(this.when(m.calculated_at))])}</b>
				${__("The latest calculation failed:")} ${this.esc(m.error || "")} <button class="btn sm" data-action="retry" style="margin-left:6px">${__("Try again")}</button></div>`);
		}
		if (w.earlier_orders) {
			banners.push(`<div class="banner info">${__("{0} confirmed orders from before {1} have not been delivered or invoiced, and are not in this plan.", [this.n(w.earlier_orders), this.esc(frappe.datetime.str_to_user(w.from_date))])}
				<button class="btn sm" data-action="widen" data-days="90" style="margin-left:6px">${__("Include the last 90 days")}</button></div>`);
		}
		if (this.state.mode === "drafts") {
			banners.push(`<div class="banner info">${__("Drafts mode: draft orders are added on top of confirmed ones to preview what is coming. Work Order and Material Request buttons are off here; switch to Confirmed to create documents.")}</div>`);
		}
		this.el.banner.html(banners.join(""));
		this.render_summary();
		this.render_tabs();
		this.render_content();
		this.paint_live();
	}

	render_failure() {
		this.el.content.html(`<div class="banner error"><b>${__("The production board could not be loaded.")}</b>
			${__("The reason is in the message above. Check Nexus Production Settings, then try again.")}
			<div class="actions"><button class="btn primary" data-action="retry">${__("Try again")}</button><button class="btn" data-action="open-settings-form">${__("Production settings")}</button></div></div>`);
	}

	render_summary() {
		const s = this.result.board.summary || {}, w = (this.result.meta || {}).window || {};
		const cooks = this.bulks.filter((b) => b.cook > 0);
		const tints = this.tab_cards("tint").filter((c) => c.production_total > 0);
		const rm_short = this.mats.filter((m) => m.short > 0);
		const all = this.bulks.concat(this.items);
		const p0 = all.filter((c) => c.priority === 0).length;
		const oldest = all.map((c) => c.oldest_order_at).filter(Boolean).sort()[0];
		const errors = this.issues.filter((i) => i.severity === "error" && i.in_plan).length;
		const stat = (k, v, sub, color) => `<div class="stat"><div class="k">${k}</div><div class="v"${color ? ` style="color:${color}"` : ""}>${v}</div><div class="s">${sub}</div></div>`;
		this.el.summary.html([
			stat(__("Orders in scope"), `${this.n(s.orders_counted)}${s.drafts_counted ? ` <span style="font-size:13px;color:var(--muted)">+ ${this.n(s.drafts_counted)} ${__("drafts")}</span>` : ""}`,
				`${__("since")} ${this.esc(w.from_date ? frappe.datetime.str_to_user(w.from_date) : "")} · ${this.n(s.orders_ready_from_stock)} ${__("ready from stock")}`),
			stat(__("Bulk cooks"), this.n(cooks.length), `≈ ${this.kg(s.kg_to_cook)} kg ${__("total")}`),
			stat(__("Tint jobs"), this.n(tints.length), `${this.n(tints.reduce((a, c) => a + c.production_total, 0))} ${__("units")}`),
			stat(__("RM shortfalls"), this.n(rm_short.length), `${this.kes(s.shortage_value)} ${__("to buy")}`, "var(--bad)"),
			stat(__("Urgent / oldest"), this.n(p0), oldest ? `${__("oldest waiting")} ${this.fmt_age(this.age_h(oldest))}` : __("nothing waiting"), "var(--p0)"),
			stat(__("Needs setup"), this.n(errors), `${__("errors in today's plan")} · ${this.n(this.issues.length)} ${__("checks")}`, "var(--warn)"),
		].join(""));
	}

	tab_cards(tab) {
		return this.all_tab_cards(tab).filter((c) => this.in_group(c));
	}
	all_tab_cards(tab) {
		return this.bulks.concat(this.items).filter((c) => this.tab_of(c) === tab);
	}
	tab_defs() {
		return [
			{ k: "bulk", label: __("Bulk Production"), desc: __("Bulks ticked Is BIP: what to cook, most urgent first. Each card shows the finished goods filled from it and the tints and blends that draw on it.") },
			{ k: "fg", label: __("Finished Goods"), desc: __("Every finished good that is filled, packed or made from raw materials, with its Sales Orders, under its Item Group. Tinted products are on Tinting Production.") },
			{ k: "tint", label: __("Tinting Production"), desc: __("Any FG whose BOM adds colour to a base: colourants on bulk, colourants on a packed base, or a paste blend.") },
			{ k: "paste", label: __("Pastes & Stainers"), desc: __("Bulks ticked Is Stainer, cooked like any BIP but used mostly as tinting inputs. Demand is blend draws plus packed stainer sales.") },
			{ k: "rm", label: __("Raw Materials"), desc: __("Raw materials and colourants the cooks and tints consume, less free stock in the raw material warehouses. Sorted by shortfall value, so kg, litres and pieces compare.") },
			{ k: "pack", label: __("Packaging"), desc: __("Tins, pails, lids and labels the fills and tints consume (the Packaging Item Group in Nexus Production Settings), less free stock. Sorted by shortfall value.") },
			{ k: "setup", label: __("Needs Setup"), desc: __("Data problems that stop the engine from computing correctly. Fix the ones in today's plan first.") },
			{ k: "pending", label: __("Pending WOs & POs"), desc: __("Open Work Orders and Purchase Orders not yet reconciled, read straight from ERPNext when this tab is opened. Clean-up list only: nothing here is changed by the board.") },
		];
	}
	render_tabs() {
		if (!this.result) return;
		const sc = this.state.show_covered;
		const count = (list) => list.filter((c) => sc || c.priority < 3).length;
		const short = (tab) => this.mats_for(tab).filter((m) => m.short > 0).length;
		const open_mats = (tab) => (sc ? this.mats_for(tab).length : this.mats_for(tab).filter((m) => this.rm_status(m)[0] !== "ok").length);
		const setup = this.issues.filter((i) => i.in_plan).length;
		const pd = this.pend.data;
		const pending = pd ? pd.work_orders.flagged + pd.purchase_orders.flagged : null;
		const c = {
			bulk: count(this.tab_cards("bulk")),
			fg: count(this.tab_cards("fg")),
			tint: count(this.tab_cards("tint")),
			paste: count(this.tab_cards("paste")),
			rm: open_mats("rm"),
			pack: open_mats("pack"),
			setup,
			pending: pending === null ? "…" : pending,
		};
		const bad = { rm: short("rm") > 0, pack: short("pack") > 0, setup: setup > 0, pending: pending > 0 };
		this.el.tabs.html(this.tab_defs().map((t) =>
			`<button class="tab ${t.k === this.state.tab ? "active" : ""}" data-tab="${t.k}" role="tab" aria-selected="${t.k === this.state.tab}">${t.label}<span class="n ${bad[t.k] ? "bad" : ""}">${c[t.k]}</span></button>`).join(""));
		this.el.tabdesc.text(this.tab_defs().find((t) => t.k === this.state.tab).desc);
		const t = this.state.tab;
		this.el.rmsort.toggle(t === "rm" || t === "pack");
		this.el.group.toggle(["bulk", "fg", "tint", "paste"].includes(t));
		this.el.covered.closest("label").toggle(this.state.tab !== "pending");
		this.el.legend.toggle(this.state.tab !== "pending");
	}
	set_tab(k) {
		this.state.tab = k;
		this.state.open = null;
		this.render_tabs();
		this.render_content();
	}
	render_content() {
		if (!this.result) return;
		const t = this.state.tab;
		let html;
		if (t === "bulk") html = this.flat(this.tab_cards("bulk"), __("Bulks to cook"), __("bulk cards"));
		else if (t === "paste") html = this.flat(this.tab_cards("paste"), __("Pastes and stainers"), __("paste cards"));
		else if (t === "fg") html = this.grouped(this.tab_cards("fg"), __("finished goods"));
		else if (t === "tint") html = this.grouped(this.tab_cards("tint"), __("tint jobs"));
		else if (t === "rm" || t === "pack") html = this.render_rm_list(t);
		else if (t === "pending") html = this.render_pending();
		else html = this.render_setup();
		this.el.content.html(html);
	}

	// ================================================================ groups and cards
	search_text(c) {
		const parts = [c.item_code, c.item_name, c.route, c.group];
		const add_rows = (k) => ((k && k.rows) || []).forEach((r) => parts.push(r.sales_order, r.customer, r.via));
		add_rows(c);
		(c.short_orders || []).forEach((r) => parts.push(r.sales_order, r.customer));
		(c.draws || []).forEach((d) => { parts.push(d.item_code, d.item_name); add_rows(this.cards[d.item_code]); });
		(c.children || []).forEach((code) => { const k = this.cards[code]; parts.push(code, k && k.item_name); add_rows(k); });
		(c.inputs || []).forEach((i) => parts.push(i.item_code, i.item_name));
		(c.materials || []).forEach((m) => parts.push(m.item_code, m.item_name));
		return parts.filter(Boolean).join(" ").toLowerCase();
	}
	sort_cards(a, b) {
		return (a.priority - b.priority) || String(a.oldest_order_at || "9999").localeCompare(String(b.oldest_order_at || "9999")) || a.item_code.localeCompare(b.item_code);
	}
	grouped(items, label) {
		const q = this.state.q, sc = this.state.show_covered;
		const pick = this.state.group;
		// with a group chosen, a bulk that also feeds it is shown under it
		const home = (c) => (pick && c.group !== pick ? pick : c.group);
		const order = pick ? [pick] : (this.result.board.groups || []).slice();
		items.forEach((c) => { if (!order.includes(home(c))) order.push(home(c)); });
		let html = "";
		order.forEach((g) => {
			const all = items.filter((c) => home(c) === g && (!q || this.search_text(c).includes(q)));
			if (!all.length) return;
			const vis = all.filter((c) => sc || c.priority < 3).sort((a, b) => this.sort_cards(a, b));
			const hidden = all.length - vis.length;
			const urgent = vis.filter((c) => c.priority === 0).length;
			const oldest = vis.map((c) => c.oldest_order_at).filter(Boolean).sort()[0];
			html += `<section class="group">
				<div class="ghead"><span class="chev">▾</span><h2>${this.esc(g)}</h2>
					<div class="gmeta"><span class="chip c-grey">${vis.length} ${label}</span>
					${urgent ? `<span class="chip c-red">${urgent} ${__("urgent")}</span>` : ""}
					${oldest ? `<span class="chip c-amber">${__("oldest")} ${this.fmt_age(this.age_h(oldest))}</span>` : ""}
					${hidden ? `<span class="chip c-green">${hidden} ${__("covered hidden")}</span>` : ""}</div></div>
				<div class="gbody">${vis.map((c) => this.render_card(c)).join("") || `<div class="hidden-note">${__("All cards in this group are covered. Tick “Show covered” to see them.")}</div>`}</div>
			</section>`;
		});
		if (html) return html;
		if (q) return `<div class="hidden-note">${__("No cards match the current search.")}</div>`;
		if (pick) return `<div class="hidden-note">${__("Nothing in {0} for orders in this window. Choose All Item Groups to see every group.", [this.esc(pick)])}</div>`;
		if (this.state.tab === "fg") return `<div class="hidden-note">${__("No finished goods to fill or make for orders in this window.")}</div>`;
		return `<div class="hidden-note">${__("Nothing here for orders in this window.")}</div>`;
	}
	flat(items, title, label) {
		// bulks are all in the Work In Progress item group, so they are listed by priority, not by group
		const q = this.state.q, sc = this.state.show_covered, pick = this.state.group;
		const all = items.filter((c) => !q || this.search_text(c).includes(q));
		const vis = all.filter((c) => sc || c.priority < 3).sort((a, b) => this.sort_cards(a, b));
		const hidden = all.length - vis.length;
		if (!all.length) {
			if (q) return `<div class="hidden-note">${__("No cards match the current search.")}</div>`;
			if (pick) return `<div class="hidden-note">${__("No bulk feeds {0} for orders in this window. Choose All Item Groups to see every bulk.", [this.esc(pick)])}</div>`;
			return `<div class="hidden-note">${__("Nothing to cook for orders in this window. If that looks wrong, check Needs Setup: bulks must be ticked Is BIP.")}</div>`;
		}
		const urgent = vis.filter((c) => c.priority === 0).length;
		const oldest = vis.map((c) => c.oldest_order_at).filter(Boolean).sort()[0];
		const cooks = vis.filter((c) => c.cook > 0);
		return `<section class="group">
			<div class="ghead"><span class="chev">▾</span><h2>${this.esc(pick ? __("{0} feeding {1}", [title, pick]) : title)}</h2>
				<div class="gmeta"><span class="chip c-grey">${vis.length} ${label}</span>
				${cooks.length ? `<span class="chip c-blue">${cooks.length} ${__("to cook")} · ≈ ${this.kg(cooks.reduce((a, c) => a + c.cook, 0))} kg</span>` : ""}
				${urgent ? `<span class="chip c-red">${urgent} ${__("urgent")}</span>` : ""}
				${oldest ? `<span class="chip c-amber">${__("oldest")} ${this.fmt_age(this.age_h(oldest))}</span>` : ""}
				${hidden ? `<span class="chip c-green">${hidden} ${__("covered hidden")}</span>` : ""}</div></div>
			<div class="gbody">${vis.map((c) => this.render_card(c)).join("") || `<div class="hidden-note">${__("All cards are covered. Tick “Show covered” to see them.")}</div>`}</div>
		</section>`;
	}
	shell(id, pri, head, metrics, body) {
		const open = this.state.open === id;
		return `<article class="card ${pri} ${open ? "open" : ""}" data-key="${this.esc(id)}">
			<div class="chead" data-toggle="${this.esc(id)}" role="button" tabindex="0" aria-expanded="${open}">
				${head}
				<div class="metrics">${metrics}<button class="btn sm openbtn" data-toggle="${this.esc(id)}">${__("View card")}</button></div>
			</div>
			${open ? `<div class="cbody"><div class="bodyhead"><span class="h">${__("Card details")}</span><button class="btn sm" data-toggle="${this.esc(id)}">✕ ${__("Close")}</button></div>${body()}</div>` : ""}
		</article>`;
	}
	toggle(id) {
		this.state.open = this.state.open === id ? null : id;
		this.render_content();
		if (this.state.open) {
			const el = this.$root[0].querySelector(`.card[data-key="${CSS.escape(id)}"]`);
			if (el) el.scrollIntoView({ behavior: "smooth", block: "start" });
		}
	}
	goto_card(code) {
		const target = code;
		let tab = this.tab_of(this.cards[code]);
		if (!tab && this.mat_by[code]) tab = this.MAT_TABS().pack(this.mat_by[code]) ? "pack" : "rm";
		if (!tab) return;
		const goto_card = this.cards[target];
		if (goto_card && !this.in_group(goto_card)) {
			this.state.group = "";
			this.render_group_filter(false);
			frappe.show_alert({ message: __("Showing All Item Groups to open {0}", [target]), indicator: "blue" });
		}
		this.state.tab = tab;
		this.state.q = "";
		this.el.search.val("");
		const card = this.cards[target] || this.mat_by[target];
		const covered = card && (this.mat_by[target] ? this.rm_status(card)[0] === "ok" : card.priority >= 3);
		if (covered) {
			this.state.show_covered = true;
			this.el.covered.prop("checked", true);
		}
		this.state.open = null;
		this.render_tabs();
		this.toggle(target);
	}
	render_card(c) {
		return c.cook !== undefined ? this.render_bip(c) : this.render_item(c);
	}

	// ---------------------------------------------------------------- chips
	wo_chip(w, uom) {
		const cls = { "Not Started": "c-blue", "In Process": "c-amber", Stopped: "c-red" }[w.status] || "c-grey";
		const ded = w.counted ? ` · −${this.n(w.remaining, 2)}${uom ? " " + this.esc(this.u(uom)) : ""} ${__("counted")}` : ` · ${__("not counted")}`;
		return `<span class="chip ${cls}" title="${this.esc(w.name + (w.reason ? " · " + w.reason : ""))}">${this.esc(w.name.replace(/^MFG-WO-\d{4}-/, "WO "))} ${this.esc(w.status)} ${this.n(w.produced_qty)}/${this.n(w.qty)}${ded}</span>`;
	}
	wo_chips(c) {
		if (!this.wo_on()) return "";
		return (((c.pipeline || {}).work_orders) || []).slice(0, 3).map((w) => this.wo_chip(w, c.uom)).join("");
	}
	setup_chip(c) {
		const list = this.setup_by[c.item_code] || [];
		if (list.some((i) => i.severity === "error")) return `<span class="chip c-red">⚠ ${__("Needs setup")}</span>`;
		if (list.some((i) => i.severity === "warning")) return `<span class="chip c-amber">⚠ ${__("Check setup")}</span>`;
		return "";
	}
	oldest_chip(c) {
		return c.oldest_order_at ? `<span class="chip ${c.priority === 0 ? "c-red" : "c-amber"}">${__("oldest")} ${this.fmt_age(this.age_h(c.oldest_order_at))}</span>` : "";
	}
	metric(k, v, cls = "") {
		return `<div class="m"><div class="k">${k}</div><div class="v ${cls}">${v}</div></div>`;
	}
	table(head, rows, compact, empty) {
		if (!rows.length) return `<div class="panel"><span class="empty">${empty || __("Nothing to show.")}</span></div>`;
		return `<div class="tbl-wrap"><table class="t ${compact ? "compact" : ""}"><thead><tr>${head.map((h) => {
			const [t, num] = Array.isArray(h) ? h : [h, false];
			return `<th class="${num ? "num" : ""}">${t}</th>`;
		}).join("")}</tr></thead><tbody>${rows.join("")}</tbody></table></div>`;
	}
	issues_box(c) {
		const list = (this.setup_by[c.item_code] || []).filter((i) => i.severity !== "info");
		return list.map((i) => `<div class="warnbox">⚠ <b>${this.esc(i.title)}.</b> ${this.esc(i.detail)}${i.fix && i.fix !== "Info" ? ` ${__("Fix")}: ${this.esc(i.fix)}.` : ""}</div>`).join("");
	}
	working(c) {
		const st = c.stock || {};
		const shelf = (w) => (w.shelf !== undefined ? w.shelf : Math.max(0, w.actual || 0));
		const aside = (w) => (w.set_aside !== undefined ? w.set_aside : w.reserved_for_production);
		const stock = (st.by_warehouse || []).map((w) => `<tr><td>${this.esc(w.warehouse)}${w.actual < 0 ? ` <span class="chip c-amber">${__("ERPNext shows")} ${this.kg(w.actual)}</span>` : ""}</td>
			<td class="num">${this.kg(shelf(w))}</td><td class="num">${this.kg(aside(w))}</td><td class="num">${this.kg(w.transferred_not_consumed)}</td><td class="num"><b>${this.kg(w.free)}</b></td></tr>`);
		const wos = (((c.pipeline || {}).work_orders) || []).map((w) => `<tr><td>${this.link("Work Order", w.name)}</td><td>${this.esc(w.status)}</td>
			<td class="num">${this.kg(w.qty)}</td><td class="num">${this.kg(w.produced_qty)}</td><td class="num">${this.kg(w.remaining)}</td>
			<td>${this.esc(this.when(w.planned_start_date))}</td><td>${w.counted ? `<span class="chip c-green">${__("Counted")}</span>` : `<span class="chip c-grey">${this.esc(w.reason || __("Not counted"))}</span>`}</td></tr>`);
		return `<details class="working"><summary>${__("Show the working")}</summary><div class="inner">
			<div class="sect">${__("Free stock by warehouse")} (${this.esc(this.u(c.uom))})</div>
			${this.table([__("Warehouse"), [__("On the shelf"), 1], [__("Set aside for counted WOs"), 1], [__("Moved to WIP, not used"), 1], [__("Free"), 1]], stock, true, __("No stock in the warehouses this item is read from."))}
			<div class="note">${this.wo_on() ? __("Free = on the shelf − set aside for the Work Orders the board counts − moved to WIP for them but not used yet. A Bin below zero counts as 0.")
				: __("Work Orders are not included (Nexus Production Settings), so free stock is the stock on the shelf. A Bin below zero counts as 0.")}</div>
			${this.wo_on() ? `<div class="sect">${__("Work Orders for this item")}</div>
			${this.table([__("Work Order"), __("Status"), [__("Qty"), 1], [__("Made"), 1], [__("Remaining"), 1], __("Planned"), __("Counted")], wos, true, __("No open Work Orders."))}` : ""}
			<div class="actions">${this.link("Item", c.item_code, __("Open item"))}${c.bom ? ` · ${this.link("BOM", c.bom, __("Open BOM {0}", [c.bom]))}` : ""}</div>
		</div></details>`;
	}

	// ================================================================ BIP card (bulk and paste)
	bip_parts(b) {
		const fills = [], draws = [];
		const seen = new Set();
		(b.draws || []).forEach((d) => {
			const card = d.item_code ? this.cards[d.item_code] : null;
			const tint = card && (card.tab === "tinting" || /Tint/.test(card.route || ""));
			if (card && d.is_home && !tint) fills.push({ card, kg: d.kg });
			else draws.push({ d, card });
			if (d.item_code) seen.add(d.item_code);
		});
		(b.children || []).concat((b.excess || []).map((e) => e.item_code)).forEach((code) => {
			if (seen.has(code) || !this.cards[code]) return;
			seen.add(code);
			fills.push({ card: this.cards[code], kg: 0 });
		});
		return { fills, draws };
	}
	per_unit(card, bulk_code) {
		const i = ((card && card.inputs) || []).find((x) => x.item_code === bulk_code);
		return i ? i.per_unit : 0;
	}
	render_bip(b) {
		const u = b.uom;
		const { fills, draws } = this.bip_parts(b);
		const tint_base = draws.some((x) => x.card && x.card.tab === "tinting") || fills.some((f) => (f.card.used_as_base_by || []).length);
		const role = b.is_stainer ? `<span class="chip c-violet">${__("Paste / stainer")}</span>`
			: tint_base ? `<span class="chip c-cyan">${__("Tint base")}</span>` : `<span class="chip c-grey">${__("Standard bulk")}</span>`;
		const mode = b.cook > 0 ? `<span class="chip c-blue">${__("Cook")} ≈ ${this.kg(b.cook)} ${this.esc(this.u(u))}</span>`
			: b.demand > 0 ? `<span class="chip c-green">${__("No cook needed · fill from store")}</span>` : `<span class="chip c-green">${__("Covered")}</span>`;
		const head = `<div class="ctitle"><div class="t"><span class="pri ${this.pri(b.priority)}">${this.pri(b.priority)}</span>${this.esc(b.item_name)} <span class="code">[${this.esc(b.item_code)}]</span></div>
			<div class="sub">${role}${mode}${this.feeds_chip(b)}${this.oldest_chip(b)}${this.neg_chip(b.stock)}${this.wo_chips(b)}${b.existing_wo_deficit > 0 ? `<span class="chip c-red">${__("Open WOs short")} ${this.q(b.existing_wo_deficit, u)}</span>` : ""}${(b.blocked_by || []).length ? `<span class="chip c-red">${b.blocked_by.length} ${__("RM short")}</span>` : ""}${this.setup_chip(b)}</div></div>`;
		const stays = b.cook > 0 ? b.leftover_kg : Math.max(0, b.after_store || 0);
		const metrics = this.metric(__("Min batch"), b.min_batch ? this.q(b.min_batch, u) : "—")
			+ this.metric(__("Demand"), `≈ ${this.q(b.demand, u)}`, "blue")
			+ this.metric(__("Free bulk"), this.q(this.free(b.stock), u))
			+ this.metric(__("Cook"), b.cook > 0 ? `≈ ${this.q(b.cook, u)}` : __("None"), b.cook > 0 ? "red" : "green")
			+ this.metric(__("Stays in store"), this.q(stays, u), "violet");
		return this.shell(b.item_code, this.pri(b.priority), head, metrics, () => this.bip_body(b, fills, draws));
	}
	bip_body(b, fills, draws) {
		const u = this.u(b.uom);
		const fill_kg = fills.reduce((a, f) => a + f.kg, 0);
		const draw_kg = draws.reduce((a, x) => a + x.d.kg, 0);
		const excess_kg = (b.excess || []).reduce((a, e) => a + e.kg, 0);
		const st = b.stock || {};
		const R = (l, r, cls = "") => `<span class="l ${cls}">${l}</span><span class="r ${cls}">${r}</span>`;
		const math = `<div class="panel"><h4>${__("Bulk maths")} (${this.esc(u)})</h4><div class="math">
			${R(__("FG fills (Tier 1 + Tier 2)"), this.kg(fill_kg))}
			${R(__("Drawn by tinting, blends and direct orders"), this.kg(draw_kg))}
			${R(__("Total demand"), this.kg(b.demand), "tot")}
			${R(`− ${__("Free bulk in store")} <span class="note">(${this.wo_on()
				? `${__("on the shelf")} ${this.kg(st.shelf !== undefined ? st.shelf : st.actual)} − ${__("set aside")} ${this.kg(st.reserved_for_production)} − ${__("moved to WIP")} ${this.kg(st.transferred_not_consumed)}`
				: `${__("on the shelf")}; ${__("Work Orders not included")}`})</span>`, this.minus(this.free(st), (x) => this.kg(x)))}
			${this.wo_on() ? R(`− ${__("Active cook Work Orders")}`, this.minus((b.pipeline || {}).qty, (x) => this.kg(x))) : ""}
			${b.existing_wo_deficit > 0 ? R(`+ ${__("Open Work Orders already short of this bulk")}`, "+" + this.kg(b.existing_wo_deficit)) : ""}
			${R(__("Net to cook"), this.kg(b.net), "tot")}
			${b.cook > 0
				? R(`${__("Forced up to min batch")} (${this.kg(b.min_batch)})`, this.kg(b.cook)) + R(__("Top-up"), this.kg(b.topup))
					+ R(`→ ${__("Tier 3 excess into FGs")}`, `<span class="t3">${this.kg(excess_kg)}</span>`) + R(`→ ${__("Stays in bulk store")}`, this.kg(b.leftover_kg))
				: R(__("After this demand, bulk left in store"), this.kg(b.after_store))}
			</div><div class="note" style="margin-top:6px">${__("≈ means process loss is not added. Supervisors set the final batch size from raw material availability.")}</div></div>`;

		const pack = {};
		fills.forEach((f) => (f.card.materials || []).filter((m) => m.kind === "packaging").forEach((m) => {
			pack[m.item_code] = pack[m.item_code] || { name: m.item_name, qty: 0 };
			pack[m.item_code].qty += m.qty;
		}));
		const packs = Object.entries(pack).map(([code, p]) => `<div class="pack"><b>${this.n(p.qty)}</b> × ${this.esc(p.name)} <span class="note">${this.esc(code)}</span></div>`).join("");
		const drawoffs = draws.map((x) => x.d.item_code
			? `<div class="pack" style="margin-bottom:6px"><b>${this.kg(x.d.kg)} ${this.esc(u)}</b> → ${this.esc(x.d.item_code)} ${this.esc(x.d.item_name)} · ${this.esc(x.d.route || "")}</div>`
			: `<div class="pack" style="margin-bottom:6px"><b>${this.kg(x.d.kg)} ${this.esc(u)}</b> → ${__("direct orders for this bulk")}</div>`).join("");
		const side = `<div class="panel"><h4>${__("Packaging to issue")}</h4><div class="packs">${packs || `<span class="empty">${__("No fills from this card.")}</span>`}</div>
			${drawoffs ? `<h4 style="margin-top:12px">${__("Draw-offs to tinting and blends")}</h4>${drawoffs}` : ""}</div>`;

		const so_lines = (card) => {
			const rows = (card.rows || []).filter((r) => r.sales_order).sort((a, z) => String(a.at || "").localeCompare(String(z.at || "")));
			const direct = rows.filter((r) => !r.via).map((r) => this.so_line(r, r.qty));
			const via = {};
			rows.filter((r) => r.via).forEach((r) => (via[r.via] = (via[r.via] || 0) + r.qty));
			Object.entries(via).forEach(([code, qty]) => direct.push(`<span class="sol">+ ${this.n(qty, 2)} ${__("for tinting")} <b>${this.esc(code)}</b></span>`));
			return direct.join("") || `<span class="empty">${__("none")}</span>`;
		};
		const fg_rows = fills.map(({ card: f, kg }) => {
			const per = this.per_unit(f, b.item_code);
			const wo = ((f.pipeline || {}).work_orders || [])[0];
			const tier1 = f.short + (f.existing_wo_deficit || 0);
			const mon = f.shelf_monitor && f.shelf_monitor.tracked;
			return `<tr>
				<td><button class="linkbtn" data-goto="${this.esc(f.item_code)}" title="${__("Open its card on Finished Goods")}">${this.esc(f.item_code)}</button><div class="note">${this.esc(f.item_name)}</div>${this.setup_chip(f)}</td>
				<td class="num">${per ? this.n(per, 4) : "—"}</td>
				<td>${so_lines(f)}</td>
				<td class="num"><b>${this.n(this.ordered(f), 2)}</b></td>
				<td class="num">${this.n(this.free(f.stock), 2)}</td>
				<td>${wo && this.wo_on() ? this.wo_chip(wo, f.uom) : "—"}</td>
				<td class="num">${mon ? `${this.n(f.shelf_monitor.mrl)} / ${this.n(f.shelf_monitor.max)}` : "—"}</td>
				<td class="num t1">${this.n(tier1, 2)}</td>
				<td class="num t2">${this.n(f.shelf, 2)}</td>
				<td class="num t3">${this.n(f.excess_units, 2)}</td>
				<td class="num"><b>${this.n(f.production_total, 2)}</b></td>
				<td class="num"><b>${this.kg(kg + (f.excess_kg || 0))}</b></td>
				<td>${f.production_total > 0 && f.can_produce ? this.wo_btn(f.item_code, `＋ ${__("Work Order")}`) : "—"}</td></tr>`;
		});
		const fg_table = fills.length
			? this.table([__("FG"), [`${this.esc(u)} / ${__("unit")} (BOM)`, 1], __("Orders (oldest first)"), [__("Ordered"), 1], [__("Stock"), 1], __("Fill WO"), [__("MRL / Max"), 1],
				[__("Tier 1 orders"), 1], [__("Tier 2 shelf"), 1], [__("Tier 3 excess"), 1], [__("Fill units"), 1], [`${__("Bulk")} ${this.esc(u)}`, 1], __("Action")], fg_rows)
			: `<div class="panel"><span class="empty">${__("No FG on an order is filled straight from this bulk. All its demand comes from tinting or blend draws.")}</span></div>`;

		const draw_rows = draws.map(({ d, card }) => {
			if (!d.item_code) return `<tr><td colspan="4"><b>${__("Direct orders for this bulk")}</b></td><td class="num"><b>${this.kg(d.kg)}</b></td><td>${(b.short_orders || []).slice(0, 4).map((o) => this.so_line(o)).join("")}</td></tr>`;
			const per = this.per_unit(card, b.item_code);
			const sos = card ? [...new Map((card.rows || []).filter((r) => r.sales_order && r.short > 0).map((r) => [r.sales_order, r])).values()] : [];
			const why = sos.length ? sos.slice(0, 4).map((r) => this.so_line(r)).join("") + (sos.length > 4 ? `<span class="note">+${sos.length - 4} ${__("more")}</span>` : "")
				: `<span class="note">${card && card.shelf > 0 ? __("Shelf refill") : __("Stock building")}</span>`;
			return `<tr><td><button class="btn sm" data-goto="${this.esc(d.item_code)}">${this.esc(d.item_code)}</button><div class="note">${this.esc(d.item_name)}</div></td>
				<td>${this.route_chip(d.route)}</td><td class="num">${per ? this.n(d.kg / per, 2) : "—"}</td><td class="num">${per ? this.n(per, 4) : "—"}</td>
				<td class="num"><b>${this.kg(d.kg)}</b></td><td>${why}</td></tr>`;
		});
		const draw_table = draws.length ? `<div class="sect">${__("Tinting & blend draws on this bulk")}</div>`
			+ this.table([__("Drawn by"), __("Route"), [__("Units"), 1], [`${this.esc(u)} / ${__("unit")} (BOM)`, 1], [this.esc(u), 1], __("Reason / orders")], draw_rows) : "";

		const line = (x) => `<div class="line">${x}</div>`;
		const t1 = fills.filter((f) => f.card.short > 0).map(({ card: f }) => {
			const sos = [...new Set((f.rows || []).filter((r) => r.sales_order && r.short > 0).map((r) => r.sales_order))];
			return line(`${__("Fill")} <b class="t1">${this.n(f.short, 2)}</b> × ${this.esc(f.item_code)} ${__("for")} ${sos.slice(0, 4).map((s) => this.link("Sales Order", s)).join(", ")}${sos.length > 4 ? ` +${sos.length - 4}` : ""}`);
		}).concat(fills.filter((f) => f.card.short <= 0 && f.card.demand > 0).map(({ card: f }) =>
			`<div class="line" style="color:var(--ok)">✓ ${this.esc(f.item_code)}: ${this.n(f.demand, 2)} ${__("on order covered by stock")}${f.pipeline.qty ? " + " + __("Work Order") : ""}</div>`))
			.concat(draws.filter((x) => x.card && x.card.short > 0).map((x) => line(`${__("Release")} <b class="t1">${this.kg(x.d.kg)} ${this.esc(u)}</b> ${__("to")} ${this.esc((x.d.route || "").toLowerCase())} ${this.esc(x.d.item_code)}`))).join("");
		const t2 = fills.filter((f) => f.card.shelf > 0).map(({ card: f }) =>
			line(`${__("Fill")} <b class="t2">${this.n(f.shelf, 2)}</b> × ${this.esc(f.item_code)}: ${__("stock after orders {0} ≤ MRL {1} → refill to max {2}", [this.n(f.post_order, 2), this.n(f.shelf_monitor.mrl), this.n(f.shelf_monitor.max)])}`)).join("");
		const t3 = (b.excess || []).map((e) => line(`${__("Fill")} <b class="t3">${this.n(e.units)}</b> × ${this.esc(e.item_code)} (${this.kg(e.kg)} ${this.esc(u)}): ${__("lowest shelf cover first")}`)).join("")
			+ (b.cook > 0 && b.leftover_kg > 0.01 ? line(`${this.kg(b.leftover_kg)} ${this.esc(u)} ${b.is_stainer ? __("stays in store as paste for future tinting") : __("stays in bulk store; no monitored FG has shelf space left")}`) : "");
		const tiers = `<div class="tiers">
			<div class="tier a"><h5>${__("Tier 1 · Orders")}</h5>${t1 || `<span class="empty">${__("No order shortfall.")}</span>`}</div>
			<div class="tier b"><h5>${__("Tier 2 · Shelf refill (MRL hit)")}</h5>${t2 || `<span class="empty">${__("All shelves above MRL.")}</span>`}</div>
			<div class="tier c"><h5>${__("Tier 3 · Excess from min batch")}</h5>${t3 || `<span class="empty">${__("No excess. Demand meets or exceeds the minimum batch.")}</span>`}</div></div>`;

		const actions = `<div class="actions">${b.cook > 0 ? this.wo_btn(b.item_code, `＋ ${__("Cook Work Order")} (≈ ${this.kg(b.cook)} ${u})`, true) : ""}
			<button class="btn" data-rm-for="${this.esc(b.item_code)}">${__("View raw materials for this card")}</button>
			${b.bom ? this.link("BOM", b.bom, __("Open BIP BOM")) : ""}</div>`;

		return this.issues_box(b) + `<div class="mathgrid">${math}${side}</div>`
			+ `<div class="sect">${__("FGs filled from this bulk")}</div>` + fg_table + draw_table + tiers + this.working(b) + actions;
	}

	// ================================================================ fill / tint card
	is_tint(i) {
		return i.tab === "tinting";
	}
	base_text(i) {
		return (i.inputs || []).map((x) => this.q(x.qty, x.uom)).join(" + ") || "—";
	}
	render_item(i) {
		const u = i.uom;
		const tint = this.is_tint(i);
		const made_to = i.shelf_monitor && i.shelf_monitor.tracked ? `<span class="chip c-blue">${__("fast mover")}</span>` : `<span class="chip c-grey">${__("made to order")}</span>`;
		const base = (i.inputs || []).find((x) => x.kind === "packed_base");
		const base_chip = base && this.cards[base.item_code] ? (this.cards[base.item_code].short > 0 ? `<span class="chip c-red">${__("Base short")}</span>` : `<span class="chip c-green">${__("Base ready")}</span>`) : "";
		const head = `<div class="ctitle"><div class="t"><span class="pri ${this.pri(i.priority)}">${this.pri(i.priority)}</span>${this.esc(i.item_name)} <span class="code">[${this.esc(i.item_code)}]</span></div>
			<div class="sub">${this.route_chip(i.route)}${made_to}${base_chip}${this.oldest_chip(i)}${this.neg_chip(i.stock)}${this.wo_chips(i)}${(i.blocked_by || []).length ? `<span class="chip c-red">${i.blocked_by.length} ${__("RM short")}</span>` : ""}${this.setup_chip(i)}</div></div>`;
		const metrics = this.metric(__("On order"), this.q(i.demand, u, 2))
			+ this.metric(__("Free stock"), this.q(this.free(i.stock), u, 2))
			+ this.metric(tint ? __("To tint") : __("To make"), this.q(i.production_total, u, 2), i.production_total > 0 ? "red" : "green")
			+ ((i.inputs || []).length ? this.metric(tint ? __("Base needed") : __("Bulk needed"), this.base_text(i), "blue") : "");
		return this.shell(i.item_code, this.pri(i.priority), head, metrics, () => this.item_body(i, tint, base));
	}
	item_body(i, tint, base) {
		const u = i.uom;
		const R = (l, r, cls = "") => `<span class="l ${cls}">${l}</span><span class="r ${cls}">${r}</span>`;
		const mon = i.shelf_monitor && i.shelf_monitor.tracked;
		const units = `<div class="panel"><h4>${__("Units")} (${this.esc(this.u(u))})</h4><div class="math">
			${R(__("On order"), this.n(i.demand, 2))}
			${R(`− ${__("Free stock")}`, this.minus(this.free(i.stock), (x) => this.n(x, 2)))}
			${this.wo_on() ? R(`− ${__("Active Work Orders")}`, this.minus(i.pipeline.qty, (x) => this.n(x, 2))) : ""}
			${i.existing_wo_deficit > 0 ? R(`+ ${__("Open Work Orders already short")}`, "+" + this.n(i.existing_wo_deficit, 2)) : ""}
			${R(__("Tier 1 · for orders"), this.n(i.short + (i.existing_wo_deficit || 0), 2), "tot t1")}
			${R(`${__("Tier 2 · shelf refill")} ${mon ? `(MRL ${this.n(i.shelf_monitor.mrl)} / Max ${this.n(i.shelf_monitor.max)})` : `(${__("no shelf values")})`}`, this.n(i.shelf, 2), "t2")}
			${i.excess_units > 0 ? R(__("Tier 3 · excess from bulk top-up"), this.n(i.excess_units, 2), "t3") : ""}
			${i.rounding > 0 ? R(__("Rounded up to whole units"), this.n(i.rounding, 2)) : ""}
			${R(tint ? __("Units to tint") : __("Units to make"), this.n(i.production_total, 2), "tot")}
			</div>${tint ? `<div class="note" style="margin-top:6px">${__("No Tier 3 on tint jobs: they are sized per unit, so there is no minimum batch to top up.")}</div>` : ""}</div>`;
		const rows = (i.rows || []).slice().sort((a, z) => String(a.at || "9").localeCompare(String(z.at || "9")));
		const orders = `<div class="panel"><h4>${__("Orders")}</h4>${rows.filter((r) => r.sales_order).map((r) =>
			this.so_line(r, r.qty) + (r.via ? `<span class="note">${__("via")} ${this.esc(r.via)}</span>` : "")).join("")
			|| `<span class="empty">${mon ? __("No orders. This is a fast-mover shelf refill.") : __("No orders.")}</span>`}</div>`;

		const type = (k) => ({ packed_base: [__("Packed base (FG)"), "c-blue"], stainer: [__("Paste (BIP)"), "c-violet"], bulk: [__("Bulk base (BIP)"), "c-cyan"] }[k] || [k, "c-grey"]);
		const in_rows = (i.inputs || []).map((x) => {
			const card = this.cards[x.item_code];
			const [label, cls] = type(x.kind);
			let avail = `<span class="note">${__("Not on the board")}</span>`;
			if (card) {
				const making = (card.cook || card.production_total || 0) > 0;
				avail = `${this.q(this.free(card.stock), x.uom)} ${__("free")}${making ? ` · ${__("being made")}: ${card.cook !== undefined ? this.q(card.cook, card.uom) : this.q(card.production_total, card.uom, 2)}` : ""}
					<br><button class="chip ${card.short > 0 || card.existing_wo_deficit > 0 ? "c-red" : "c-green"}" data-goto="${this.esc(x.item_code)}">${card.short > 0 ? __("Short: see its card") : __("Open its card")}</button>`;
			}
			return `<tr><td><b>${this.esc(x.item_code)}</b><div class="note">${this.esc(x.item_name)}</div></td><td><span class="chip ${cls}">${label}</span></td>
				<td class="num">${this.q(x.per_unit, x.uom, 4)}</td><td class="num"><b>${this.q(x.qty, x.uom)}</b></td><td>${avail}</td></tr>`;
		});
		const inputs = in_rows.length ? this.table([tint ? __("Base input") : __("Bulk input"), __("Type"), [__("Per unit (BOM)"), 1], [__("Needed"), 1], __("Availability")], in_rows) : "";

		const mats = i.materials || [];
		const made = i.production_total || 0;
		const mline = (m) => `<div class="pack" style="margin-bottom:6px">${this.esc(m.item_code)} ${this.esc(m.item_name)} · ${this.n(m.per_unit, 4)} ${this.esc(this.u(m.uom))} × ${this.n(made, 2)} = <b>${this.q(m.qty, m.uom)}</b>${m.short ? ` <button class="chip c-red" data-goto="${this.esc(m.item_code)}">${__("short")}</button>` : ""}</div>`;
		const colour = mats.filter((m) => m.kind !== "packaging");
		const packaging = mats.filter((m) => m.kind === "packaging");
		const panels = `<div class="mathgrid">
			<div class="panel"><h4>${tint ? __("Colourants (shown in full on Raw Materials)") : __("Raw materials and colourants")}</h4>${colour.map(mline).join("") || `<span class="empty">${tint ? __("No colourants. Colour comes from the paste or base.") : __("None besides the bulk.")}</span>`}</div>
			<div class="panel"><h4>${__("Packaging")}</h4>${packaging.length ? `<div class="packs">${packaging.map((m) => `<div class="pack"><b>${this.n(m.qty)}</b> × ${this.esc(m.item_name)} <span class="note">${this.esc(m.item_code)}</span></div>`).join("")}</div>`
				: `<span class="empty">${base ? __("None. The packed base already comes in its pail.") : __("No packaging in the BOM.")}</span>`}</div></div>`;

		const actions = `<div class="actions">${i.production_total > 0 && i.can_produce ? this.wo_btn(i.item_code, `＋ ${tint ? __("Tint Work Order") : __("Work Order")} (${this.n(i.production_total, 2)} ${this.esc(this.u(i.uom))})`, true) : ""}
			<button class="btn" data-rm-for="${this.esc(i.item_code)}">${tint ? __("View colourants on Raw Materials") : __("View raw materials for this card")}</button>
			${i.home_bulk && this.cards[i.home_bulk] && i.home_bulk !== i.item_code ? `<button class="btn" data-goto="${this.esc(i.home_bulk)}">${__("Open bulk card")}</button>` : ""}</div>`;

		return this.issues_box(i) + `<div class="mathgrid">${units}${orders}</div>` + inputs + panels + this.working(i) + actions;
	}

	// ================================================================ raw materials
	rm_status(m) {
		if (m.short > 0) return ["short", "c-red", __("Short"), "P1"];
		if (m.free < m.required - 1e-9) return ["po", "c-amber", __("Short now · on order"), "P2"];
		return ["ok", "c-green", __("Covered"), "P3"];
	}
	rm_cat(m) {
		return m.kind === "Packaging" ? ["c-grey", __("Packaging")] : m.kind === "Colourant" ? ["c-violet", __("Colourant (RM)")] : ["c-cyan", __("Raw Material")];
	}
	rm_text(m) {
		return [m.item_code, m.item_name, m.kind].concat((m.sources || []).map((s) => s.item_code + " " + s.item_name), (m.orders || []).map((o) => o.sales_order)).join(" ").toLowerCase();
	}
	render_rm_list(tab) {
		const q = this.state.q, sc = this.state.show_covered, by = this.state.rm_sort;
		const mats = this.mats_for(tab);
		const pack = tab === "pack";
		const pct = (m) => (m.required ? m.short / m.required : 0);
		const list = mats.filter((m) => (!q || this.rm_text(m).includes(q)) && (sc || this.rm_status(m)[0] !== "ok"))
			.sort((a, b) => by === "risk" ? b.value_at_risk - a.value_at_risk : by === "pct" ? pct(b) - pct(a) : (b.shortage_value - a.shortage_value) || (b.short - a.short));
		const hidden = mats.filter((m) => this.rm_status(m)[0] === "ok").length;
		const cats = {};
		mats.forEach((m) => { const c = this.rm_cat(m)[1]; cats[c] = (cats[c] || 0) + 1; });
		const to_request = mats.filter((m) => m.suggested_request > 0).map((m) => m.item_code);
		const MAX = 150;
		return `<div class="group"><div class="ghead"><span class="chev">▾</span><h2>${pack ? __("Packaging for open production") : __("Raw materials for open production")}</h2>
			<div class="gmeta">${Object.entries(cats).map(([c, k]) => `<span class="chip c-grey">${k} ${c}</span>`).join("")}${!sc && hidden ? `<span class="chip c-green">${hidden} ${__("covered hidden")}</span>` : ""}
			${to_request.length ? this.mr_btn(to_request.join(","), `＋ ${pack ? __("Material Request for all short packaging") : __("Material Request for all short raw materials")} (${to_request.length})`) : ""}</div></div>
			<div class="gbody">${list.slice(0, MAX).map((m) => this.render_rm(m)).join("") || `<div class="hidden-note">${__("No materials match.")}</div>`}
			${list.length > MAX ? `<div class="hidden-note">${__("Showing the first {0} of {1}. Use the search box to find the rest.", [MAX, list.length])}</div>` : ""}</div></div>`;
	}
	render_rm(m) {
		const u = m.uom;
		const [, scls, slabel, pri] = this.rm_status(m);
		const [ccls, clabel] = this.rm_cat(m);
		const short_now = Math.max(0, m.required - m.free);
		const head = `<div class="ctitle"><div class="t"><span class="pri ${pri}">${pri}</span>${this.esc(m.item_name)} <span class="code">[${this.esc(m.item_code)}]</span></div>
			<div class="sub"><span class="chip ${ccls}">${clabel}</span><span class="chip ${scls}">${slabel}</span>${m.indented > 0 ? `<span class="chip c-blue">${__("Requested")} ${this.q(m.indented, u)}</span>` : ""}${m.short > 0 && !m.valuation_rate ? `<span class="chip c-amber">⚠ ${__("no price")}</span>` : ""}${this.neg_chip(m)}</div></div>`;
		const metrics = this.metric(__("Required"), this.q(m.required, u))
			+ this.metric(__("On the shelf"), this.kg(m.shelf !== undefined ? m.shelf : Math.max(0, m.actual || 0)))
			+ (this.wo_on() && m.set_aside > 0 ? this.metric(__("Set aside"), this.kg(m.set_aside)) : "")
			+ (this.po_on() ? this.metric(__("On order"), this.kg(m.on_po)) : "")
			+ this.metric(__("Shortfall"), this.kg(m.short), m.short > 0 ? "red" : "green")
			+ this.metric(__("Shortfall value"), m.short > 0 && !m.valuation_rate ? __("Unpriced") : this.kes(m.shortage_value))
			+ this.metric(__("Sales at risk"), this.kes(m.value_at_risk), "blue");
		return this.shell(m.item_code, pri, head, metrics, () => this.rm_body(m, short_now));
	}
	rm_body(m, short_now) {
		const u = m.uom;
		const R = (l, r, cls = "", style = "") => `<span class="l ${cls}">${l}</span><span class="r ${cls}"${style ? ` style="${style}"` : ""}>${r}</span>`;
		const uses = (m.sources || []).map((s) => {
			const card = this.cards[s.item_code];
			const line = card ? (card.materials || []).find((x) => x.item_code === m.item_code) : null;
			const made = card ? (card.cook !== undefined ? card.cook : card.production_total) : 0;
			const verb = card && card.cook !== undefined ? __("Cook") : card && card.tab === "tinting" ? __("Tint") : __("Fill");
			const calc = line ? `${this.kg(made)} ${this.esc(this.u(card.uom))} × ${this.n(line.per_unit, 4)}` : "";
			return `<tr><td><b>${verb}</b> <button class="btn sm" data-goto="${this.esc(s.item_code)}">${this.esc(s.item_code)}</button><div class="note">${this.esc(s.item_name)}</div></td>
				<td>${calc}</td><td class="num"><b>${this.q(s.qty, u)}</b></td></tr>`;
		});
		const so_rows = (m.orders || []).map((o) => `<tr><td>${this.so_line(o)}<div class="note">${this.esc(o.customer || "")}</div></td><td>${this.esc((o.items || []).join(", "))}</td>
			<td class="num">${this.q(o.qty, u)}</td><td class="num">${this.kes(o.amount)}</td></tr>`);
		const wo = this.wo_on(), po = this.po_on();
		const shelf = m.shelf !== undefined ? m.shelf : Math.max(0, m.actual || 0);
		const aside = m.set_aside !== undefined ? m.set_aside : m.reserved_for_production;
		const position = `<div class="panel"><h4>${__("Stock position")} (${this.esc(this.u(u))})</h4><div class="math">
			${R(__("Required by open production"), this.kg(m.required))}
			${R(__("On the shelf (raw material warehouses)"), this.kg(shelf))}
			${wo ? R(`− ${__("Set aside for the Work Orders the board counts")}`, this.minus(aside, (x) => this.kg(x))) : ""}
			${wo && m.transferred_not_consumed > 0 ? R(`− ${__("Moved to WIP, not used yet")}`, this.minus(m.transferred_not_consumed, (x) => this.kg(x))) : ""}
			${R(__("Free"), this.kg(Math.max(0, m.free)), "tot")}
			${R(__("Shortfall now"), this.kg(short_now), "tot", `color:${short_now > 0 ? "var(--bad)" : "var(--ok)"}`)}
			${po ? R(__("On purchase order"), this.kg(m.on_po)) : ""}
			${R(po ? __("Stock after this production and POs") : __("Stock after this production"), this.kg(m.after))}
			${R(__("Already requested (Material Requests)"), this.kg(m.indented))}
			${R(__("Suggested Material Request"), this.kg(m.suggested_request), "tot")}
			</div><div class="note" style="margin-top:6px">
			${m.free < 0 ? __("The counted Work Orders need {0} more than is on the shelf; that is part of the shortfall.", [this.q(-m.free, u)]) + " " : ""}
			${po ? __("Suggested = shortfall after purchase orders − quantity already requested.") : __("Suggested = shortfall − quantity already requested. Purchase Orders are not included (Nexus Production Settings).")}
			${m.stock_building_qty > 0 ? " " + __("{0} of the requirement is for shelf refills and batch top-ups, not orders.", [this.q(m.stock_building_qty, u)]) : ""}
			${m.rate ? " " + __("Shortfall valued at {0} ({1} per {2}).", [this.esc(m.price_basis || __("valuation rate")), this.kes(m.rate), this.esc(this.u(u))]) : ""}</div></div>`;
		const justify = `<div class="panel"><h4>${__("Purchase justification")}</h4><div class="line">${__("{0} order(s) worth <b>{1}</b> wait on this material.", [this.n(m.order_count), this.kes(m.value_at_risk)])}</div>
			<div class="note" style="margin-top:8px">${__("The Material Request will carry these orders and FGs in its remarks, so purchasing can see why it was raised.")}</div>
			${(m.by_warehouse || []).length ? `<h4 style="margin-top:12px">${__("Stock by warehouse")}</h4>` + (m.by_warehouse || []).map((w) => `<div class="pack" style="margin-bottom:6px">${this.esc(w.warehouse)}: <b>${this.kg(w.shelf !== undefined ? w.shelf : Math.max(0, w.actual || 0))}</b> ${__("on the shelf")}${(w.set_aside || 0) > 0 ? ` · ${this.kg(w.set_aside)} ${__("set aside")}` : ""}${w.actual < 0 ? ` · <span class="chip c-amber">${__("ERPNext shows")} ${this.kg(w.actual)}</span>` : ""}</div>`).join("") : ""}</div>`;
		return `<div class="sect">${__("Consumed by")}${m.source_count > uses.length ? ` · ${__("top {0} of {1}", [uses.length, m.source_count])}` : ""}</div>`
			+ this.table([__("Consumed by"), __("Calculation (BOM)"), [__("Qty"), 1]], uses, true)
			+ `<div class="mathgrid">${position}${justify}</div>`
			+ `<div class="sect">${__("Sales orders depending on it")}${m.order_count > so_rows.length ? ` · ${__("{0} of {1}", [so_rows.length, m.order_count])}` : ""}</div>`
			+ this.table([__("Sales order"), __("Items"), [__("Share of requirement"), 1], [__("Order value"), 1]], so_rows, true, __("Only shelf refills and batch top-ups need it."))
			+ `<div class="actions">${m.suggested_request > 0 ? this.mr_btn(m.item_code, `＋ ${__("Material Request")} (${this.kg(m.suggested_request)} ${this.u(u)})`, true) : ""}
				<a class="btn" href="/app/query-report/Stock Ledger?item_code=${encodeURIComponent(m.item_code)}" target="_blank" rel="noopener">${__("Stock ledger")}</a></div>`;
	}

	// ================================================================ needs setup
	render_setup() {
		const q = this.state.q;
		const sev = { error: ["High", "c-red"], warning: ["Medium", "c-amber"], info: ["Info", "c-grey"] };
		const rank = { error: 0, warning: 1, info: 2 };
		const list = this.issues.filter((i) => !q || [i.item, i.item_name, i.title, i.detail, i.ref].join(" ").toLowerCase().includes(q))
			.sort((a, b) => (b.in_plan - a.in_plan) || (rank[a.severity] - rank[b.severity]) || String(a.item).localeCompare(String(b.item)));
		const by_title = {};
		this.issues.forEach((i) => (by_title[i.title] = (by_title[i.title] || 0) + 1));
		const MAX = 200;
		const rows = list.slice(0, MAX).map((i) => {
			const [label, cls] = sev[i.severity] || sev.info;
			const doc = i.ref && /^BOM-/.test(i.ref) && !/->/.test(i.ref) ? this.link("BOM", i.ref) : this.esc(i.ref || "—");
			return `<tr><td><span class="chip ${cls}">${label}</span>${i.in_plan ? `<div class="note">${__("in today's plan")}</div>` : ""}</td>
				<td><b>${this.esc(i.title)}</b></td>
				<td>${i.item ? this.link("Item", i.item) : "—"}${i.item_name && i.item_name !== i.item ? `<div class="note">${this.esc(i.item_name)}</div>` : ""}</td>
				<td>${doc}</td><td>${this.esc(i.detail)}</td><td>${this.esc(i.fix && i.fix !== "Info" ? i.fix : "")}</td></tr>`;
		});
		let html = `<div class="legend">${Object.entries(by_title).sort((a, b) => b[1] - a[1]).map(([t, k]) => `<span class="chip c-grey">${k} · ${this.esc(t)}</span>`).join("")}</div>`;
		html += this.table([__("Severity"), __("Check"), __("Item"), __("Document"), __("Problem"), __("Fix")], rows, false, q ? __("Nothing matches the search.") : __("No setup problems found."));
		if (list.length > MAX) html += `<div class="hidden-note">${__("Showing {0} of {1}, most urgent first. Use the search box to find a specific item or check.", [MAX, list.length])}</div>`;
		if (this.issues.some((i) => i.code === "outdated_sub_bom")) {
			html += `<div class="banner info">${__("Outdated sub-BOM links can be fixed all at once in Nexus Production Settings > Replace Outdated Sub-BOM Links.")}${(this.result.page || {}).can_configure ? ` <button class="btn sm" data-action="open-settings-form">${__("Open settings")}</button>` : ""}</div>`;
		}
		const b = this.result.board;
		const owo = b.older_work_orders || [];
		if (owo.length) {
			html += `<div class="sect">${__("Older Work Orders not deducted")} (${owo.length})</div>` + this.table([__("Work Order"), __("Item"), __("Status"), __("Planned"), [__("Remaining"), 1]],
				owo.slice(0, 100).map((w) => `<tr><td>${this.link("Work Order", w.name)}</td><td><b>${this.esc(w.item_code)}</b><div class="note">${this.esc(w.item_name)}</div></td><td>${this.esc(w.status)}</td><td>${this.esc(this.when(w.planned_start_date))}</td><td class="num">${this.kg(w.remaining)}</td></tr>`), true);
		}
		const ex = (b.excluded_lines || []).map((e) => [e.sales_order, e.item_code, e.qty, __("Route set to Exclude from Production Card")])
			.concat((b.not_manufactured || []).map((nm) => [null, nm.item_code, nm.qty, __("No BOM and its item group is not on the page (traded item)")]));
		if (ex.length) {
			html += `<div class="sect">${__("Order lines not planned")} (${ex.length})</div>` + this.table([__("Sales order"), __("Item"), [__("Qty"), 1], __("Why")],
				ex.slice(0, 100).map(([so, item, qty, why]) => `<tr><td>${so ? this.link("Sales Order", so) : "—"}</td><td>${this.esc(item)}</td><td class="num">${this.n(qty, 2)}</td><td>${why}</td></tr>`), true);
		}
		html += `<div class="panel"><h4>${__("Checks the engine runs on every sync")}</h4><div class="note" style="font-size:13px;line-height:1.8">
			• ${__("Ordered FG with no submitted default BOM")}<br>• ${__("Bulk ticked Is BIP with no BOM, or a BOM with Quantity 0")}<br>• ${__("Item made from raw materials that looks like an untagged bulk")}<br>
			• ${__("BOM that contains itself, or a circular chain of BOMs")}<br>• ${__("Bulk ingredients outside 1 kg per kg (Mass Balance Tolerance in Settings)")}<br>
			• ${__("Parent BOMs pointing at an older version of a component BOM")}<br>• ${__("Linked BIP that disagrees with the BOM")}<br>• ${__("Packed base not 1 per unit; bulk BOM rows not in kg")}</div></div>`;
		return html;
	}

	// ================================================================ actions (Batch 5)
	action_args(extra) {
		return Object.assign({ mode: this.state.mode, from_date: this.state.from, to_date: this.state.to }, extra);
	}
	action_call(method, args) {
		return new Promise((resolve, reject) => frappe.call({
			method: `nexus_supply_chain.production.actions.${method}`,
			type: "POST",
			args: this.action_args(args),
			freeze: true,
			freeze_message: __("Checking the board..."),
			callback: (r) => resolve(r.message),
			error: reject,
		}));
	}
	drafts_html(drafts, doctype) {
		if (!(drafts || []).length) return "";
		return `<div class="alert alert-warning" style="margin-top:10px">${__("Draft {0}s already exist for this item:", [doctype])} ${drafts.map((d) =>
			this.link(doctype, d.name) + ` (${this.n(d.qty, 2)})`).join(", ")}. ${__("Check them before creating another.")}</div>`;
	}
	created(r) {
		const url = `/app/${frappe.router.slug(r.doctype)}/${encodeURIComponent(r.name)}`;
		frappe.msgprint({
			title: __("Draft created"),
			indicator: "green",
			message: `${__("{0} {1} was created as a draft. Review it and submit when ready.", [__(r.doctype), `<a href="${url}" target="_blank" rel="noopener"><b>${this.esc(r.name)}</b></a>`])}
				${r.link_note ? `<div class="text-muted" style="margin-top:8px">${this.esc(r.link_note)}</div>` : ""}
				<div style="margin-top:12px"><a class="btn btn-primary btn-sm" href="${url}" target="_blank" rel="noopener">${__("Open {0}", [this.esc(r.name)])}</a></div>`,
		});
		this.load(false);
	}
	async action_wo(code) {
		let p;
		try { p = await this.action_call("work_order", { item_code: code, commit: 0 }); } catch (e) { return; }
		const row = (k, v) => `<tr><td class="text-muted" style="width:170px">${k}</td><td><b>${v}</b></td></tr>`;
		const d = new frappe.ui.Dialog({
			title: __("New Work Order (draft)"),
			fields: [{ fieldtype: "HTML", options: `<table class="table table-condensed" style="margin:0">
				${row(__("Item"), `${this.esc(p.item_code)} · ${this.esc(p.item_name || "")}`)}
				${row(__("Quantity"), `${this.n(p.qty, 3)} ${this.esc(this.u(p.uom))}`)}
				${row(__("BOM"), this.esc(p.bom))}
				${row(__("Target warehouse"), this.esc(p.fg_warehouse))}
				${row(__("Work-in-progress warehouse"), this.esc(p.wip_warehouse || "—"))}
				${row(__("Sales Order"), p.sales_order ? this.esc(p.sales_order) : `<span class="text-muted">${__("not linked")}</span>`)}
				${row(__("Made for"), this.esc((p.orders || []).join(", ") || __("shelf refill / batch top-up")))}
				</table>${p.link_note ? `<div class="text-muted" style="margin-top:8px">${this.esc(p.link_note)}</div>` : ""}
				${this.drafts_html(p.drafts, "Work Order")}
				<div class="text-muted" style="margin-top:10px">${__("Quantity comes from the current board. The draft is not counted on the page until it is submitted.")}</div>` }],
			primary_action_label: __("Create draft Work Order"),
			primary_action: async () => {
				d.hide();
				try { this.created(await this.action_call("work_order", { item_code: code, commit: 1 })); } catch (e) { /* message already shown */ }
			},
		});
		d.show();
	}
	async action_mr(codes) {
		const args = codes ? { item_codes: JSON.stringify(codes) } : { all_short: 1 };
		let p;
		try { p = await this.action_call("material_request", Object.assign({ commit: 0 }, args)); } catch (e) { return; }
		const rows = (p.lines || []).map((l) => `<tr><td><b>${this.esc(l.item_code)}</b><div class="text-muted small">${this.esc(l.item_name || "")}</div></td>
			<td class="text-right">${this.n(l.qty, 3)} ${this.esc(this.u(l.uom))}</td><td>${this.esc(l.warehouse || "")}</td><td>${this.esc(l.schedule_date)}</td>
			<td class="text-right">${this.kes(l.shortage_value)}</td></tr>${(l.drafts || []).length ? `<tr><td colspan="5">${this.drafts_html(l.drafts, "Material Request")}</td></tr>` : ""}`).join("");
		const d = new frappe.ui.Dialog({
			title: __("New Material Request (Purchase, draft)"),
			size: (p.lines || []).length > 1 ? "large" : "",
			fields: [{ fieldtype: "HTML", options: `<div style="max-height:420px;overflow:auto"><table class="table table-condensed" style="margin:0">
				<thead><tr><th>${__("Material")}</th><th class="text-right">${__("Qty")}</th><th>${__("Warehouse")}</th><th>${__("Required by")}</th><th class="text-right">${__("Shortfall value")}</th></tr></thead>
				<tbody>${rows}</tbody></table></div>
				<div style="margin-top:8px"><b>${__("Total")}: ${this.kes(p.total_value)}</b> · ${__("{0} line(s)", [(p.lines || []).length])}</div>
				<div class="text-muted" style="margin-top:8px">${__("Quantity = shortfall after purchase orders, minus what is already on submitted Material Requests. Required-by date uses each item's lead time.")}</div>` }],
			primary_action_label: __("Create draft Material Request"),
			primary_action: async () => {
				d.hide();
				try { this.created(await this.action_call("material_request", Object.assign({ commit: 1 }, args))); } catch (e) { /* message already shown */ }
			},
		});
		d.show();
	}

	// ================================================================ pending Work Orders & Purchase Orders (Batch 8)
	load_pending(more) {
		const P = this.pend;
		if (P.loading) return;
		P.loading = true;
		P.error = false;
		P.q = this.state.q;
		const key = (k) => (k === "wo" ? "work_orders" : "purchase_orders");
		const start = (k) => (more === k && P.data ? P.data[key(k)].rows.length : 0);
		frappe.call({
			method: "nexus_supply_chain.production.pending.get_pending",
			args: { wo_start: start("wo"), po_start: start("po"), page_length: 50, search: P.q || "", flagged_only: P.flagged_only ? 1 : 0 },
			callback: (r) => {
				const d = r.message;
				if (!d) return;
				if (more && P.data) {
					const k = key(more);
					P.data[k] = Object.assign({}, d[k], { rows: P.data[k].rows.concat(d[k].rows) });
				} else {
					P.data = d;
				}
				P.loaded_at = Date.now();
				P.stale = false;
			},
			error: () => (P.error = true),
			always: () => {
				P.loading = false;
				if (this.state.tab === "pending") {
					this.render_tabs();
					this.render_content();
				}
			},
		});
	}
	render_pending() {
		const P = this.pend;
		const outdated = !P.data || P.stale || P.q !== this.state.q || Date.now() - P.loaded_at > 60000;
		if (outdated && !P.loading && !P.error) this.load_pending();
		if (P.error) {
			return `<div class="banner error">${__("The pending lists could not be loaded.")} <button class="btn sm" data-action="pend-retry">${__("Try again")}</button></div>`;
		}
		if (!P.data) return `<div class="skeleton">${__("Reading open Work Orders and Purchase Orders...")}</div>`;
		const d = P.data, cfg = d.settings || {};
		const seg = `<div class="seg" role="group" aria-label="${__("Show")}">
			<button data-pend-flag="1" aria-pressed="${P.flagged_only}">${__("Needing attention")}</button>
			<button data-pend-flag="0" aria-pressed="${!P.flagged_only}">${__("All open")}</button></div>`;
		const read_at = P.loaded_at ? frappe.datetime.obj_to_str(new Date(P.loaded_at)).slice(11, 16) : "";
		const head = `<div class="pend-head">${seg}<span class="note">${__("Flagged: no progress, or overdue, for more than {0} days. Work Orders are also flagged when older than {1} days. Stopped Work Orders are not listed: the board never counts them.", [cfg.stale_days, cfg.wo_age_limit_days])}
			· ${P.loading ? __("Updating...") : `${__("read at")} ${read_at}`}</span></div>`;
		const flags = (list) => (list || []).map((f) => `<span class="chip ${/Overdue|Older|Stopped|Nothing received/.test(f) ? "c-red" : "c-amber"}">${this.esc(f)}</span>`).join("");
		const counted = (on) => `<span class="chip ${on ? "c-green" : "c-grey"}">${on ? __("Counted on the board") : __("Not counted")}</span>`;
		const status_cls = { "Not Started": "c-blue", "In Process": "c-amber", Stopped: "c-red" };
		const day = (v) => this.esc(frappe.datetime.str_to_user(v));
		const wo_rows = d.work_orders.rows.map((w) => `<div class="prow">
			<div class="prow-top">${this.link("Work Order", w.name)}<span class="chip ${status_cls[w.status] || "c-grey"}">${this.esc(w.status)}</span>${counted(w.counted)}</div>
			<div class="prow-item"><b>${this.esc(w.item_code)}</b> ${this.esc(w.item_name && w.item_name !== w.item_code ? w.item_name : "")}</div>
			<div class="note">${__("Made")} <b>${this.n(w.produced_qty, 2)} / ${this.n(w.qty, 2)}</b> ${this.esc(this.u(w.uom))} · ${__("planned")} ${day(w.planned)} (${__("{0} days ago", [w.age_days])})${w.lines_in_wip ? ` · <b>${__("{0} material line(s) in WIP", [w.lines_in_wip])}</b>` : ""}${w.sales_order ? ` · ${this.link("Sales Order", w.sales_order)}` : ""}</div>
			${w.flags.length ? `<div class="prow-flags">${flags(w.flags)}</div>` : ""}
			${w.action ? `<div class="prow-action">→ ${this.esc(w.action)}</div>` : ""}</div>`).join("");
		const po_rows = d.purchase_orders.rows.map((p) => `<div class="prow">
			<div class="prow-top">${this.link("Purchase Order", p.name)}<span class="chip c-grey">${this.esc(p.status)}</span>${counted(p.counted)}</div>
			<div class="prow-item"><b>${this.esc(p.supplier_name)}</b> · ${this.esc(p.items.join(", "))}${p.more_items ? ` +${p.more_items}` : ""}</div>
			<div class="note">${__("Due")} ${day(p.due)}${p.overdue_days ? ` · <b>${__("{0} days overdue", [p.overdue_days])}</b>` : ""} · ${this.n(p.per_received, 1)}% ${__("received")} · ${__("last receipt")} ${p.last_receipt ? day(p.last_receipt) : __("none")} · ${__("still to come")} <b>${this.kes(p.pending_value)}</b></div>
			${p.flags.length ? `<div class="prow-flags">${flags(p.flags)}</div>` : ""}
			${p.action ? `<div class="prow-action">→ ${this.esc(p.action)}</div>` : ""}</div>`).join("");
		const col = (title, part, rows, kind, note) => `<section class="pend-col">
			<div class="pend-title"><h3>${title}</h3><span class="chip ${part.flagged ? "c-red" : "c-green"}">${this.n(part.total)} ${P.flagged_only ? __("flagged") : __("open")}</span></div>
			${note ? `<div class="note" style="margin-bottom:6px">${note}</div>` : ""}
			${rows || `<div class="hidden-note">${P.flagged_only ? __("Nothing needs attention.") : __("None open.")}</div>`}
			${part.rows.length < part.total ? `<div class="actions"><button class="btn sm" data-pend-more="${kind}">${__("Show more")} (${this.n(part.total - part.rows.length)})</button></div>` : ""}</section>`;
		return head + `<div class="pend-grid">
			${col(__("Work Orders"), d.work_orders, wo_rows, "wo", cfg.include_work_orders ? "" : __("Work Orders are not included in the board (Nexus Production Settings); listed here for clean-up only."))}
			${col(__("Purchase Orders"), d.purchase_orders, po_rows, "po", cfg.include_purchase_orders ? "" : __("Purchase Orders are not included in the board (Nexus Production Settings); listed here for clean-up only."))}
			</div>`;
	}

	// ================================================================ settings drawer
	open_drawer() {
		const b = (this.result || {}).board || {}, s = b.settings_used || {}, m = (this.result || {}).meta || {}, w = m.window || {}, p = (this.result || {}).page || {};
		const f = (k, v) => `<div class="field"><div class="k">${k}</div><div class="v">${this.esc(v === undefined || v === null || v === "" ? "—" : v)}</div></div>`;
		const yes = (v) => (v ? __("Yes") : __("No"));
		this.el.drawer.html(`<div style="display:flex;justify-content:space-between;align-items:center;gap:10px">
				<div><h3>${__("Nexus Production Settings")}</h3><div class="note">${__("Single doctype · nothing hard-coded in the page")}</div></div>
				<button class="btn" data-action="close-drawer">✕ ${__("Close")}</button></div>
			${f(__("Orders confirmed in this view"), `${w.from_date ? frappe.datetime.str_to_user(w.from_date) : ""} → ${w.to_date ? frappe.datetime.str_to_user(w.to_date) : ""}${w.is_default_window ? " (" + __("default window") + ")" : ""}`)}
			${f(__("Orders counted"), __("Submitted, not closed, nothing delivered or invoiced, and no Delivery Note or Sales Invoice linked yet"))}
			${f(__("Finished Goods Warehouse"), s.fg_warehouse)}
			${f(__("Bulk Warehouses"), (s.bulk_warehouses || []).join(", "))}
			${f(__("Colourant Warehouse"), s.colourant_warehouse)}
			${f(__("Raw Material Warehouses"), (s.rm_warehouses || []).join(", "))}
			${f(__("Packaging Item Group"), s.packaging_item_group)}
			${f(__("Work Orders"), this.wo_on() ? __("Included") + " · " + [s.count_not_started ? __("Not Started") : "", s.count_in_process ? __("In Process") : ""].filter(Boolean).join(", ") + ` · ${__("planned in the last {0} days", [s.wo_age_limit_days])} · ${__("Stopped never")} · ${__("materials set aside only for these")}` : __("Not included: no Work Order is deducted or shown"))}
			${f(__("Purchase Orders"), this.po_on() ? __("Included: open PO quantities count as incoming") : __("Not included: shortfall = requirement − free stock"))}
			${f(__("Price for shortfall value"), s.material_price_basis || __("Valuation Rate"))}
			${f(__("Shelf refills without orders"), yes(s.include_shelf_without_orders))}
			${f(__("Excess fill rule"), s.excess_fill_rule)}
			${f(__("Tint base priority"), s.tint_base_priority)}
			${f(__("Mass balance tolerance"), s.mass_balance_tolerance + " %")}
			${f(__("Refresh"), __("{0} min by day, {1} min at night", [p.refresh_minutes, p.night_refresh_minutes]))}
			${f(__("Engine"), `${b.engine_version || ""} · ${this.n(m.timing_ms)} ms · ${m.cached ? __("served from cache") : __("freshly calculated")}`)}
			${f(__("Snapshot"), m.snapshot ? __("{0} items, {1} BOMs, {2} orders ({3} lines), {4} Work Orders", [m.snapshot.items, m.snapshot.boms, m.snapshot.orders, m.snapshot.order_lines, m.snapshot.work_orders]) : "")}
			${p.can_configure ? `<div class="actions"><button class="btn primary" data-action="open-settings-form">${__("Open Nexus Production Settings")}</button></div>` : ""}`);
		this.el.overlay.addClass("show");
		this.el.drawer.addClass("show");
	}
	close_drawer() {
		if (!this.el) return;
		this.el.overlay.removeClass("show");
		this.el.drawer.removeClass("show");
	}
};
