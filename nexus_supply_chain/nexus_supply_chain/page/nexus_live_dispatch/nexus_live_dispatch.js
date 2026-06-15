frappe.pages['nexus_live_dispatch'].on_page_load = function(wrapper) {
    let page = frappe.ui.make_app_page({
        parent: wrapper,
        title: 'Nexus Fleet Command Center',
        single_column: true 
    });

    $(wrapper).find('.layout-main-section').html(`
        <div class="container-fluid p-0" style="max-width: 1800px; margin: 20px auto; height: 85vh; background: #fff;">
            <div class="row g-0 h-100 border rounded shadow-sm overflow-hidden" style="border-color: #d1d5db !important;">
                
                <div class="col-md-3 border-end d-flex flex-column bg-white" style="max-height: 100%;">
                    <div class="p-3 border-bottom bg-light">
                        <div class="d-flex justify-content-between align-items-center mb-3">
                            <h6 class="m-0 fw-bold text-success"><i class="fa fa-truck me-2"></i> ACTIVE FLEET</h6>
                            <span class="badge bg-success-subtle text-success border border-success" id="conn-stat" style="font-size: 10px;">Connecting...</span>
                        </div>
                        <div class="input-group input-group-sm">
                            <span class="input-group-text bg-white border-end-0"><i class="fa fa-search text-muted"></i></span>
                            <input type="text" class="form-control border-start-0" id="fleet-search-active" placeholder="Filter active vehicle...">
                        </div>
                    </div>
                    <div class="flex-grow-1 overflow-auto p-3" id="active-fleet-container" style="background-color: #f8fafc;">
                        <div class="text-center p-5 text-muted"><i class="fa fa-spinner fa-spin fa-2x"></i><br><br>Loading Active Fleet...</div>
                    </div>
                </div>
                
                <div class="col-md-6 position-relative bg-light border-end">
                    <div id="fleet-map" style="height: 100%; width: 100%; z-index: 1;"></div>
                    
                    <div class="position-absolute top-0 end-0 m-3 p-3 bg-white shadow rounded border" style="z-index: 999; font-size: 11px; min-width: 160px; border-color: #e5e7eb !important;">
                        <div class="fw-bold mb-2 text-dark small text-uppercase">Map Legend</div>
                        <div class="d-flex align-items-center mb-2">
                            <span class="me-2" style="width:12px;height:12px;border-radius:50%;background:#3b82f6;border:2px solid #fff;box-shadow:0 0 4px rgba(59,130,246,0.4);"></span> 
                            Loading / Ready
                        </div>
                        <div class="d-flex align-items-center mb-2">
                            <span class="me-2" style="width:12px;height:12px;border-radius:50%;background:#10b981;border:2px solid #fff;box-shadow:0 0 4px rgba(16,185,129,0.4);"></span> 
                            En Route
                        </div>
                        <div class="d-flex align-items-center mb-2">
                            <span class="me-2" style="width:12px;height:12px;border-radius:50%;background:#f59e0b;border:2px solid #fff;box-shadow:0 0 4px rgba(245,158,11,0.4);"></span> 
                            Returning
                        </div>
                        <div class="d-flex align-items-center mb-1">
                            <span class="me-2" style="width:12px;height:2px;background:#9ca3af;border-top:2px dashed #9ca3af;"></span> 
                            Planned Route (Grey)
                        </div>
                        <div class="d-flex align-items-center mb-1">
                            <span class="me-2" style="width:12px;height:2px;background:#2563eb;"></span> 
                            Active Remaining (Blue)
                        </div>
                        <div class="mt-2 pt-2 border-top text-muted small italic">
                            <i class="fa fa-shield-alt me-1"></i> Hosted Secure Map Engine
                        </div>
                    </div>
                </div>

                <div class="col-md-3 d-flex flex-column bg-white" style="max-height: 100%;">
                    <div class="p-3 border-bottom bg-light">
                        <div class="d-flex justify-content-between align-items-center mb-3">
                            <h6 class="m-0 fw-bold text-secondary"><i class="fa fa-parking me-2"></i> STANDBY FLEET</h6>
                        </div>
                        <div class="input-group input-group-sm">
                            <span class="input-group-text bg-white border-end-0"><i class="fa fa-search text-muted"></i></span>
                            <input type="text" class="form-control border-start-0" id="fleet-search-standby" placeholder="Filter standby vehicle...">
                        </div>
                    </div>
                    <div class="flex-grow-1 overflow-auto p-3" id="standby-fleet-container" style="background-color: #f8fafc;">
                         <div class="text-center p-5 text-muted"><i class="fa fa-spinner fa-spin fa-2x"></i><br><br>Loading Standby Fleet...</div>
                    </div>
                </div>

            </div>
        </div>
    `);

    $('head').append(`
        <style>
            .vehicle-card { 
                padding: 20px; 
                border-radius: 10px; 
                box-shadow: 0 10px 15px -3px rgba(0,0,0,0.1), 0 4px 6px -2px rgba(0,0,0,0.05);
                font-size: 14px; 
                border-left-width: 6px;
                border-left-style: solid;
                transition: transform 0.2s ease, box-shadow 0.2s ease;
                margin-bottom: 16px;
                cursor: pointer;
            }
            .vehicle-card:hover {
                transform: translateY(-3px);
                box-shadow: 0 15px 25px -5px rgba(0,0,0,0.15);
            }
            .vehicle-card.active-selection { 
                box-shadow: 0 0 0 3px rgba(56, 189, 248, 0.8); 
            }
            
            .status-group-header { font-size: 11px; font-weight: 800; text-transform: uppercase; letter-spacing: 0.12em; margin: 20px 0 12px 5px; display: flex; align-items: center; color: #64748b; }
            .status-group-header::after { content: ""; flex: 1; height: 1px; background: #e2e8f0; margin-left: 12px; }

            .card-header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 12px; }
            .plate-number { font-size: 18px; font-weight: 900; letter-spacing: 0.5px; }
            .speed-indicator { font-family: 'Monaco', 'Consolas', monospace; font-weight: 700; font-size: 16px; }
            
            .status-badge { display: inline-block; padding: 4px 10px; border-radius: 6px; font-size: 11px; font-weight: 800; text-transform: uppercase; letter-spacing: 1px; margin-bottom: 12px; border: 1px solid rgba(255,255,255,0.2); }
            
            .card-meta { font-size: 13px; margin-bottom: 8px; opacity: 0.95; display: flex; align-items: center; }
            .card-meta-icon { margin-right: 8px; opacity: 0.7; width: 16px; text-align: center; }

            .card-btn { background: rgba(255,255,255,0.1); border: 1px solid rgba(255,255,255,0.2); padding: 10px 15px; border-radius: 6px; font-size: 13px; cursor: pointer; text-align: center; width: 100%; font-weight: 700; margin-top: 15px; transition: background 0.2s; }
            .card-btn:hover { background: rgba(255,255,255,0.25); }

            .ping-dot { width: 10px; height: 10px; border-radius: 50%; display: inline-block; margin-right: 8px; position: relative; }
            .ping-online { background: #22c55e; box-shadow: 0 0 8px rgba(34, 197, 94, 0.5); animation: pulse-ring 2s infinite; }
            .ping-offline { background: #9ca3af; opacity: 0.5; }
            @keyframes pulse-ring { 0% { transform: scale(0.9); opacity: 1; } 50% { transform: scale(1.1); opacity: 0.7; } 100% { transform: scale(0.9); opacity: 1; } }

            .theme-transit { background-color: #064e3b; border-left-color: #10b981; color: #ecfdf5; }
            .theme-transit .status-badge { background-color: #047857; color: #ffffff; }
            
            .theme-loading { background-color: #1e3a8a; border-left-color: #3b82f6; color: #eff6ff; }
            .theme-loading .status-badge { background-color: #1d4ed8; color: #ffffff; }
            
            .theme-returning { background-color: #78350f; border-left-color: #f59e0b; color: #fffbeb; }
            .theme-returning .status-badge { background-color: #b45309; color: #ffffff; }
            
            .theme-idle { background-color: #334155; border-left-color: #94a3b8; color: #f8fafc; }
            .theme-idle .status-badge { background-color: #475569; color: #ffffff; }
            
            .theme-maintenance { background-color: #881337; border-left-color: #f43f5e; color: #fff1f2; }
            .theme-maintenance .status-badge { background-color: #be123c; color: #ffffff; }
        </style>
    `);

    let map = null;
    let vehicle_markers = {};
    
    let static_route_layers = {}; 
    let active_route_layers = {}; 
    let unvisited_waypoints = {}; 
    
    let last_math_calc = {}; 
    let ws = null;
    
    const FASTAPI_WS_URL = "wss://api.crystalapps.dev/telemetry/ws";
    
    const TILE_SERVER_URL = "https://maps.crystalapps.dev/styles/basic-preview/style.json";

    frappe.require([
        "/assets/nexus_supply_chain/leaflet/leaflet.css", 
        "/assets/nexus_supply_chain/leaflet/leaflet.js",
        "https://cdnjs.cloudflare.com/ajax/libs/Turf.js/6.5.0/turf.min.js",
        "https://unpkg.com/maplibre-gl@3.6.2/dist/maplibre-gl.css",
        "https://unpkg.com/maplibre-gl@3.6.2/dist/maplibre-gl.js",
        "https://unpkg.com/@maplibre/maplibre-gl-leaflet@0.0.20/leaflet-maplibre-gl.js"
    ], function() {
        
        map = L.map('fleet-map', { zoomControl: false }).setView([-1.2921, 36.8219], 12);
        L.control.zoom({ position: 'topright' }).addTo(map);
        
        L.maplibreGL({
            style: TILE_SERVER_URL,
            attribution: '© Sovereign Nexus Maps'
        }).addTo(map);
        
        refresh_dispatch_data();
        connectTelemetryWebSocket();
    });

    function refresh_dispatch_data() {
        frappe.call({
            method: "frappe.client.get_list",
            args: {
                doctype: "Vehicle In Transit",
                fields: ["name", "vehicle_plate", "current_driver", "current_status"],
                limit: 1000
            },
            callback: function(r) {
                if (r.message) {
                    fetch_active_manifests_and_render(r.message);
                }
            }
        });
    }

    function fetch_active_manifests_and_render(vehicles) {
        frappe.call({
            method: "frappe.client.get_list",
            args: {
                doctype: "Vehicle Delivery Manifest",
                filters: [["trip_status", "in", ["Ready", "Dispatched", "Completed"]]],
                fields: ["name", "driver", "vehicle", "trip_status", "route_geojson"],
                limit: 1000
            },
            callback: function(r) {
                let manifests = r.message || [];
                process_fleet_data(vehicles, manifests);
            }
        });
    }

    function process_fleet_data(vehicles, manifests) {
        let activeContainer = $('#active-fleet-container').empty();
        let standbyContainer = $('#standby-fleet-container').empty();

        let manifestMap = {};
        manifests.forEach(m => manifestMap[m.vehicle] = m);

        let groups = {
            'LOADING': [], 'EN ROUTE': [], 'RETURNING': [],
            'IDLE': [], 'MAINTENANCE': []
        };

        vehicles.forEach(v => {
            let status = v.current_status ? v.current_status.toUpperCase() : 'IDLE';
            let m = manifestMap[v.name];
            
            if (m && m.trip_status === 'Ready') {
                status = 'LOADING';
            }
            
            if (status === 'IDLE' || status === 'MAINTENANCE') {
                m = null; 
            }
            
            if (status === 'LOADING') {
                groups['LOADING'].push(v);
            } else if (groups[status]) {
                groups[status].push(v);
            } else {
                groups['IDLE'].push(v);
            }

            if (m && m.route_geojson && !static_route_layers[v.name]) {
                try {
                    let parsedGeoJSON = JSON.parse(m.route_geojson);
                    let staticLayer = L.geoJSON(parsedGeoJSON, {
                        style: { color: '#9ca3af', weight: 4, opacity: 0.5, dashArray: '5, 5' }
                    }).addTo(map);
                    static_route_layers[v.name] = staticLayer;

                    if (parsedGeoJSON.features && parsedGeoJSON.features.length > 0) {
                        let lineFeature = parsedGeoJSON.features.find(f => f.geometry.type === 'LineString');
                        if (lineFeature) {
                            unvisited_waypoints[v.name] = [...lineFeature.geometry.coordinates];
                        }
                    }

                    let activeLayer = L.geoJSON(null, {
                        style: { color: '#2563eb', weight: 5, opacity: 0.9 }
                    }).addTo(map);
                    active_route_layers[v.name] = activeLayer;
                } catch(e) {
                    console.warn(`Failed to parse GeoJSON for vehicle ${v.name}`);
                }
            }
        });

        render_group(activeContainer, 'Loading / Ready', groups['LOADING'], manifestMap, 'theme-loading');
        render_group(activeContainer, 'En Route', groups['EN ROUTE'], manifestMap, 'theme-transit');
        render_group(activeContainer, 'Returning', groups['RETURNING'], manifestMap, 'theme-returning');
        
        render_group(standbyContainer, 'Idle', groups['IDLE'], manifestMap, 'theme-idle');
        render_group(standbyContainer, 'Maintenance', groups['MAINTENANCE'], manifestMap, 'theme-maintenance');
    }

	function render_group(container, title, items, manifestMap, themeClass) {
		if (items.length === 0) return;
		
		container.append(`<div class="status-group-header">${title}</div>`);
		
		items.forEach(v => {
			let m = manifestMap[v.name];
			if (title === 'Idle' || title === 'Maintenance') {
				m = null; 
			}

			let manifestText = m 
				? `<div class="card-meta"><span class="card-meta-icon">📄</span> Manifest: ${m.name}</div>` 
				: `<div class="card-meta" style="opacity: 0.6;"><span class="card-meta-icon">📄</span> No Active Manifest</div>`;
			
			let driver_email = v.current_driver || "Unknown_Driver";
			let vehicle_id = v.name || "Idle";
			let trackingKey = `${driver_email}::${vehicle_id}`;

			let btnHtml = m && m.trip_status !== 'Ready' ? `
				<button class="card-btn btn-gmaps" data-manifest-id="${m.name}" data-vehicle-id="${v.name}" data-tid="${trackingKey}" style="color: inherit;">
					<i class="fa fa-traffic-light me-2 text-warning"></i> View Live Traffic & ETA
				</button>` : '';

			container.append(`
				<div class="vehicle-card ${themeClass}" data-vehicle="${v.name}" data-tid="${trackingKey}">
					<div class="card-header">
						<span class="plate-number">${v.name}</span>
						<span class="speed-indicator speed-val" data-tid="${trackingKey}">0 km/h</span>
					</div>
					<div class="status-badge">● ${title}</div>
					<div class="card-meta"><span class="card-meta-icon">👤</span> Operator: ${v.current_driver || 'Unassigned'}</div>
					${manifestText}
					${btnHtml}
					<div class="mt-3 d-flex align-items-center small rounded" style="background: rgba(0,0,0,0.2); padding: 8px;">
						<span class="ping-dot ping-offline" data-tid="${trackingKey}"></span>
						<span class="stat-text" data-tid="${trackingKey}" style="opacity: 0.9;">Offline</span>
					</div>
				</div>
			`);
		});
	}

    $(wrapper).on('click', '.btn-gmaps', function(e) {
        e.stopPropagation(); 
        let btn = $(this);
        let manifest_id = btn.attr('data-manifest-id');
        let vehicle_id = btn.attr('data-vehicle-id');
        let trackingKey = btn.attr('data-tid');

        if (!localStorage.getItem('nexus_google_educated')) {
            let d = new frappe.ui.Dialog({
                title: '<i class="fa fa-info-circle text-primary"></i> Traffic Engine Protocol',
                fields: [
                    { 
                        fieldtype: 'HTML', 
                        fieldname: 'msg', 
                        options: `<div class="p-2 text-muted" style="line-height: 1.6;">
                            This tool securely bridges to Google Maps to calculate accurate ETAs based on global traffic.<br><br>
                            <b class="text-dark">Note:</b> The external map will not display live truck animation. Return here for live physical tracking.
                        </div>` 
                    },
                    { fieldtype: 'Check', fieldname: 'dont_show_again', label: "Don't show this again." }
                ],
                primary_action_label: 'Execute',
                primary_action: function(values) {
                    if (values.dont_show_again) localStorage.setItem('nexus_google_educated', 'true');
                    d.hide();
                    triggerDeepLinkGenerator(manifest_id, vehicle_id, trackingKey, btn);
                }
            });
            d.show();
        } else {
            triggerDeepLinkGenerator(manifest_id, vehicle_id, trackingKey, btn);
        }
    });

    function triggerDeepLinkGenerator(manifest_id, vehicle_id, trackingKey, btn) {
        let original_html = btn.html();
        btn.prop('disabled', true).html('<i class="fa fa-spinner fa-spin me-2"></i> Syncing...');

        let marker = vehicle_markers[trackingKey];
        let current_pos = marker ? marker.getLatLng() : null;
        
        if (!current_pos) {
            frappe.msgprint("Awaiting initial GPS lock from vehicle.");
            btn.prop('disabled', false).html(original_html);
            return;
        }

        frappe.call({
            method: 'frappe.client.get_list',
            args: {
                doctype: 'In Transit-Delivery Stop',
                filters: { parent: manifest_id, delivery_status: 'Pending' },
                fields: ['latitude', 'longitude'],
                order_by: 'idx asc'
            },
            callback: function(r) {
                let stops = r.message || [];
                
                let origin = `${current_pos.lat},${current_pos.lng}`;
                let waypoints = [];
                let destination = "";

                stops.forEach((s, i) => {
                    if (s.latitude && s.longitude) {
                        if (i === stops.length - 1) {
                            destination = `${s.latitude},${s.longitude}`;
                        } else {
                            waypoints.push(`${s.latitude},${s.longitude}`);
                        }
                    }
                });

                if (!destination) {
                    let remaining = unvisited_waypoints[vehicle_id];
                    if (remaining && remaining.length > 0) {
                        let final_pt = remaining[remaining.length - 1];
                        destination = `${final_pt[1]},${final_pt[0]}`; 
                    } else {
                        destination = origin; 
                    }
                }

                let url = `https://www.google.com/maps/dir/?api=1&origin=${origin}&destination=${destination}&travelmode=driving`;
                if (waypoints.length > 0) {
                    url += `&waypoints=${waypoints.slice(0, 8).join('|')}`;
                }

                window.open(url, '_blank');
                setTimeout(() => btn.prop('disabled', false).html(original_html), 3000);
            }
        });
    }

    function connectTelemetryWebSocket() {
        if (ws && ws.readyState === WebSocket.OPEN) return;

        ws = new WebSocket(FASTAPI_WS_URL);

        ws.onopen = () => {
            $('#conn-stat').text('WS Live').removeClass('text-danger border-danger').addClass('text-success border-success');
        };

        ws.onmessage = (event) => {
            requestAnimationFrame(() => {
                try {
                    const data = JSON.parse(event.data);
                    const fleet = data.fleet || {};
                    const now = Date.now();

                    Object.keys(fleet).forEach(tracking_id => {
                        let v = fleet[tracking_id];
                        let speedKmh = Math.round(v.speed * 3.6);
                        
                        let exact_tid = v.tracking_id || tracking_id;
                        let safe_tid = exact_tid.replace(/"/g, '\\"'); 
                        
                        let $dot = $(`.ping-dot[data-tid="${safe_tid}"]`);
                        let $stat = $(`.stat-text[data-tid="${safe_tid}"]`);
                        let $speed = $(`.speed-val[data-tid="${safe_tid}"]`);

                        $dot.removeClass('ping-offline').addClass('ping-online');
                        $stat.text('Live').css('opacity', '1');
                        $speed.text(`${speedKmh} km/h`);

                        let heading = v.heading || 0;
                        
                        let color = '#2563eb'; 
                        let cardColor = $(`.vehicle-card[data-tid="${safe_tid}"]`).css('border-left-color');
                        if (cardColor) color = cardColor;

                        if (vehicle_markers[exact_tid]) {
                            vehicle_markers[exact_tid].setLatLng([v.lat, v.lng]);
                            let iconElement = vehicle_markers[exact_tid].getElement();
                            if (iconElement) {
                                let arrow = iconElement.querySelector('.truck-arrow');
                                if (arrow) arrow.style.transform = `rotate(${heading}deg)`;
                            }
                        } else {
                            let htmlIcon = `
                                <div style="background:#fff; border-radius:50%; width:24px; height:24px; box-shadow: 0 0 10px rgba(0,0,0,0.3); display:flex; align-items:center; justify-content:center;">
                                    <div class="truck-arrow" style="transform: rotate(${heading}deg); transition: transform 0.5s linear;">
                                        <svg width="14" height="14" viewBox="0 0 24 24" fill="${color}" xmlns="http://www.w3.org/2000/svg">
                                            <path d="M12 2L22 20L12 17L2 20L12 2Z" />
                                        </svg>
                                    </div>
                                </div>`;

                            let icon = L.divIcon({ className: '', html: htmlIcon, iconSize: [24, 24] });
                            
                            let popupText = v.vehicle === 'Idle' 
                                ? `<b>Unassigned Driver</b><br><span class="text-muted small">${v.driver}</span>`
                                : `<b>${v.vehicle}</b><br><span class="text-muted small">${v.driver}</span>`;

                            vehicle_markers[exact_tid] = L.marker([v.lat, v.lng], { icon: icon }).addTo(map).bindPopup(`<div class="p-1">${popupText}</div>`);
                        }

                        let physical_vehicle_name = v.vehicle;
                        
                        if (!last_math_calc[physical_vehicle_name] || (now - last_math_calc[physical_vehicle_name] > 1000)) {
                            last_math_calc[physical_vehicle_name] = now;
                            
                            if (physical_vehicle_name !== 'Idle' && unvisited_waypoints[physical_vehicle_name] && typeof turf !== 'undefined') {
                                let truckPoint = turf.point([v.lng, v.lat]);
                                let remainingCoords = unvisited_waypoints[physical_vehicle_name];

                                while (remainingCoords.length > 0) {
                                    let nextPoint = turf.point(remainingCoords[0]);
                                    let dist = turf.distance(truckPoint, nextPoint, {units: 'kilometers'});
                                    if (dist < 0.05) remainingCoords.shift(); 
                                    else break; 
                                }

                                if (remainingCoords.length > 0) {
                                    let remainingLine = turf.lineString(remainingCoords);
                                    let snappedPoint = turf.nearestPointOnLine(remainingLine, truckPoint);
                                    let offRouteDist = turf.distance(truckPoint, snappedPoint, {units: 'kilometers'});

                                    let liveLineCoords = [];
                                    if (offRouteDist > 0.1) {
                                        liveLineCoords = [...remainingCoords]; 
                                    } else {
                                        liveLineCoords = [[v.lng, v.lat], ...remainingCoords]; 
                                    }

                                    if (active_route_layers[physical_vehicle_name]) {
                                        active_route_layers[physical_vehicle_name].clearLayers();
                                        active_route_layers[physical_vehicle_name].addData(turf.lineString(liveLineCoords));
                                    }
                                } else {
                                    if (active_route_layers[physical_vehicle_name]) active_route_layers[physical_vehicle_name].clearLayers();
                                }
                            }
                        }
                    });

                    Object.keys(vehicle_markers).forEach(marker_id => {
                        if (!fleet[marker_id]) {
                            map.removeLayer(vehicle_markers[marker_id]);
                            delete vehicle_markers[marker_id];
                            
                            let physical_vehicle = marker_id.split('::')[1];

                            if (physical_vehicle && physical_vehicle !== 'Idle') {
                                if(active_route_layers[physical_vehicle]) {
                                    map.removeLayer(active_route_layers[physical_vehicle]);
                                    delete active_route_layers[physical_vehicle];
                                }
                                
                                if(static_route_layers[physical_vehicle]) {
                                    map.removeLayer(static_route_layers[physical_vehicle]);
                                    delete static_route_layers[physical_vehicle];
                                }

                                delete unvisited_waypoints[physical_vehicle];
                                delete last_math_calc[physical_vehicle];
                            }

                            let safe_tid = marker_id.replace(/"/g, '\\"'); 
                            $(`.ping-dot[data-tid="${safe_tid}"]`).removeClass('ping-online').addClass('ping-offline');
                            $(`.stat-text[data-tid="${safe_tid}"]`).text('Offline').css('opacity', '0.8');
                            $(`.speed-val[data-tid="${safe_tid}"]`).text(`0 km/h`);
                        }
                    });

                } catch (e) {
                    console.error("Payload execution error:", e);
                }
            });
        };

        ws.onclose = () => {
            $('#conn-stat').text('Reconnecting...').removeClass('text-success border-success').addClass('text-danger border-danger');
            setTimeout(connectTelemetryWebSocket, 3000); 
        };

        ws.onerror = () => { ws.close(); };
    }

    $(wrapper).on('click', '.vehicle-card', function(e) {
        if ($(e.target).closest('.btn-gmaps').length) return; 

        let tid = $(this).attr('data-tid'); 
        let v_name = $(this).attr('data-vehicle'); 
        
        $('.vehicle-card').removeClass('active-selection');
        $(this).addClass('active-selection');

        if (vehicle_markers[tid]) {
            map.flyTo(vehicle_markers[tid].getLatLng(), 15, { duration: 1.2 });
            vehicle_markers[tid].openPopup();
        } else if (static_route_layers[v_name]) {
            map.fitBounds(static_route_layers[v_name].getBounds(), { padding: [50, 50], duration: 1.2 });
        }
    });
};
