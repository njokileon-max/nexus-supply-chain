// apps/nexus_supply_chain/nexus_supply_chain/public/js/nexus_gmaps_loader.js
//
// Loads the Google Maps JavaScript API ON DEMAND, the way Google documents
// it ("dynamic library import"): nothing is downloaded until a page asks for
// a library, and each library (maps, marker, ...) is fetched only once.
//
//   const { Map } = await window.NexusGMaps.importLibrary("maps");
//
// Channel: "quarterly" (Google's stable channel, updated four times a year),
// so a weekly Google release can never change the dispatch pages under the
// team's feet.
//
// Key: the WEB key from Nexus App Settings (frappe.boot.nexus_maps
// .google_maps_web_api_key), restricted in Google Cloud to the ERPNext
// address (HTTP referrers) and to "Maps JavaScript API". It is NOT the
// Android key used by the driver app.
//
// Auth problems (wrong key, referrer not allowed, billing off) are reported
// by Google through window.gm_authFailure; listeners registered with
// NexusGMaps.onAuthFailure() are told, so a page can switch to the hosted map.
(function () {
	"use strict";
	if (window.NexusGMaps) return;

	const CHANNEL = "quarterly";
	const authListeners = new Set();
	let authFailed = false;
	let bootstrapped = false;
	let scriptPromise = null;
	const requested = new Set();

	const previousAuthFailure = window.gm_authFailure;
	window.gm_authFailure = function () {
		authFailed = true;
		authListeners.forEach((fn) => {
			try {
				fn();
			} catch (e) {
				console.error("[NexusGMaps] auth listener failed", e);
			}
		});
		if (typeof previousAuthFailure === "function") previousAuthFailure();
	};

	function settings() {
		return (window.frappe && frappe.boot && frappe.boot.nexus_maps) || {};
	}

	function apiKey() {
		return String(settings().google_maps_web_api_key || "").trim();
	}

	/**
	 * Google's bootstrap, written out: defines google.maps.importLibrary as a
	 * stub that loads the API script once (with every library requested so
	 * far), then hands over to Google's real importLibrary.
	 */
	function bootstrap() {
		if (bootstrapped) return;
		bootstrapped = true;

		const google = (window.google = window.google || {});
		const maps = (google.maps = google.maps || {});
		if (maps.importLibrary) return; // already loaded by someone else

		const load = () => {
			if (scriptPromise) return scriptPromise;
			scriptPromise = new Promise((resolve, reject) => {
				const params = new URLSearchParams();
				params.set("key", apiKey());
				params.set("v", CHANNEL);
				params.set("libraries", [...requested].join(","));
				params.set("callback", "google.maps.__nexus_ready");
				const lang = (window.frappe && frappe.boot && frappe.boot.lang) || "";
				if (lang) params.set("language", lang);

				maps.__nexus_ready = () => resolve();
				const script = document.createElement("script");
				script.src = `https://maps.googleapis.com/maps/api/js?${params.toString()}`;
				script.async = true;
				const nonced = document.querySelector("script[nonce]");
				if (nonced && nonced.nonce) script.nonce = nonced.nonce;
				script.onerror = () => {
					scriptPromise = null;
					reject(new Error("The Google Maps JavaScript API could not load (network or blocked)."));
				};
				document.head.appendChild(script);
			});
			return scriptPromise;
		};

		const stub = (name, ...rest) => {
			requested.add(name);
			// Google replaces maps.importLibrary with the real one once loaded.
			return load().then(() => {
				if (maps.importLibrary === stub) throw new Error("Google Maps loaded without importLibrary.");
				return maps.importLibrary(name, ...rest);
			});
		};
		maps.importLibrary = stub;
	}

	window.NexusGMaps = {
		CHANNEL,

		/** True when a web key is configured in Nexus App Settings. */
		isConfigured() {
			return !!apiKey();
		},

		/** True after Google rejected the key on this page load. */
		authFailed() {
			return authFailed;
		},

		onAuthFailure(fn) {
			authListeners.add(fn);
			return () => authListeners.delete(fn);
		},

		/** Resolves a Google Maps library ("maps", "marker", "geometry", ...). */
		importLibrary(name) {
			if (!apiKey()) {
				return Promise.reject(new Error("No Google Maps web key in Nexus App Settings."));
			}
			if (authFailed) {
				return Promise.reject(new Error("Google rejected the Maps key on this page."));
			}
			bootstrap();
			return window.google.maps.importLibrary(name);
		},
	};
})();
