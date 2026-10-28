// apps/nexus_supply_chain/nexus_supply_chain/public/js/nexus_dispatch_socket.js
//
// The dispatch pages' live feed: FastAPI /api/v2/fleet/ws (Batch 3).
//
//   const sock = new NexusDispatchSocket({
//     kind: "driver" | "sales",
//     onSnapshot(items), onDelta(items), onManifests(msg), onSettings(settings),
//     onState(state)   // "connecting" | "open" | "reconnecting" | "denied" | "offline"
//   });
//   sock.start();  ...  sock.stop();
//
// - Every (re)connect asks ERPNext for a fresh 10-minute ticket
//   (driver_api.get_dispatch_ws_ticket); the ticket is only checked when the
//   socket opens, so an open socket stays open.
// - Reconnects with exponential backoff + jitter (1 s .. 30 s); immediately
//   when the browser comes back online.
// - Heartbeat every 25 s; a socket silent for 70 s is treated as dead.
// - After the tab was hidden for a while, asks the server for a full
//   snapshot ("resync") instead of trusting deltas it may have missed.
// - Close code 4403 (no access to this kind) stops reconnecting.
(function () {
	"use strict";
	if (window.NexusDispatchSocket) return;

	const TICKET_METHOD = "nexus_supply_chain.driver_api.get_dispatch_ws_ticket";
	const PING_MS = 25000;
	const DEAD_MS = 70000;
	const RESYNC_AFTER_HIDDEN_MS = 60000;

	class NexusDispatchSocket {
		constructor(options) {
			this.o = options || {};
			this.kind = this.o.kind || "driver";
			this.ws = null;
			this.attempt = 0;
			this.stopped = true;
			this.timers = {};
			this.lastMessageAt = 0;
			this.hiddenAt = 0;
			this.state = "idle";
			this._onOnline = () => this._reconnectNow();
			this._onVisibility = () => this._visibility();
		}

		start() {
			if (!this.stopped) return;
			this.stopped = false;
			window.addEventListener("online", this._onOnline);
			document.addEventListener("visibilitychange", this._onVisibility);
			this._connect();
		}

		stop() {
			this.stopped = true;
			window.removeEventListener("online", this._onOnline);
			document.removeEventListener("visibilitychange", this._onVisibility);
			Object.values(this.timers).forEach((t) => clearTimeout(t) || clearInterval(t));
			this.timers = {};
			if (this.ws) {
				this.ws.onclose = null;
				try {
					this.ws.close(1000, "page");
				} catch (e) {
					/* already closed */
				}
				this.ws = null;
			}
		}

		send(payload) {
			if (this.ws && this.ws.readyState === WebSocket.OPEN) this.ws.send(JSON.stringify(payload));
		}

		resync() {
			this.send({ action: "resync" });
		}

		_setState(state, detail) {
			this.state = state;
			if (this.o.onState) this.o.onState(state, detail);
		}

		/**
		 * Fetches a ticket quietly (no frappe.call error dialogs on every retry).
		 * Throws {denied, message} on 403, {message} otherwise.
		 */
		async _ticket() {
			let res;
			try {
				res = await fetch(`/api/method/${TICKET_METHOD}`, {
					method: "GET",
					credentials: "same-origin",
					headers: { Accept: "application/json", "X-Frappe-CSRF-Token": frappe.csrf_token || "" },
				});
			} catch (e) {
				throw { message: __("No connection to ERPNext.") };
			}
			let body = {};
			try {
				body = await res.json();
			} catch (e) {
				body = {};
			}
			let serverMessage = "";
			try {
				const msgs = JSON.parse(body._server_messages || "[]");
				serverMessage = msgs.length ? JSON.parse(msgs[0]).message || "" : "";
			} catch (e) {
				serverMessage = "";
			}
			if (res.status === 403 || body.exc_type === "PermissionError") {
				throw { denied: true, message: serverMessage || __("You don't have access to this live map.") };
			}
			const msg = body.message;
			if (!res.ok || !msg || msg.status !== "success" || !msg.ticket || !msg.ws_url) {
				throw { message: frappe.utils.strip_html(serverMessage) || __("Couldn't get a live-map ticket ({0}).", [res.status]) };
			}
			return msg;
		}

		async _connect() {
			if (this.stopped) return;
			this._setState(this.attempt ? "reconnecting" : "connecting");
			let ticket;
			try {
				ticket = await this._ticket();
			} catch (e) {
				if (e && e.denied) {
					this._setState("denied", e.message);
					return;
				}
				this.lastError = (e && e.message) || null;
				this._scheduleReconnect();
				return;
			}
			this.lastError = null;
			if (this.stopped) return;
			if (ticket.scopes && !ticket.scopes.includes(this.kind === "sales" ? "sales" : "fleet")) {
				this._setState("denied", __("You don't have access to this live map."));
				return;
			}

			const url = `${ticket.ws_url}?ticket=${encodeURIComponent(ticket.ticket)}&kind=${encodeURIComponent(this.kind)}`;
			let ws;
			try {
				ws = new WebSocket(url);
			} catch (e) {
				this._scheduleReconnect();
				return;
			}
			this.ws = ws;

			ws.onopen = () => {
				this.attempt = 0;
				this.lastMessageAt = Date.now();
				this._setState("open");
				this._clear("ping");
				this.timers.ping = setInterval(() => {
					if (Date.now() - this.lastMessageAt > DEAD_MS) {
						try {
							ws.close(4000, "silent");
						} catch (e) {
							/* ignore */
						}
						return;
					}
					this.send({ action: "ping" });
				}, PING_MS);
			};

			ws.onmessage = (event) => {
				this.lastMessageAt = Date.now();
				let msg;
				try {
					msg = JSON.parse(event.data);
				} catch (e) {
					return;
				}
				try {
					this._route(msg);
				} catch (e) {
					console.error("[NexusDispatchSocket] handler failed", e, msg);
				}
			};

			ws.onclose = (event) => {
				this._clear("ping");
				if (this.ws === ws) this.ws = null;
				if (this.stopped) return;
				if (event.code === 4403) {
					this._setState("denied", __("You don't have access to this live map."));
					return;
				}
				this._scheduleReconnect();
			};

			ws.onerror = () => {
				/* onclose follows and schedules the reconnect */
			};
		}

		_route(msg) {
			switch (msg.type) {
				case "hello":
					if (this.o.onHello) this.o.onHello(msg);
					if (msg.settings && this.o.onSettings) this.o.onSettings(msg.settings);
					break;
				case "snapshot":
					if (this.o.onSnapshot) this.o.onSnapshot(msg.items || [], msg.server_time);
					break;
				case "delta":
					if (this.o.onDelta) this.o.onDelta(msg.items || [], msg.server_time);
					break;
				case "manifests":
					if (this.o.onManifests) this.o.onManifests(msg);
					break;
				case "settings":
					if (this.o.onSettings) this.o.onSettings(msg.settings || {});
					break;
				default:
					break; // pong and future message types
			}
		}

		_clear(name) {
			if (this.timers[name]) {
				clearTimeout(this.timers[name]);
				clearInterval(this.timers[name]);
				delete this.timers[name];
			}
		}

		_scheduleReconnect() {
			if (this.stopped) return;
			this._clear("reconnect");
			if (!navigator.onLine) {
				this._setState("offline");
				return; // the "online" event reconnects
			}
			const base = Math.min(30000, 1000 * Math.pow(2, this.attempt));
			const delay = Math.round(base / 2 + Math.random() * (base / 2));
			this.attempt += 1;
			this._setState("reconnecting", { inMs: delay, error: this.lastError || null });
			this.timers.reconnect = setTimeout(() => this._connect(), delay);
		}

		_reconnectNow() {
			if (this.stopped || (this.ws && this.ws.readyState === WebSocket.OPEN)) return;
			this._clear("reconnect");
			this.attempt = 0;
			this._connect();
		}

		_visibility() {
			if (document.hidden) {
				this.hiddenAt = Date.now();
				return;
			}
			if (this.hiddenAt && Date.now() - this.hiddenAt > RESYNC_AFTER_HIDDEN_MS) this.resync();
			this.hiddenAt = 0;
			this._reconnectNow();
		}
	}

	window.NexusDispatchSocket = NexusDispatchSocket;
})();
