// apps/nexus_supply_chain/nexus_supply_chain/page/nexus_sales_dispatch/nexus_sales_dispatch.js

frappe.pages['nexus_sales_dispatch'].on_page_load = function(wrapper) {
    let page = frappe.ui.make_app_page({
        parent: wrapper,
        title: 'Nexus Sales Team Command Center',
        single_column: true 
    });

    // 1. Centered Enterprise Layout (3-Column Architecture)
    $(wrapper).find('.layout-main-section').html(`
        <div class="container-fluid p-0" style="max-width: 1800px; margin: 20px auto; height: 85vh; background: #fff;">
            <div class="row g-0 h-100 border rounded shadow-sm overflow-hidden" style="border-color: #d1d5db !important;">
                
                <div class="col-md-3 border-end d-flex flex-column bg-white" style="max-height: 100%;">
                    <div class="p-3 border-bottom bg-light">
                        <div class="d-flex justify-content-between align-items-center mb-3">
                            <h6 class="m-0 fw-bold text-success"><i class="fa fa-users me-2"></i> ACTIVE FIELD TEAM</h6>
                            <span class="badge bg-success-subtle text-success border border-success" id="conn-stat" style="font-size: 10px;">Connecting...</span>
                        </div>
                        <div class="input-group input-group-sm">
                            <span class="input-group-text bg-white border-end-0"><i class="fa fa-search text-muted"></i></span>
                            <input type="text" class="form-control border-start-0" id="sales-search-active" placeholder="Filter active rep...">
                        </div>
                    </div>
                    <div class="flex-grow-1 overflow-auto p-3" id="active-sales-container" style="background-color: #f8fafc;">
                        </div>
                </div>
                
                <div class="col-md-6 position-relative bg-light border-end">
                    <div id="fleet-map" style="height: 100%; width: 100%; z-index: 1;"></div>
                    
                    <div class="position-absolute top-0 end-0 m-3 p-3 bg-white shadow rounded border" style="z-index: 999; font-size: 11px; min-width: 160px; border-color: #e5e7eb !important;">
                        <div class="fw-bold mb-2 text-dark small text-uppercase">Map Legend</div>
                        <div class="d-flex align-items-center mb-2">
                            <span class="me-2" style="width:12px;height:12px;border-radius:50%;background:#3b82f6;border:2px solid #fff;box-shadow:0 0 4px rgba(59,130,246,0.4);"></span> 
                            Traveling
                        </div>
                        <div class="d-flex align-items-center mb-2">
                            <span class="me-2" style="width:12px;height:12px;border-radius:50%;background:#10b981;border:2px solid #fff;box-shadow:0 0 4px rgba(16,185,129,0.4);"></span> 
                            Checked-In
                        </div>
                        <div class="mt-2 pt-2 border-top text-muted small italic">
                            <i class="fa fa-shield-alt me-1"></i> Hosted Secure Map Engine
                        </div>
                    </div>
                </div>

                <div class="col-md-3 d-flex flex-column bg-white" style="max-height: 100%;">
                    <div class="p-3 border-bottom bg-light">
                        <div class="d-flex justify-content-between align-items-center mb-3">
                            <h6 class="m-0 fw-bold text-secondary"><i class="fa fa-user-clock me-2"></i> OFFLINE TEAM</h6>
                        </div>
                        <div class="input-group input-group-sm">
                            <span class="input-group-text bg-white border-end-0"><i class="fa fa-search text-muted"></i></span>
                            <input type="text" class="form-control border-start-0" id="sales-search-standby" placeholder="Filter offline rep...">
                        </div>
                    </div>
                    <div class="flex-grow-1 overflow-auto p-3" id="standby-sales-container" style="background-color: #f8fafc;">
                         <div class="text-center p-5 text-muted"><i class="fa fa-spinner fa-spin fa-2x"></i><br><br>Loading Database...</div>
                    </div>
                </div>

            </div>
        </div>
    `);

    // 2. Deep Enterprise Styling
    $('head').append(`
        <style>
            .sales-card { 
                padding: 16px; 
                border-radius: 10px; 
                box-shadow: 0 4px 6px -1px rgba(0,0,0,0.1), 0 2px 4px -1px rgba(0,0,0,0.06);
                font-size: 14px; 
                border-left-width: 6px;
                border-left-style: solid;
                transition: transform 0.2s ease, box-shadow 0.2s ease, background-color 0.3s ease;
                margin-bottom: 16px;
                cursor: pointer;
                background-color: #ffffff;
            }
            .sales-card:hover {
                transform: translateY(-2px);
                box-shadow: 0 10px 15px -3px rgba(0,0,0,0.1);
            }
            .sales-card.active-selection { 
                box-shadow: 0 0 0 3px rgba(56, 189, 248, 0.8); 
            }
            
            .card-header { display: flex; flex-direction: column; align-items: flex-start; margin-bottom: 12px; }
            .rep-name { font-size: 16px; font-weight: 800; letter-spacing: 0.2px; color: #1e293b; line-height: 1.2; word-break: break-word; padding-right: 10px; }
            .speed-indicator { font-family: 'Monaco', 'Consolas', monospace; font-weight: 700; font-size: 14px; color: #64748b; white-space: nowrap; }
            
            .status-badge { display: inline-block; padding: 4px 10px; border-radius: 6px; font-size: 10px; font-weight: 800; text-transform: uppercase; letter-spacing: 1px; margin-bottom: 12px; }
            
            .card-meta { font-size: 13px; margin-bottom: 6px; color: #475569; display: flex; align-items: center; }
            .card-meta-icon { margin-right: 8px; width: 16px; text-align: center; color: #94a3b8; }

            .ping-dot { width: 10px; height: 10px; border-radius: 50%; display: inline-block; margin-right: 8px; position: relative; }
            .ping-online { background: #22c55e; box-shadow: 0 0 8px rgba(34, 197, 94, 0.5); animation: pulse-ring 2s infinite; }
            .ping-offline { background: #9ca3af; opacity: 0.5; }
            @keyframes pulse-ring { 0% { transform: scale(0.9); opacity: 1; } 50% { transform: scale(1.1); opacity: 0.7; } 100% { transform: scale(0.9); opacity: 1; } }

            /* Dynamic Theme Colors */
            .theme-traveling { border-left-color: #3b82f6; }
            .theme-traveling .status-badge { background-color: #eff6ff; color: #1d4ed8; border: 1px solid #bfdbfe; }
            
            .theme-checked-in { border-left-color: #10b981; }
            .theme-checked-in .status-badge { background-color: #ecfdf5; color: #047857; border: 1px solid #a7f3d0; }
            
            .theme-offline { border-left-color: #94a3b8; background-color: #f8fafc; opacity: 0.85; }
            .theme-offline .status-badge { background-color: #f1f5f9; color: #475569; border: 1px solid #e2e8f0; }
            .theme-offline .rep-name { color: #475569; }
        </style>
    `);

    // 3. Global State Registry
    let map = null;
    let sales_markers = {};
    let ws = null;
    let pingInterval = null; // 🚨 Added to manage the heartbeat loop
    
    // 🚨 ENDPOINTS 🚨
    const FASTAPI_WS_URL = "wss://api.crystalapps.dev/telemetry/sales-ws";
    const TILE_SERVER_URL = "https://maps.crystalapps.dev/styles/basic-preview/style.json";

    // 🚨 LOAD NATIVE LIBRARIES 🚨
    frappe.require([
        "/assets/nexus_supply_chain/leaflet/leaflet.css", 
        "/assets/nexus_supply_chain/leaflet/leaflet.js",
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
        
        refresh_sales_data();
        connectTelemetryWebSocket();
    });

    // 4. Data Bridge: Fetch Base Sales Team from Backend
    function refresh_sales_data() {
        frappe.call({
            method: "nexus_supply_chain.nexus_supply_chain.page.nexus_sales_dispatch.nexus_sales_dispatch.get_sales_team",
            callback: function(r) {
                let initial_team = r.message || [];
                render_baseline_team(initial_team);
            }
        });
    }

    // Creates the static DOM elements initially. They default to Offline.
    function render_baseline_team(team) {
        let standbyContainer = $('#standby-sales-container').empty();
        $('#active-sales-container').empty(); // Clear spinners

        if(team.length === 0) {
            standbyContainer.html(`<div class="text-center p-5 text-muted">No active Sales Personnel found in system.</div>`);
            return;
        }

        team.forEach(rep => {
            let email = (rep.email || "").toLowerCase(); // 🚨 Absolute Lowercase Shield
            let safe_name = rep.full_name || email || "Unknown Rep";
            let $card = create_card_html(email, safe_name);
            standbyContainer.append($card);
        });
    }

    // 🚨 UPDATE: Improved HTML Generator Strategy (Forces Lowercase data-tid)
    function create_card_html(email, full_name) {
        let lower_name = full_name ? full_name.toLowerCase() : "";
        let display_email = email ? email.toLowerCase() : ""; // 🚨 Absolute Lowercase Shield
        
        // Added email to data-name so search works for both Name and Email
        return $(`
            <div class="sales-card theme-offline" data-tid="${display_email}" data-name="${lower_name} ${display_email}">
                <div class="card-header">
                    <div style="display: flex; justify-content: space-between; width: 100%;">
                        <span class="rep-name">${full_name}</span>
                        <span class="speed-indicator speed-val">--</span>
                    </div>
                    <span style="font-size: 11px; color: #64748b; margin-top: 4px;"><i class="fa fa-envelope-o me-1"></i> ${display_email}</span>
                </div>
                <div class="status-badge status-val">● OFFLINE</div>
                <div class="card-meta"><i class="fa fa-building card-meta-icon"></i> <span class="customer-val text-truncate" style="max-width: 180px;">None</span></div>
                <div class="mt-2 d-flex align-items-center small rounded" style="background: rgba(0,0,0,0.03); padding: 8px;">
                    <span class="ping-dot ping-offline"></span>
                    <span class="stat-text text-muted">Awaiting Connection...</span>
                </div>
            </div>
        `);
    }

    // 5. THE TELEMETRY ENGINE (0-Lag DOM Rendering)
    function connectTelemetryWebSocket() {
        if (ws && ws.readyState === WebSocket.OPEN) return;

        ws = new WebSocket(FASTAPI_WS_URL);

        ws.onopen = () => {
            $('#conn-stat').text('WS Live').removeClass('text-danger border-danger').addClass('text-success border-success');
            
            // 🚨 CLEAN HEARTBEAT: Prevent memory leaks on reconnect
            if (pingInterval) clearInterval(pingInterval);
            
            // 30-Second Bidirectional Heartbeat to keep Nginx tunnel open
            pingInterval = setInterval(() => {
                if (ws && ws.readyState === WebSocket.OPEN) {
                    ws.send(JSON.stringify({ action: "ping" }));
                }
            }, 30000);
        };

        ws.onmessage = (event) => {
            requestAnimationFrame(() => {
                try {
                    const data = JSON.parse(event.data);
                    
                    // 🚨 Ignore heartbeat responses from the server
                    if (data.action === "pong") return;
                    
                    const raw_sales_team = data.sales_team || {};
                    
                    // 🚨 ABSOLUTE STRING NORMALIZATION: Map incoming payload to strictly lowercase
                    const sales_team = {};
                    Object.keys(raw_sales_team).forEach(k => {
                        sales_team[k.toLowerCase()] = raw_sales_team[k];
                    });

                    // A. Update Live Reps & Shift to Active Container
                    Object.keys(sales_team).forEach(email => {
                        let rep = sales_team[email];
                        let speedKmh = Math.round((rep.speed || 0) * 3.6);
                        
                        let $card = $('.sales-card').filter(function() {
                            return $(this).attr('data-tid') === email;
                        });

                        // 🚨 DYNAMIC CARD INJECTION: If they don't exist in DOM, build them on the fly
                        if ($card.length === 0) {
                            let safe_name = rep.full_name || email || "Unknown Rep";
                            let newCardHtml = create_card_html(email, safe_name);
                            $('#active-sales-container').append(newCardHtml);
                            
                            // Re-select the newly injected card to apply styling updates
                            $card = $('.sales-card').filter(function() {
                                return $(this).attr('data-tid') === email;
                            });
                        } else if ($card.parent().attr('id') !== 'active-sales-container') {
                            // Shift to Active container if it was offline
                            $('#active-sales-container').append($card);
                        }

                        // Update DOM States smoothly
                        $card.removeClass('theme-offline theme-traveling theme-checked-in');
                        let color = '#3b82f6'; // Default Blue for traveling
                        
                        if (rep.status === 'Checked-In') {
                            $card.addClass('theme-checked-in');
                            color = '#10b981'; // Green
                        } else {
                            $card.addClass('theme-traveling');
                        }

                        $card.find('.ping-dot').removeClass('ping-offline').addClass('ping-online');
                        $card.find('.stat-text').text('Live Tracking').removeClass('text-muted').addClass('text-dark fw-bold');
                        $card.find('.speed-val').text(`${speedKmh} km/h`);
                        $card.find('.status-val').text(`● ${rep.status.toUpperCase()}`);
                        
                        let customerDisplay = rep.current_customer && rep.current_customer !== 'None' ? rep.current_customer : 'In Transit';
                        $card.find('.customer-val').text(customerDisplay);

                        // Update or Create the Map Marker (Gender Neutral SVG Profile)
                        let heading = rep.heading || 0;

                        if (sales_markers[email]) {
                            sales_markers[email].setLatLng([rep.lat, rep.lng]);
                            let iconElement = sales_markers[email].getElement();
                            if (iconElement) {
                                let arrow = iconElement.querySelector('.direction-ring');
                                if (arrow) arrow.style.transform = `rotate(${heading}deg)`;
                            }
                        } else {
                            let htmlIcon = `
                                <div style="position:relative; width:34px; height:34px;">
                                    <div class="direction-ring" style="position:absolute; top:0; left:0; width:100%; height:100%; transform: rotate(${heading}deg); transition: transform 0.5s linear;">
                                        <svg width="34" height="34" viewBox="0 0 24 24" fill="none" stroke="${color}" stroke-width="2" style="opacity:0.6;">
                                            <path d="M12 2L15 8L12 6L9 8L12 2Z" fill="${color}"/>
                                        </svg>
                                    </div>
                                    <div style="position:absolute; top:4px; left:4px; background:#fff; border-radius:50%; width:26px; height:26px; box-shadow: 0 2px 5px rgba(0,0,0,0.3); display:flex; align-items:center; justify-content:center; border: 2px solid ${color};">
                                        <svg width="16" height="16" viewBox="0 0 24 24" fill="${color}" xmlns="http://www.w3.org/2000/svg">
                                            <path d="M12 12c2.21 0 4-1.79 4-4s-1.79-4-4-4-4 1.79-4 4 1.79 4 4 4zm0 2c-2.67 0-8 1.34-8 4v2h16v-2c0-2.66-5.33-4-8-4z"/>
                                        </svg>
                                    </div>
                                </div>`;

                            let icon = L.divIcon({ className: '', html: htmlIcon, iconSize: [34, 34], iconAnchor: [17, 17] });
                            
                            let safe_popup_name = rep.full_name || email || "Unknown";
                            let popupText = `<b>${safe_popup_name}</b><br><span class="text-muted small">${rep.status}</span>`;
                            sales_markers[email] = L.marker([rep.lat, rep.lng], { icon: icon }).addTo(map).bindPopup(`<div class="p-1">${popupText}</div>`);
                        }
                    });

                    // B. The Purge Fix: Maintain Offline shifting using the normalized keys
                    $('.sales-card').each(function() {
                        let dom_email = $(this).attr('data-tid'); // Already lowercase

                        // If DOM email isn't in the newly sanitized sales_team payload
                        if (!sales_team[dom_email]) {
                            // If they are missing from WS, shift to Standby
                            if ($(this).parent().attr('id') !== 'standby-sales-container') {
                                $('#standby-sales-container').append($(this));
                            }
                            
                            // Revert styles
                            $(this).removeClass('theme-traveling theme-checked-in').addClass('theme-offline');
                            $(this).find('.ping-dot').removeClass('ping-online').addClass('ping-offline');
                            $(this).find('.stat-text').text('Offline').removeClass('text-dark fw-bold').addClass('text-muted');
                            $(this).find('.speed-val').text(`--`);
                            $(this).find('.status-val').text(`● OFFLINE`);
                            $(this).find('.customer-val').text('None');

                            // Purge Marker from Map
                            if (sales_markers[dom_email]) {
                                map.removeLayer(sales_markers[dom_email]);
                                delete sales_markers[dom_email];
                            }
                        }
                    });

                } catch (e) {
                    console.error("Payload execution error:", e);
                }
            });
        };

        ws.onclose = () => {
            $('#conn-stat').text('Reconnecting...').removeClass('text-success border-success').addClass('text-danger border-danger');
            // 🚨 Clean up the interval when closed to prevent memory leaks
            if (pingInterval) clearInterval(pingInterval);
            setTimeout(connectTelemetryWebSocket, 3000); 
        };

        ws.onerror = () => { ws.close(); };
    }

    // =========================================================================
    // SEARCH & FILTER INTERACTIVITY
    // =========================================================================
    function applySearch(inputId, containerId) {
        $(inputId).on('keyup', function() {
            let val = $(this).val().toLowerCase();
            $(`${containerId} .sales-card`).each(function() {
                let name = $(this).attr('data-name');
                if(name) {
                   $(this).toggle(name.includes(val));
                }
            });
        });
    }
    
    applySearch('#sales-search-active', '#active-sales-container');
    applySearch('#sales-search-standby', '#standby-sales-container');

    // =========================================================================
    // MAP ZOOM INTERACTION
    // =========================================================================
    $(wrapper).on('click', '.sales-card', function(e) {
        let raw_email = $(this).attr('data-tid'); 
        
        $('.sales-card').removeClass('active-selection');
        $(this).addClass('active-selection');

        if (sales_markers[raw_email]) {
            map.flyTo(sales_markers[raw_email].getLatLng(), 16, { duration: 1.2 });
            sales_markers[raw_email].openPopup();
        } else {
            frappe.show_alert({message: 'User is currently offline. No live GPS fix available.', indicator: 'orange'});
        }
    });
};