// apps/nexus_supply_chain/nexus_supply_chain/page/nexus_driver_tracker/nexus_driver_tracker.js

let wakeLock = null;
let ws = null; 
let reconnect_interval = 3000; 

// 🚨 CLOUDFLARE SECURE WEBSOCKET TUNNEL (FastAPI Telemetry) 🚨
const FASTAPI_WS_URL = "wss://whereas-reproductive-tribune-accepts.trycloudflare.com/telemetry/ws";

// 🚨 SOVEREIGN SELF-HOSTED MAP TUNNEL (Docker TileServer-GL) 🚨
const TILE_SERVER_URL = "https://boc-weblog-johnny-shelter.trycloudflare.com/styles/basic-preview/style.json";

frappe.pages['nexus_driver_tracker'].on_page_load = function(wrapper) {
    let page = frappe.ui.make_app_page({ 
        parent: wrapper, 
        title: 'Nexus Driver Tracker', 
        single_column: true 
    });
    
    $(wrapper).find('.layout-main-section').html(`
        <div class="container-fluid p-0" style="max-width: 1000px; margin: 0 auto; height: 85vh;">
            <div class="d-flex flex-column h-100 bg-white border rounded shadow-sm">
                
                <div class="p-3 text-center border-bottom bg-light">
                    <div class="input-group input-group-sm mb-2" style="max-width: 400px; margin: 0 auto;">
                        <input type="text" class="form-control" id="target-search" placeholder="Enter Manifest ID or Driver Email...">
                        <button class="btn btn-primary" id="btn-start-track">Track Target</button>
                    </div>
                    <h4 id="tracker-status" class="mb-1 text-dark fw-bold">Awaiting Target...</h4>
                    <div class="d-flex align-items-center justify-content-center mt-2">
                        <div id="live-pulse" style="width:12px; height:12px; background:#cbd5e1; border-radius:50%; margin-right: 8px;"></div>
                        <span id="ping-text" class="text-muted small">Standby</span>
                    </div>
                </div>
                
                <div class="flex-grow-1 position-relative">
                    <div id="driver-map" style="height: 100%; width: 100%; z-index: 1;"></div>
                    
                    <div class="position-absolute top-0 end-0 m-3 d-flex flex-column gap-2" style="z-index: 999; width: 220px;">
                        <button class="btn btn-primary shadow fw-bold driver-action-btn w-100" id="btn-full-route" disabled>
                            <i class="fa fa-map-marked-alt me-2"></i> View Full Live Route
                        </button>
                        <button class="btn btn-warning shadow fw-bold driver-action-btn w-100" id="btn-go-customer" disabled>
                            <i class="fa fa-location-arrow me-2 text-dark"></i> Go to Customer
                        </button>
                    </div>

                    <div class="position-absolute bottom-0 start-0 m-3 p-2 bg-white shadow-sm rounded border" style="z-index: 999; font-size: 11px;">
                        <div class="d-flex align-items-center mb-1">
                            <span class="me-2" style="width:12px;height:12px;border-radius:50%;background:#9ca3af;border:2px solid #fff;box-shadow:0 0 4px rgba(156,163,175,0.4);"></span> 
                            Free Roaming (Grey)
                        </div>
                        <div class="d-flex align-items-center mb-1">
                            <span class="me-2" style="width:12px;height:2px;background:#9ca3af;border-top:2px dashed #9ca3af;"></span> 
                            Planned Route (Grey)
                        </div>
                        <div class="d-flex align-items-center">
                            <span class="me-2" style="width:12px;height:2px;background:#2563eb;"></span> 
                            Active Remaining (Blue)
                        </div>
                    </div>
                </div>
            </div>
        </div>
    `);

    $("<style>@keyframes smallPulse { 0% { box-shadow: 0 0 0 0 rgba(34,197,94,0.7); } 70% { box-shadow: 0 0 0 8px rgba(34,197,94,0); } 100% { box-shadow: 0 0 0 0 rgba(34,197,94,0); } } .driver-action-btn { font-size: 13px; padding: 12px 15px; border-radius: 8px; text-align: left; }</style>").appendTo("head");

    // Clear ghost 404 cache immediately
    let activeManifestKey = Object.keys(localStorage).find(k => k.startsWith('tracking_'));
    let initialTarget = null;
    
    if (activeManifestKey) {
        initialTarget = activeManifestKey.replace('tracking_', '');
        localStorage.removeItem(activeManifestKey); // 🚨 Fixes the infinite 404 loop
    }

    if (initialTarget) {
        $('#target-search').val(initialTarget);
        init_tracker_engine(initialTarget);
    }

    $('#btn-start-track').on('click', function() {
        let t = $('#target-search').val().trim();
        if(t) init_tracker_engine(t);
    });

    function init_tracker_engine(target_id) {
        $('#tracker-status').text(`Querying Database...`);
        $('#live-pulse').css({ 'background': '#eab308', 'animation': 'smallPulse 1.5s infinite' });
        $('#ping-text').text("Validating ID...").removeClass("text-danger").addClass("text-warning");
        $('#btn-full-route, #btn-go-customer').prop('disabled', true);
        
        let map = null;
        let driver_marker = null;
        let last_known_pos = null; 
        
        let current_active_manifest = null; // State tracker for hot-swaps
        let current_manifest_doc = null;
        let target_driver_email = null; // 🚨 Assign driver directly from DB

        let activeRouteLayer = null; 
        let unvisitedWaypoints = []; 

        // Reset Map if exists
        if (window.nexus_tracker_map) {
            window.nexus_tracker_map.remove();
        }

        // 🚨 LOAD LEAFLET + MAPLIBRE-GL-LEAFLET BRIDGE + TURF.JS 🚨
        frappe.require([
            "/assets/nexus_supply_chain/leaflet/leaflet.css", 
            "/assets/nexus_supply_chain/leaflet/leaflet.js",
            "https://cdnjs.cloudflare.com/ajax/libs/Turf.js/6.5.0/turf.min.js",
            "https://unpkg.com/maplibre-gl@3.6.2/dist/maplibre-gl.css",
            "https://unpkg.com/maplibre-gl@3.6.2/dist/maplibre-gl.js",
            "https://unpkg.com/@maplibre/maplibre-gl-leaflet@0.0.20/leaflet-maplibre-gl.js"
        ], function() {
            
            map = L.map('driver-map', { zoomControl: false }).setView([-1.2921, 36.8219], 13);
            window.nexus_tracker_map = map;
            L.control.zoom({ position: 'topright' }).addTo(map);
            
            // 🚨 NATIVE VECTOR TILES INJECTION (Sovereign Docker Server) 🚨
            L.maplibreGL({
                style: TILE_SERVER_URL,
                attribution: '&copy; Sovereign Nexus Maps'
            }).addTo(map);

            activeRouteLayer = L.geoJSON(null, { style: { color: '#2563eb', weight: 6, opacity: 0.9 } }).addTo(map);

            // =========================================================================
            // 🚨 1. DB CALL: FIND THE DRIVER BEFORE TRACKING BEGINS & GRACEFUL FAILURE 🚨
            // =========================================================================
            frappe.call({
                method: 'frappe.client.get',
                args: { doctype: 'Vehicle Delivery Manifest', name: target_id },
                callback: function(r) {
                    if (r.message) {
                        // Success! It's a valid Manifest ID. Lock in the driver and the route.
                        current_manifest_doc = r.message;
                        target_driver_email = r.message.driver.toLowerCase(); 
                        $('#tracker-status').text(`Tracking: ${r.message.name}`);
                        $('#btn-full-route, #btn-go-customer').prop('disabled', false);

                        if (r.message.route_geojson) {
                            try {
                                let parsedGeoJSON = JSON.parse(r.message.route_geojson);
                                
                                L.geoJSON(parsedGeoJSON, {
                                    style: { color: '#9ca3af', weight: 4, opacity: 0.5, dashArray: '5, 5' }
                                }).addTo(map);
                                
                                if (parsedGeoJSON.features && parsedGeoJSON.features.length > 0) {
                                    let lineFeature = parsedGeoJSON.features.find(f => f.geometry.type === 'LineString');
                                    if (lineFeature) { unvisitedWaypoints = [...lineFeature.geometry.coordinates]; }
                                }
                            } catch(e) { console.log("GeoJSON parse failed", e); }
                        }
                        connect_websocket();
                    } else if (target_id.includes('@')) {
                        // Fallback: It's not a manifest, but it is an email. Try raw tracking.
                        target_driver_email = target_id.toLowerCase();
                        $('#tracker-status').text(`Free Roaming: ${target_driver_email}`);
                        connect_websocket();
                    } else {
                        // 🚨 ERROR HANDLING: It's a dead ID. Fail gracefully.
                        $('#tracker-status').text(`Target Not Found`);
                        $('#ping-text').text("ID does not exist in Database.").removeClass("text-warning").addClass("text-danger");
                        $('#live-pulse').css({ 'background': '#ef4444', 'animation': 'none' });
                    }
                }
            });

            // =========================================================================
            // 🚨 2. TELEMETRY ENGINE & RUBBER-BAND PHYSICS
            // =========================================================================
            function connect_websocket() {
                if (ws && ws.readyState === WebSocket.OPEN) ws.close();

                ws = new WebSocket(FASTAPI_WS_URL);

                ws.onopen = function() {
                    $('#ping-text').text("Awaiting Native Android Pings...").removeClass("text-danger text-warning").addClass("text-warning fw-bold");
                };

                ws.onmessage = function(event) {
                    let data = JSON.parse(event.data);
                    const fleet = data.fleet || {};
                    
                    let vehicleData = null;
                    let active_driver_id = null;

                    // 🚨 STRICT MATCHER: Ensures we track the exact email regardless of the phone's state
                    let d_key = Object.keys(fleet).find(k => k.toLowerCase() === target_driver_email);
                    
                    if (d_key) {
                        vehicleData = fleet[d_key];
                        active_driver_id = d_key;
                    }

                    if (!vehicleData) return;

                    $('#ping-text').text("Native GPS Active").removeClass("text-warning text-danger").addClass("text-success fw-bold");
                    $('#live-pulse').css({ 'background': '#22c55e', 'animation': 'smallPulse 1.5s infinite' });

                    let currentLat = vehicleData.lat;
                    let currentLng = vehicleData.lng;
                    let heading = vehicleData.heading || 0; 
                    let ping_manifest = vehicleData.manifest_id;

                    // 🚨 HOLY TRINITY EXTRACTION: Parse Vehicle ID safely
                    let display_vehicle = (vehicleData.vehicle && vehicleData.vehicle !== "Unknown_Vehicle") 
                                            ? vehicleData.vehicle 
                                            : (current_manifest_doc ? current_manifest_doc.vehicle : 'Unassigned Truck');
                    
                    last_known_pos = { lat: currentLat, lng: currentLng }; 
                    let is_idle = !ping_manifest || ping_manifest === "No_Active_Manifest";

                    // 🚨 STATE MACHINE HOT-SWAP DETECTION 🚨
                    if (!is_idle && current_active_manifest !== ping_manifest) {
                        current_active_manifest = ping_manifest;
                        $('#tracker-status').text(`Active Trip: ${ping_manifest}`);
                    } else if (is_idle && current_active_manifest !== null) {
                        current_active_manifest = null;
                        $('#tracker-status').text(`Free Roaming: ${active_driver_id}`);
                    } else if (is_idle && current_active_manifest === null) {
                        $('#tracker-status').text(`Free Roaming: ${active_driver_id}`);
                    }

                    // Render Dynamic Truck Marker (Blue = Active, Grey = Idle)
                    let markerColor = is_idle ? '#9ca3af' : '#2563eb';
                    
                    if (driver_marker) {
                        driver_marker.setLatLng([currentLat, currentLng]);
                        let iconElement = driver_marker.getElement();
                        if (iconElement) {
                            let arrow = iconElement.querySelector('.truck-arrow');
                            let svgPath = iconElement.querySelector('path');
                            if (arrow) arrow.style.transform = `rotate(${heading}deg)`;
                            if (svgPath) svgPath.setAttribute('fill', markerColor);
                        }
                        // Update popup dynamically with the verified Vehicle ID
                        driver_marker.setPopupContent(`<div class="p-1"><b>${display_vehicle}</b><br><span class="text-muted small">${active_driver_id}</span></div>`);
                        map.panTo([currentLat, currentLng], {animate: true});
                    } else {
                        let htmlIcon = `
                            <div style="background:#fff; border-radius:50%; width:24px; height:24px; box-shadow: 0 0 10px rgba(0,0,0,0.3); display:flex; align-items:center; justify-content:center;">
                                <div class="truck-arrow" style="transform: rotate(${heading}deg); transition: transform 0.5s ease-in-out;">
                                    <svg width="14" height="14" viewBox="0 0 24 24" fill="${markerColor}" xmlns="http://www.w3.org/2000/svg">
                                        <path d="M12 2L22 20L12 17L2 20L12 2Z" />
                                    </svg>
                                </div>
                            </div>`;
                        let icon = L.divIcon({ className: '', html: htmlIcon, iconSize: [24, 24] });
                        driver_marker = L.marker([currentLat, currentLng], {icon: icon}).addTo(map);
                        driver_marker.bindPopup(`<div class="p-1"><b>${display_vehicle}</b><br><span class="text-muted small">${active_driver_id}</span></div>`);
                        map.setView([currentLat, currentLng], 15);
                    }

                    // 🚨 THE TRUE 3-POINT RUBBER-BAND PHYSICS 🚨
                    if (!is_idle && ping_manifest === target_id && unvisitedWaypoints.length > 0 && typeof turf !== 'undefined') {
                        try {
                            let truckPoint = turf.point([currentLng, currentLat]);

                            // 1. Pop waypoints that are passed
                            while (unvisitedWaypoints.length > 0) {
                                let nextPoint = turf.point(unvisitedWaypoints[0]);
                                let dist = turf.distance(truckPoint, nextPoint, {units: 'kilometers'});
                                if (dist < 0.05) unvisitedWaypoints.shift(); 
                                else break; 
                            }

                            if (unvisitedWaypoints.length > 0) {
                                let remainingLine = turf.lineString(unvisitedWaypoints);
                                
                                // 2. Snap to the Asphalt
                                let snappedPoint = turf.nearestPointOnLine(remainingLine, truckPoint);
                                let offRouteDist = turf.distance(truckPoint, snappedPoint, {units: 'kilometers'});

                                let liveLineCoords = [];
                                
                                if (offRouteDist > 0.1) {
                                    // Detached: Leash broken, draw only on the asphalt
                                    liveLineCoords = [...unvisitedWaypoints];
                                } else {
                                    // 3. Attached: [Live Car] -> [Snapped Asphalt Point] -> [Remaining Route]
                                    let snappedCoords = snappedPoint.geometry.coordinates;
                                    liveLineCoords = [[currentLng, currentLat], [snappedCoords[0], snappedCoords[1]], ...unvisitedWaypoints];
                                }

                                let liveLineFeature = turf.lineString(liveLineCoords);
                                activeRouteLayer.clearLayers();
                                activeRouteLayer.addData(liveLineFeature);
                            } else {
                                activeRouteLayer.clearLayers();
                            }
                        } catch (e) { console.warn(e); }
                    } else if (is_idle && activeRouteLayer) {
                        activeRouteLayer.clearLayers(); // Erase the blue line if they go idle
                    }
                };

                ws.onclose = function() {
                    $('#ping-text').text(`Connection Lost. Reconnecting...`).removeClass("text-success text-warning").addClass("text-danger fw-bold");
                    $('#live-pulse').css({ 'background': '#ef4444', 'animation': 'none' });
                    setTimeout(connect_websocket, reconnect_interval);
                };
                ws.onerror = function(error) { ws.close(); };
            }

            // =========================================================================
            // 🚨 3. GOOGLE MAPS SHORTCUTS (No $ string bleed)
            // =========================================================================
            $('#btn-full-route').on('click', function(e) {
                e.stopPropagation();
                let btn = $(this);
                if (!current_manifest_doc) return;

                let d = new frappe.ui.Dialog({
                    title: '<i class="fa fa-satellite-dish text-primary"></i> Traffic Engine',
                    fields: [{ fieldtype: 'HTML', fieldname: 'msg', options: `<div class="p-3 bg-light rounded text-muted">Calculating external global traffic snapshot...</div>` }],
                    primary_action_label: '<i class="fa fa-spinner fa-spin"></i> Establishing Connection...',
                    primary_action: function() {
                        d.hide();
                        let original_html = btn.html();
                        btn.prop('disabled', true).html('<i class="fa fa-spinner fa-spin me-2"></i> Launching...');

                        let stops = (current_manifest_doc.stops || []).filter(s => s.delivery_status === 'Pending');
                        let googleMapsUrl = "http://googleusercontent.com/maps.google.com/dir/?api=1";

                        if (last_known_pos) googleMapsUrl += "&origin=" + last_known_pos.lat + "," + last_known_pos.lng;

                        let coords = [];
                        stops.forEach(s => { if (s.latitude && s.longitude) coords.push(s.latitude + "," + s.longitude); });

                        if (unvisitedWaypoints && unvisitedWaypoints.length > 0) {
                            let final_pt = unvisitedWaypoints[unvisitedWaypoints.length - 1];
                            coords.push(final_pt[1] + "," + final_pt[0]); 
                        }

                        if (coords.length > 10) coords = coords.slice(0, 10);

                        if (coords.length > 0) {
                            let dest = coords.pop(); 
                            googleMapsUrl += "&destination=" + dest;
                            if (coords.length > 0) googleMapsUrl += "&waypoints=" + coords.join('|');
                        } else if (last_known_pos) {
                            googleMapsUrl += "&destination=" + last_known_pos.lat + "," + last_known_pos.lng;
                        }

                        window.open(googleMapsUrl, '_blank');
                        setTimeout(() => { btn.prop('disabled', false).html(original_html); }, 3000);
                    }
                });
                d.show();
                d.get_primary_btn().prop('disabled', true).removeClass('btn-primary').addClass('btn-secondary');
                setTimeout(() => { d.get_primary_btn().prop('disabled', false).removeClass('btn-secondary').addClass('btn-primary').html('Execute'); }, 3000);
            });

            $('#btn-go-customer').on('click', function(e) {
                e.stopPropagation();
                if (!current_manifest_doc) return;
                
                let stops = (current_manifest_doc.stops || []).filter(s => s.delivery_status === 'Pending');
                let stop_options = stops.filter(s => s.latitude && s.longitude).map(s => ({ label: s.customer_name || s.customer, value: s.latitude + "," + s.longitude }));

                if(stop_options.length === 0) return frappe.msgprint("No pending mapped stops.");

                let nav_dialog = new frappe.ui.Dialog({
                    title: 'Navigate to Customer',
                    fields: [{ label: 'Select Destination', fieldname: 'target_coords', fieldtype: 'Select', options: stop_options, reqd: 1 }],
                    primary_action_label: '<i class="fa fa-location-arrow"></i> Start Directions',
                    primary_action(v) {
                        let googleMapsUrl = "http://googleusercontent.com/maps.google.com/dir/?api=1&destination=" + v.target_coords;
                        if (last_known_pos) googleMapsUrl += "&origin=" + last_known_pos.lat + "," + last_known_pos.lng;
                        window.open(googleMapsUrl, '_blank');
                        nav_dialog.hide();
                    }
                });
                nav_dialog.show();
            });

        });
    }
};