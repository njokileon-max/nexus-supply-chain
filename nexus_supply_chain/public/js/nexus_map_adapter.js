// apps/nexus_supply_chain/nexus_supply_chain/public/js/nexus_map_adapter.js
//
// ONE map API for every Nexus desk page, with two interchangeable engines:
//
//   "google"  Google Maps JavaScript API (quarterly channel, loaded on demand
//             by nexus_gmaps_loader.js). Uses Advanced Markers when a Map ID
//             is set in Nexus App Settings, otherwise HTML overlay markers
//             (no Map ID needed, no deprecated google.maps.Marker).
//   "hosted"  MapLibre GL JS 6 (self-hosted under /assets) on your own tile
//             server (Nexus App Settings -> Hosted Tile URL).
//
// Pages never touch an engine directly. They describe what should be on the
// map (markers, lines, popups, camera) through NexusMapView; the view keeps
// that description, so switching engine (the toggle) rebuilds the new map
// with exactly the same pins and lines.
//
//   const view = await NexusMaps.createView(element, { page: "live_dispatch" });
//   view.setMarker("dev:abc", { lat, lng, element, anchor: "center", zIndex: 50, onClick });
//   view.animateMarker("dev:abc", lat, lng);          // smooth movement
//   view.setLine("route", [[lat, lng], ...], { color: "#2563eb", width: 5 });
//   view.fitPoints([[lat, lng], ...]);
//   view.showPopup(htmlOrElement, lat, lng);
//   await view.switchEngine("hosted");
//
// Marker elements: the engine positions a marker through the ROOT element's
// CSS transform, so pages must not put their own transform on that root (put
// it on an inner element). Hiding uses visibility, never display.
//
// Helpers: NexusMaps.decodePolyline(), NexusMaps.geojsonLines(),
// NexusMaps.haversineM(), NexusMaps.formatDistance(), NexusMaps.formatDuration(),
// NexusMaps.timeAgo(), NexusMaps.loadTurf().
(function () {
	"use strict";
	if (window.NexusMaps) return;

	const ASSETS = "/assets/nexus_supply_chain";
	const MAPLIBRE_VERSION = "6.13.0";
	const MAPLIBRE_DIR = `${ASSETS}/vendor/maplibre-gl-${MAPLIBRE_VERSION}`;
	const TURF_URL = `${ASSETS}/vendor/turf-7.4.0/turf.min.js`;
	const GMAPS_LOADER_URL = `${ASSETS}/js/nexus_gmaps_loader.js`;
	const FALLBACK_TILE_URL = "https://maps.crystalapps.dev/styles/basic-preview/style.json";
	const DEFAULT_CENTER = [-1.2921, 36.8219]; // Nairobi
	const ENGINE_KEY = "nexus_map_engine";
	/** An engine that hasn't drawn within this time is reported, never waited on forever. */
	const ENGINE_READY_TIMEOUT_MS = 8000;
	const GOOGLE_LOAD_TIMEOUT_MS = 15000;

	// ------------------------------------------------------------ settings
	function settings() {
		return (window.frappe && frappe.boot && frappe.boot.nexus_maps) || {};
	}

	function hostedTileUrl() {
		return String(settings().hosted_tile_url || "").trim() || FALLBACK_TILE_URL;
	}

	function googleConfigured() {
		return !!String(settings().google_maps_web_api_key || "").trim();
	}

	function availableEngines() {
		const out = ["hosted"];
		if (googleConfigured() && !(window.NexusGMaps && NexusGMaps.authFailed())) out.unshift("google");
		return out;
	}

	function storageKey() {
		const user = (window.frappe && frappe.session && frappe.session.user) || "guest";
		return `${ENGINE_KEY}:${user}`;
	}

	/** The engine to start with: this user's last choice, else the settings default. */
	function preferredEngine() {
		const available = availableEngines();
		let saved = null;
		try {
			saved = localStorage.getItem(storageKey());
		} catch (e) {
			saved = null;
		}
		if (saved && available.includes(saved)) return saved;
		const def = settings().default_engine === "hosted" ? "hosted" : "google";
		return available.includes(def) ? def : available[0];
	}

	function rememberEngine(engine) {
		try {
			localStorage.setItem(storageKey(), engine);
		} catch (e) {
			/* private mode: the choice just isn't remembered */
		}
	}

	/** Resolves `promise`, or rejects after `ms` with `message`. */
	function withTimeout(promise, ms, message) {
		let timer;
		return Promise.race([
			promise,
			new Promise((_, reject) => {
				timer = setTimeout(() => reject(new Error(message)), ms);
			}),
		]).finally(() => clearTimeout(timer));
	}

	// ------------------------------------------------------------- loaders
	const once = {};
	function loadOnce(key, fn) {
		if (!once[key]) {
			once[key] = fn().catch((e) => {
				delete once[key]; // allow a retry later
				throw e;
			});
		}
		return once[key];
	}

	/** Classic script, stamped with the deployed asset version (see boot.py). */
	function loadScript(url) {
		const version = settings().asset_version || "1";
		const scripts = (window.__nexusScripts = window.__nexusScripts || {});
		if (scripts[url]) return scripts[url];
		scripts[url] = new Promise((resolve, reject) => {
			const s = document.createElement("script");
			s.src = `${url}?v=${encodeURIComponent(version)}`;
			s.async = true;
			s.onload = () => resolve();
			s.onerror = () => {
				delete scripts[url];
				reject(new Error(`Could not load ${url}`));
			};
			document.head.appendChild(s);
		});
		return scripts[url];
	}

	function loadCss(url) {
		if (document.querySelector(`link[data-nexus-css="${url}"]`)) return;
		const link = document.createElement("link");
		link.rel = "stylesheet";
		link.href = url;
		link.dataset.nexusCss = url;
		document.head.appendChild(link);
	}

	function loadMapLibre() {
		return loadOnce("maplibre", async () => {
			loadCss(`${MAPLIBRE_DIR}/maplibre-gl.css`);
			// MapLibre 6 is an ES module; its worker is another module next to it.
			const mod = await import(`${MAPLIBRE_DIR}/maplibre-gl.js`);
			const lib = mod.default && mod.default.Map ? mod.default : mod;
			lib.setWorkerUrl(new URL(`${MAPLIBRE_DIR}/maplibre-gl-worker.js`, window.location.origin).href);
			return lib;
		});
	}

	function loadGoogle() {
		return loadOnce("google", async () => {
			if (!window.NexusGMaps) await loadScript(GMAPS_LOADER_URL);
			const mapsLib = await withTimeout(NexusGMaps.importLibrary("maps"), GOOGLE_LOAD_TIMEOUT_MS, "Google Maps took too long to load.");
			const markerLib = settings().google_maps_map_id
				? await withTimeout(NexusGMaps.importLibrary("marker"), GOOGLE_LOAD_TIMEOUT_MS, "Google Maps markers took too long to load.")
				: null;
			return { maps: mapsLib, marker: markerLib, core: window.google.maps };
		});
	}

	function loadTurf() {
		return loadOnce("turf", async () => {
			if (!window.turf) await loadScript(TURF_URL);
			return window.turf;
		});
	}

	// ------------------------------------------------------------- helpers
	const toNum = (v) => (v === null || v === undefined || v === "" ? NaN : Number(v));

	function validPoint(lat, lng) {
		lat = toNum(lat);
		lng = toNum(lng);
		return isFinite(lat) && isFinite(lng) && Math.abs(lat) <= 90 && Math.abs(lng) <= 180 && !(lat === 0 && lng === 0);
	}

	function haversineM(a, b) {
		const R = 6371008.8;
		const rad = (d) => (d * Math.PI) / 180;
		const dLat = rad(b[0] - a[0]);
		const dLng = rad(b[1] - a[1]);
		const h = Math.sin(dLat / 2) ** 2 + Math.cos(rad(a[0])) * Math.cos(rad(b[0])) * Math.sin(dLng / 2) ** 2;
		return 2 * R * Math.asin(Math.min(1, Math.sqrt(h)));
	}

	/** Encoded polyline (Google / ORS, precision 5) -> [[lat, lng], ...]. */
	function decodePolyline(encoded, precision) {
		const factor = Math.pow(10, precision || 5);
		const out = [];
		let index = 0;
		let lat = 0;
		let lng = 0;
		const next = () => {
			let result = 0;
			let shift = 0;
			let byte;
			do {
				if (index >= encoded.length) return null;
				byte = encoded.charCodeAt(index++) - 63;
				result |= (byte & 0x1f) << shift;
				shift += 5;
			} while (byte >= 0x20);
			return result & 1 ? ~(result >> 1) : result >> 1;
		};
		while (index < (encoded || "").length) {
			const dLat = next();
			const dLng = next();
			if (dLat === null || dLng === null) break;
			lat += dLat;
			lng += dLng;
			out.push([lat / factor, lng / factor]);
		}
		return out;
	}

	/** Any GeoJSON (string or object) -> list of lines [[lat, lng], ...]. Points/polygons ignored. */
	function geojsonLines(raw) {
		let data = raw;
		if (!data) return [];
		if (typeof data === "string") {
			try {
				data = JSON.parse(data);
			} catch (e) {
				return [];
			}
		}
		const lines = [];
		const toLine = (coords) =>
			(Array.isArray(coords) ? coords : [])
				.filter((c) => Array.isArray(c) && validPoint(c[1], c[0]))
				.map((c) => [Number(c[1]), Number(c[0])]);
		const visit = (node, depth) => {
			if (!node || typeof node !== "object" || depth > 6) return;
			switch (node.type) {
				case "FeatureCollection":
					(node.features || []).forEach((f) => visit(f, depth + 1));
					break;
				case "Feature":
					visit(node.geometry, depth + 1);
					break;
				case "GeometryCollection":
					(node.geometries || []).forEach((g) => visit(g, depth + 1));
					break;
				case "LineString":
					lines.push(toLine(node.coordinates));
					break;
				case "MultiLineString":
					(node.coordinates || []).forEach((c) => lines.push(toLine(c)));
					break;
				default:
					break;
			}
		};
		visit(data, 0);
		return lines.filter((l) => l.length >= 2);
	}

	function formatDistance(m) {
		if (m === null || m === undefined || !isFinite(m)) return "—";
		if (m < 1000) return `${Math.max(10, Math.round(m / 10) * 10)} m`;
		if (m < 10000) return `${(m / 1000).toFixed(1)} km`;
		return `${Math.round(m / 1000)} km`;
	}

	function formatDuration(s) {
		if (s === null || s === undefined || !isFinite(s)) return "";
		const min = Math.max(1, Math.round(s / 60));
		if (min < 60) return `${min} min`;
		const h = Math.floor(min / 60);
		const rest = min % 60;
		return rest ? `${h} h ${rest} min` : `${h} h`;
	}

	/** seconds-epoch or ms-epoch -> "just now", "4 min ago", "3 h ago", "2 days ago". */
	function timeAgo(t) {
		if (!t) return "never";
		const ms = t > 1e12 ? t : t * 1000;
		const s = Math.max(0, Math.round((Date.now() - ms) / 1000));
		if (s < 45) return "just now";
		if (s < 3600) return `${Math.round(s / 60)} min ago`;
		if (s < 86400) return `${Math.round(s / 3600)} h ago`;
		return `${Math.round(s / 86400)} days ago`;
	}

	/** Hides a marker without touching the element's own display/layout (pages style pins freely). */
	function setHidden(element, hidden) {
		element.style.visibility = hidden ? "hidden" : "";
		element.style.pointerEvents = hidden ? "none" : "";
	}

	function toElement(content) {
		if (content instanceof HTMLElement) return content;
		const div = document.createElement("div");
		div.innerHTML = content == null ? "" : String(content);
		return div;
	}

	// ================================================================ MapLibre
	class HostedEngine {
		constructor(container, lib, options) {
			this.lib = lib;
			this.markers = new Map();
			this.lines = new Map(); // id -> {zIndex}
			this.popup = null;
			this.pending = [];
			this.loaded = false;
			this.map = new lib.Map({
				container,
				style: hostedTileUrl(),
				center: [options.center[1], options.center[0]],
				zoom: options.zoom,
				attributionControl: { compact: true },
				dragRotate: false,
				pitchWithRotate: false,
			});
			this.map.touchZoomRotate.disableRotation();
			this.map.addControl(new lib.NavigationControl({ showCompass: false }), "top-right");
			// Ready = the style loaded, the style failed, or the timeout passed: whichever
			// comes first. Lines asked for before the style loads are queued and drawn
			// if it loads later; markers work either way.
			this.problem = null;
			this.ready = new Promise((resolve) => {
				const timer = setTimeout(() => {
					if (!this.loaded) this.problem = this.problem || `The hosted map style didn't load within ${ENGINE_READY_TIMEOUT_MS / 1000} s (${hostedTileUrl()}).`;
					resolve();
				}, ENGINE_READY_TIMEOUT_MS);
				this.map.once("load", () => {
					this.loaded = true;
					this.problem = null;
					clearTimeout(timer);
					this.pending.splice(0).forEach((fn) => fn());
					if (this.onRecovered) this.onRecovered();
					resolve();
				});
				this.map.on("error", (e) => {
					const message = e && e.error ? e.error.message : String(e);
					console.warn("[NexusMaps] hosted map:", message);
					// A failure before the style loaded (wrong URL, server down, blocked) ends the wait.
					if (!this.loaded && !(e && e.sourceId)) {
						this.problem = `The hosted map style couldn't be loaded from ${hostedTileUrl()} (${message}).`;
						clearTimeout(timer);
						resolve();
					}
				});
			});
			this.map.on("click", (e) => {
				if (this.clickHandler && !(e.originalEvent && e.originalEvent.__nexusMarker)) this.clickHandler();
			});
		}

		_whenLoaded(fn) {
			if (this.loaded) fn();
			else this.pending.push(fn);
		}

		setMarker(id, spec) {
			let entry = this.markers.get(id);
			if (!entry) {
				const marker = new this.lib.Marker({ element: spec.element, anchor: spec.anchor === "center" ? "center" : "bottom" })
					.setLngLat([spec.lng, spec.lat])
					.addTo(this.map);
				entry = { marker, element: spec.element };
				this.markers.set(id, entry);
			} else {
				if (entry.element !== spec.element) {
					// New content: replace the marker (keeps the anchor correct).
					entry.marker.remove();
					entry.marker = new this.lib.Marker({ element: spec.element, anchor: spec.anchor === "center" ? "center" : "bottom" })
						.setLngLat([spec.lng, spec.lat])
						.addTo(this.map);
					entry.element = spec.element;
				} else {
					entry.marker.setLngLat([spec.lng, spec.lat]);
				}
			}
			entry.element.style.zIndex = String(spec.zIndex || 0);
			setHidden(entry.element, spec.hidden);
		}

		moveMarker(id, lat, lng) {
			const entry = this.markers.get(id);
			if (entry) entry.marker.setLngLat([lng, lat]);
		}

		removeMarker(id) {
			const entry = this.markers.get(id);
			if (entry) {
				entry.marker.remove();
				this.markers.delete(id);
			}
		}

		_beforeId(zIndex) {
			// Keep line layers ordered by zIndex: insert before the first higher one.
			let before;
			let best = Infinity;
			this.lines.forEach((meta, lineId) => {
				if (meta.zIndex > zIndex && meta.zIndex < best && this.map.getLayer(`nx-line-${lineId}`)) {
					best = meta.zIndex;
					before = `nx-line-${lineId}`;
				}
			});
			return before;
		}

		setLine(id, points, style) {
			this._whenLoaded(() => {
				const sourceId = `nx-line-${id}`;
				const data = {
					type: "Feature",
					properties: {},
					geometry: { type: "LineString", coordinates: points.map((p) => [p[1], p[0]]) },
				};
				const paint = {
					"line-color": style.color || "#2563eb",
					"line-width": style.width || 4,
					"line-opacity": style.opacity === undefined ? 0.9 : style.opacity,
				};
				const zIndex = style.zIndex || 0;
				const existing = this.map.getSource(sourceId);
				if (existing) {
					existing.setData(data);
					Object.keys(paint).forEach((k) => this.map.setPaintProperty(sourceId, k, paint[k]));
					this.map.setPaintProperty(sourceId, "line-dasharray", style.dash ? [style.dash[0] / (style.width || 4), style.dash[1] / (style.width || 4)] : null);
					this.lines.set(id, { zIndex });
					return;
				}
				this.map.addSource(sourceId, { type: "geojson", data });
				if (style.dash) paint["line-dasharray"] = [style.dash[0] / (style.width || 4), style.dash[1] / (style.width || 4)];
				this.lines.set(id, { zIndex });
				this.map.addLayer(
					{ id: sourceId, type: "line", source: sourceId, layout: { "line-join": "round", "line-cap": "round" }, paint },
					this._beforeId(zIndex),
				);
			});
		}

		removeLine(id) {
			this._whenLoaded(() => {
				const sourceId = `nx-line-${id}`;
				if (this.map.getLayer(sourceId)) this.map.removeLayer(sourceId);
				if (this.map.getSource(sourceId)) this.map.removeSource(sourceId);
				this.lines.delete(id);
			});
		}

		fitPoints(points, options) {
			if (!points.length) return;
			const padding = options.padding === undefined ? 60 : options.padding;
			if (points.length === 1) {
				this.map.easeTo({ center: [points[0][1], points[0][0]], zoom: options.singleZoom || 15, duration: 600 });
				return;
			}
			const bounds = new this.lib.LngLatBounds();
			points.forEach((p) => bounds.extend([p[1], p[0]]));
			this.map.fitBounds(bounds, { padding, maxZoom: options.maxZoom || 16, duration: options.animate === false ? 0 : 700 });
		}

		flyTo(lat, lng, zoom) {
			this.map.flyTo({ center: [lng, lat], zoom: zoom || this.map.getZoom(), speed: 1.4 });
		}

		showPopup(content, lat, lng, options) {
			this.closePopup();
			this.popup = new this.lib.Popup({ closeButton: true, maxWidth: "300px", offset: options.offset || 14 })
				.setLngLat([lng, lat])
				.setDOMContent(toElement(content))
				.addTo(this.map);
		}

		closePopup() {
			if (this.popup) {
				this.popup.remove();
				this.popup = null;
			}
		}

		onMapClick(fn) {
			this.clickHandler = fn;
		}

		resize() {
			this.map.resize();
		}

		destroy() {
			this.closePopup();
			this.markers.forEach((e) => e.marker.remove());
			this.markers.clear();
			this.map.remove();
		}
	}

	// ================================================================== Google
	function makeOverlayMarkerClass(core) {
		// HTML marker without a Map ID: an OverlayView that positions a DOM node.
		return class OverlayMarker extends core.OverlayView {
			constructor(element, position, anchor) {
				super();
				this.element = element;
				this.position = position;
				this.anchor = anchor;
				this.element.style.position = "absolute";
			}
			onAdd() {
				// Clicks and drags on the pin must not reach the map (Google's documented way).
				core.OverlayView.preventMapHitsAndGesturesFrom(this.element);
				this.getPanes().overlayMouseTarget.appendChild(this.element);
			}
			draw() {
				const projection = this.getProjection();
				if (!projection) return;
				const p = projection.fromLatLngToDivPixel(this.position);
				if (!p) return;
				this.element.style.left = `${p.x}px`;
				this.element.style.top = `${p.y}px`;
				this.element.style.transform = this.anchor === "center" ? "translate(-50%, -50%)" : "translate(-50%, -100%)";
			}
			onRemove() {
				if (this.element.parentNode) this.element.parentNode.removeChild(this.element);
			}
			setPosition(position) {
				this.position = position;
				this.draw();
			}
		};
	}

	class GoogleEngine {
		constructor(container, libs, options) {
			this.libs = libs;
			this.core = libs.core;
			this.markers = new Map();
			this.lines = new Map();
			this.mapId = String(settings().google_maps_map_id || "").trim() || null;
			this.OverlayMarker = this.mapId ? null : makeOverlayMarkerClass(this.core);
			this.map = new libs.maps.Map(container, {
				center: { lat: options.center[0], lng: options.center[1] },
				zoom: options.zoom,
				mapId: this.mapId || undefined,
				mapTypeControl: false,
				streetViewControl: false,
				fullscreenControl: false,
				clickableIcons: false,
				gestureHandling: "greedy",
				rotateControl: false,
				tilt: 0,
			});
			this.info = new libs.maps.InfoWindow({ maxWidth: 300 });
			this.problem = null;
			this.ready = new Promise((resolve) => {
				const timer = setTimeout(() => {
					this.problem = `Google Maps didn't draw within ${ENGINE_READY_TIMEOUT_MS / 1000} s.`;
					resolve();
				}, ENGINE_READY_TIMEOUT_MS);
				this.core.event.addListenerOnce(this.map, "idle", () => {
					clearTimeout(timer);
					resolve();
				});
			});
			this.map.addListener("click", () => {
				if (this.clickHandler) this.clickHandler();
			});
		}

		_wrap(element, anchor) {
			// Advanced Markers anchor at bottom-centre; centre-anchored pins are nudged down.
			if (!this.mapId || anchor !== "center") return element;
			const wrap = document.createElement("div");
			wrap.style.transform = "translateY(50%)";
			wrap.appendChild(element);
			return wrap;
		}

		setMarker(id, spec) {
			const position = { lat: spec.lat, lng: spec.lng };
			let entry = this.markers.get(id);
			if (entry && entry.element !== spec.element) {
				this.removeMarker(id);
				entry = null;
			}
			if (!entry) {
				let marker;
				if (this.mapId) {
					marker = new this.libs.marker.AdvancedMarkerElement({
						map: this.map,
						position,
						content: this._wrap(spec.element, spec.anchor),
						zIndex: spec.zIndex || 0,
						gmpClickable: true,
					});
				} else {
					marker = new this.OverlayMarker(spec.element, new this.core.LatLng(spec.lat, spec.lng), spec.anchor);
					marker.setMap(this.map);
				}
				entry = { marker, element: spec.element };
				this.markers.set(id, entry);
			} else if (this.mapId) {
				entry.marker.position = position;
			} else {
				entry.marker.setPosition(new this.core.LatLng(spec.lat, spec.lng));
			}
			if (this.mapId) entry.marker.zIndex = spec.zIndex || 0;
			entry.element.style.zIndex = String(spec.zIndex || 0);
			setHidden(entry.element, spec.hidden);
		}

		moveMarker(id, lat, lng) {
			const entry = this.markers.get(id);
			if (!entry) return;
			if (this.mapId) entry.marker.position = { lat, lng };
			else entry.marker.setPosition(new this.core.LatLng(lat, lng));
		}

		removeMarker(id) {
			const entry = this.markers.get(id);
			if (!entry) return;
			if (this.mapId) entry.marker.map = null;
			else entry.marker.setMap(null);
			this.markers.delete(id);
		}

		setLine(id, points, style) {
			const path = points.map((p) => ({ lat: p[0], lng: p[1] }));
			const width = style.width || 4;
			const opacity = style.opacity === undefined ? 0.9 : style.opacity;
			const options = {
				path,
				strokeColor: style.color || "#2563eb",
				strokeWeight: width,
				strokeOpacity: style.dash ? 0 : opacity,
				zIndex: style.zIndex || 0,
				clickable: false,
				icons: style.dash
					? [{
						icon: { path: "M 0,-1 0,1", strokeOpacity: opacity, strokeColor: style.color || "#2563eb", scale: width / 2 },
						offset: "0",
						repeat: `${style.dash[0] + style.dash[1]}px`,
					}]
					: null,
			};
			const existing = this.lines.get(id);
			if (existing) existing.setOptions(options);
			else this.lines.set(id, new this.libs.maps.Polyline({ ...options, map: this.map }));
		}

		removeLine(id) {
			const line = this.lines.get(id);
			if (line) {
				line.setMap(null);
				this.lines.delete(id);
			}
		}

		fitPoints(points, options) {
			if (!points.length) return;
			if (points.length === 1) {
				this.map.panTo({ lat: points[0][0], lng: points[0][1] });
				this.map.setZoom(options.singleZoom || 15);
				return;
			}
			const bounds = new this.core.LatLngBounds();
			points.forEach((p) => bounds.extend({ lat: p[0], lng: p[1] }));
			const padding = options.padding === undefined ? 60 : options.padding;
			this.map.fitBounds(bounds, padding);
			const maxZoom = options.maxZoom || 16;
			this.core.event.addListenerOnce(this.map, "idle", () => {
				if (this.map.getZoom() > maxZoom) this.map.setZoom(maxZoom);
			});
		}

		flyTo(lat, lng, zoom) {
			this.map.panTo({ lat, lng });
			if (zoom) this.map.setZoom(zoom);
		}

		showPopup(content, lat, lng, options) {
			this.info.setContent(toElement(content));
			this.info.setPosition({ lat, lng });
			this.info.setOptions({ pixelOffset: new this.core.Size(0, -(options.offset || 14)) });
			this.info.open({ map: this.map });
		}

		closePopup() {
			this.info.close();
		}

		onMapClick(fn) {
			this.clickHandler = fn;
		}

		resize() {
			/* Google watches its container size itself. */
		}

		destroy() {
			this.closePopup();
			[...this.markers.keys()].forEach((id) => this.removeMarker(id));
			[...this.lines.keys()].forEach((id) => this.removeLine(id));
			this.core.event.clearInstanceListeners(this.map);
		}
	}

	// ==================================================================== View
	class NexusMapView {
		constructor(element, options) {
			this.element = element;
			this.options = options || {};
			this.markers = new Map(); // id -> spec (lat/lng = target position)
			this.lines = new Map(); // id -> {points, style}
			this.animations = new Map(); // id -> {from, to, start, duration, current}
			this.popup = null;
			this.engine = null;
			this.engineName = null;
			this.listeners = { engine: new Set(), click: new Set() };
			this.frame = null;
			this._tick = this._tick.bind(this);
			this.canvas = document.createElement("div");
			this.canvas.className = "nexus-map-canvas";
			this.canvas.style.cssText = "position:absolute;inset:0;";
			// The canvas fills the element; only give it a positioning context if it has none
			// (never override a page's own absolute/fixed layout).
			if (window.getComputedStyle(this.element).position === "static") this.element.style.position = "relative";
			this.element.appendChild(this.canvas);
			this.resizeObserver = window.ResizeObserver ? new ResizeObserver(() => this.engine && this.engine.resize()) : null;
			if (this.resizeObserver) this.resizeObserver.observe(this.element);
			this.offAuth = window.NexusGMaps
				? NexusGMaps.onAuthFailure(() => this._googleRejected())
				: null;
		}

		async _start(engineName, camera) {
			const center = camera ? camera.center : this.options.center || DEFAULT_CENTER;
			const zoom = camera ? camera.zoom : this.options.zoom || 11;
			this.canvas.innerHTML = "";
			const target = document.createElement("div");
			target.style.cssText = "position:absolute;inset:0;";
			this.canvas.appendChild(target);

			let engine;
			if (engineName === "google") {
				const libs = await loadGoogle();
				if (!this.offAuth && window.NexusGMaps) this.offAuth = NexusGMaps.onAuthFailure(() => this._googleRejected());
				engine = new GoogleEngine(target, libs, { center, zoom });
			} else {
				const lib = await loadMapLibre();
				engine = new HostedEngine(target, lib, { center, zoom });
			}
			this.engine = engine;
			this.engineName = engineName;
			engine.onRecovered = () => this._showProblem(null);
			engine.onMapClick(() => {
				// Some engines also report a marker click as a map click: ignore those.
				if (performance.now() - (this._markerClickAt || 0) < 350) return;
				this.listeners.click.forEach((fn) => fn());
			});
			await engine.ready;
			this._showProblem(engine.problem);
			// Replay everything the page asked for.
			this.lines.forEach((line, id) => engine.setLine(id, line.points, line.style));
			this.markers.forEach((spec, id) => engine.setMarker(id, this._currentSpec(id, spec)));
			if (this.popup) engine.showPopup(this.popup.content, this.popup.lat, this.popup.lng, this.popup.options);
			this.listeners.engine.forEach((fn) => fn(engineName));
		}

		/** A small notice on the map when the engine couldn't draw (pins and panels keep working). */
		_showProblem(problem) {
			if (!this.problemBox) {
				this.problemBox = document.createElement("div");
				this.problemBox.className = "nexus-map-problem";
				this.element.appendChild(this.problemBox);
			}
			this.problem = problem || null;
			this.problemBox.style.display = problem ? "" : "none";
			if (problem) {
				const hint =
					this.engineName === "hosted"
						? __("Check Nexus App Settings → Hosted Tile URL, or switch to Google.")
						: __("Check the Google web key, or switch to Hosted.");
				this.problemBox.textContent = `⚠ ${problem} ${hint}`;
			}
			this.listeners.engine.forEach((fn) => fn(this.engineName));
		}

		_camera() {
			const eng = this.engine;
			if (!eng) return null;
			try {
				if (this.engineName === "google") {
					const c = eng.map.getCenter();
					return { center: [c.lat(), c.lng()], zoom: eng.map.getZoom() };
				}
				const c = eng.map.getCenter();
				return { center: [c.lat, c.lng], zoom: eng.map.getZoom() };
			} catch (e) {
				return null;
			}
		}

		/** Switches engine, keeping the camera and everything drawn. */
		async switchEngine(engineName) {
			if (engineName === this.engineName) return;
			const camera = this._camera();
			const old = this.engine;
			this.engine = null;
			if (old) {
				try {
					old.destroy();
				} catch (e) {
					console.warn("[NexusMaps] destroy failed", e);
				}
			}
			try {
				await this._start(engineName, camera);
				rememberEngine(engineName);
			} catch (e) {
				console.error("[NexusMaps] engine failed, falling back to hosted", e);
				if (engineName !== "hosted") {
					frappe.show_alert({ message: __("Google Maps couldn't load; showing the hosted map."), indicator: "orange" });
					await this._start("hosted", camera);
				} else {
					throw e;
				}
			}
		}

		_googleRejected() {
			if (this.engineName !== "google") return;
			frappe.show_alert({
				message: __("Google rejected the Maps key (check its HTTP referrer restriction and billing). Switched to the hosted map."),
				indicator: "red",
			}, 12);
			this.switchEngine("hosted");
		}

		onEngineChange(fn) {
			this.listeners.engine.add(fn);
			return () => this.listeners.engine.delete(fn);
		}

		onMapClick(fn) {
			this.listeners.click.add(fn);
			return () => this.listeners.click.delete(fn);
		}

		// ── markers ──
		setMarker(id, spec) {
			if (!validPoint(spec.lat, spec.lng)) return;
			const clean = { ...spec, lat: Number(spec.lat), lng: Number(spec.lng) };
			if (clean.onClick && !clean.element.__nexusClick) {
				clean.element.__nexusClick = true;
				clean.element.style.cursor = "pointer";
				clean.element.addEventListener("click", (ev) => {
					ev.stopPropagation();
					ev.__nexusMarker = true;
					this._markerClickAt = performance.now();
					const current = this.markers.get(id);
					if (current && current.onClick) current.onClick(ev);
				});
			}
			this.animations.delete(id);
			this.markers.set(id, clean);
			if (this.engine) this.engine.setMarker(id, clean);
		}

		_currentSpec(id, spec) {
			const anim = this.animations.get(id);
			return anim && anim.current ? { ...spec, lat: anim.current[0], lng: anim.current[1] } : spec;
		}

		/**
		 * Moves a marker smoothly to a new position (default 1 s). Large jumps
		 * (> 3 km), hidden tabs and reduced-motion users move instantly.
		 */
		animateMarker(id, lat, lng, durationMs) {
			const spec = this.markers.get(id);
			if (!spec || !validPoint(lat, lng)) return;
			lat = Number(lat);
			lng = Number(lng);
			const anim = this.animations.get(id);
			const from = anim && anim.current ? anim.current : [spec.lat, spec.lng];
			spec.lat = lat;
			spec.lng = lng;
			const reduced = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
			if (document.hidden || reduced || haversineM(from, [lat, lng]) > 3000 || !this.engine) {
				this.animations.delete(id);
				if (this.engine) this.engine.moveMarker(id, lat, lng);
				return;
			}
			this.animations.set(id, { from, to: [lat, lng], start: performance.now(), duration: durationMs || 1000, current: from });
			if (!this.frame) this.frame = requestAnimationFrame(this._tick);
		}

		_tick(now) {
			this.frame = null;
			this.animations.forEach((a, id) => {
				const t = Math.min(1, (now - a.start) / a.duration);
				const e = t < 0.5 ? 2 * t * t : 1 - Math.pow(-2 * t + 2, 2) / 2; // ease-in-out
				a.current = [a.from[0] + (a.to[0] - a.from[0]) * e, a.from[1] + (a.to[1] - a.from[1]) * e];
				if (this.engine) this.engine.moveMarker(id, a.current[0], a.current[1]);
				if (t >= 1) this.animations.delete(id);
			});
			if (this.animations.size) this.frame = requestAnimationFrame(this._tick);
		}

		/** Changes some fields of a marker; a glide in progress continues unless the position changes. */
		updateMarker(id, changes) {
			const spec = this.markers.get(id);
			if (!spec) return;
			const next = { ...spec, ...changes };
			if ("lat" in changes || "lng" in changes || "element" in changes) {
				this.setMarker(id, next);
				return;
			}
			this.markers.set(id, next);
			if (this.engine) this.engine.setMarker(id, this._currentSpec(id, next));
		}

		hasMarker(id) {
			return this.markers.has(id);
		}

		markerPosition(id) {
			const spec = this.markers.get(id);
			return spec ? [spec.lat, spec.lng] : null;
		}

		markerIds() {
			return [...this.markers.keys()];
		}

		removeMarker(id) {
			this.animations.delete(id);
			this.markers.delete(id);
			if (this.engine) this.engine.removeMarker(id);
		}

		// ── lines ──
		setLine(id, points, style) {
			const clean = (points || []).filter((p) => validPoint(p[0], p[1])).map((p) => [Number(p[0]), Number(p[1])]);
			if (clean.length < 2) {
				this.removeLine(id);
				return;
			}
			const entry = { points: clean, style: style || {} };
			this.lines.set(id, entry);
			if (this.engine) this.engine.setLine(id, entry.points, entry.style);
		}

		removeLine(id) {
			if (!this.lines.has(id)) return;
			this.lines.delete(id);
			if (this.engine) this.engine.removeLine(id);
		}

		lineIds() {
			return [...this.lines.keys()];
		}

		// ── camera / popups ──
		fitPoints(points, options) {
			const clean = (points || []).filter((p) => p && validPoint(p[0], p[1])).map((p) => [Number(p[0]), Number(p[1])]);
			if (this.engine && clean.length) this.engine.fitPoints(clean, options || {});
		}

		flyTo(lat, lng, zoom) {
			if (this.engine && validPoint(lat, lng)) this.engine.flyTo(Number(lat), Number(lng), zoom);
		}

		showPopup(content, lat, lng, options) {
			if (!validPoint(lat, lng)) return;
			this.popup = { content, lat: Number(lat), lng: Number(lng), options: options || {} };
			if (this.engine) this.engine.showPopup(content, Number(lat), Number(lng), this.popup.options);
		}

		closePopup() {
			this.popup = null;
			if (this.engine) this.engine.closePopup();
		}

		resize() {
			if (this.engine) this.engine.resize();
		}

		destroy() {
			if (this.frame) cancelAnimationFrame(this.frame);
			if (this.resizeObserver) this.resizeObserver.disconnect();
			if (this.offAuth) this.offAuth();
			if (this.engine) this.engine.destroy();
			this.engine = null;
			this.markers.clear();
			this.lines.clear();
			this.element.removeChild(this.canvas);
		}
	}

	/**
	 * Builds the Google / Hosted toggle as a small overlay control (top-right,
	 * beside the zoom buttons) inside the map element. Hidden when only one engine is available.
	 */
	function attachEngineToggle(view) {
		const engines = availableEngines();
		if (engines.length < 2) return null;
		const box = document.createElement("div");
		box.className = "nexus-engine-toggle";
		box.innerHTML = engines
			.map((e) => `<button type="button" data-engine="${e}">${e === "google" ? __("Google") : __("Hosted")}</button>`)
			.join("");
		const paint = (active) =>
			box.querySelectorAll("button").forEach((b) => b.classList.toggle("active", b.dataset.engine === active));
		box.addEventListener("click", async (ev) => {
			const btn = ev.target.closest("button[data-engine]");
			if (!btn || btn.dataset.engine === view.engineName || box.dataset.busy) return;
			box.dataset.busy = "1";
			paint(btn.dataset.engine);
			try {
				await view.switchEngine(btn.dataset.engine);
			} finally {
				delete box.dataset.busy;
				paint(view.engineName);
			}
		});
		view.onEngineChange(paint);
		paint(view.engineName);
		view.element.appendChild(box);
		return box;
	}

	function injectStyles() {
		if (document.getElementById("nexus-map-adapter-css")) return;
		const style = document.createElement("style");
		style.id = "nexus-map-adapter-css";
		style.textContent = `
			.nexus-engine-toggle{position:absolute;right:52px;top:10px;z-index:5;display:flex;background:#fff;border-radius:8px;
				box-shadow:0 2px 8px rgba(15,23,42,.25);overflow:hidden;font-size:12px}
			.nexus-engine-toggle button{border:0;background:#fff;padding:6px 12px;font-weight:700;color:#475569;cursor:pointer}
			.nexus-engine-toggle button.active{background:#0f172a;color:#fff}
			.nexus-map-problem{position:absolute;left:50%;top:12px;transform:translateX(-50%);z-index:6;max-width:min(520px,80%);background:#fffbeb;
				color:#92400e;border:1px solid #f59e0b;border-radius:8px;padding:8px 12px;font-size:12px;font-weight:600;box-shadow:0 4px 12px rgba(15,23,42,.15)}
			.nexus-map-canvas .maplibregl-popup-content{border-radius:10px;padding:12px 14px;box-shadow:0 10px 24px rgba(15,23,42,.25)}
		`;
		document.head.appendChild(style);
	}

	/**
	 * Creates a map view in `element` with the preferred engine.
	 * options: { center: [lat, lng], zoom, toggle: true }
	 */
	async function createView(element, options) {
		injectStyles();
		if (googleConfigured() && !window.NexusGMaps) {
			try {
				await loadScript(GMAPS_LOADER_URL);
			} catch (e) {
				/* the hosted engine still works */
			}
		}
		const view = new NexusMapView(element, options || {});
		const first = preferredEngine();
		try {
			await view._start(first, null);
		} catch (e) {
			console.error(`[NexusMaps] ${first} engine failed`, e);
			if (first === "hosted") throw e;
			frappe.show_alert({ message: __("Google Maps couldn't load; showing the hosted map."), indicator: "orange" });
			await view._start("hosted", null);
		}
		if (!options || options.toggle !== false) attachEngineToggle(view);
		return view;
	}

	window.NexusMaps = {
		version: "1.1.0",
		createView,
		availableEngines,
		preferredEngine,
		loadTurf,
		decodePolyline,
		geojsonLines,
		haversineM,
		validPoint,
		formatDistance,
		formatDuration,
		timeAgo,
	};
})();
