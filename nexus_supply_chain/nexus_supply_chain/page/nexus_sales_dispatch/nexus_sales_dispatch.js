frappe.pages['nexus_sales_dispatch'].on_page_load = function(wrapper) {
    let page = frappe.ui.make_app_page({
        parent: wrapper,
        title: 'Nexus Sales Team Command Center',
        single_column: true 
    });

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
                    <div class="flex-grow-1 overflow-auto p-3" id="active-sales-container" style="background-color: #f8fafc;"></div>
                </div>
                
                <div class="col-md-6 position-relative bg-light border-end">
                    <div id="fleet-map" style="height: 100%; width: 100%; z-index: 1;"></div>
                    <div class="position-absolute top-0 end-0 m-3 p-3 bg-white shadow rounded border" style="z-index: 999; font-size: 11px; min-width: 160px; border-color: #e5e7eb !important;">
                        <div class="fw-bold mb-2 text-dark small text-uppercase">Map Legend</div>
                        <div class="d-flex align-items-center mb-2">
                            <span class="me-2" style="width:12px;height:12px;border-radius:50%;background:#3b82f6;border:2px solid #fff;box-shadow:0 0 4px rgba(59,130,246,0.4);display:inline-block;"></span> 
                            Traveling
                        </div>
                        <div class="d-flex align-items-center mb-2">
                            <span class="me-2" style="width:12px;height:12px;border-radius:50%;background:#10b981;border:2px solid #fff;box-shadow:0 0 4px rgba(16,185,129,0.4);display:inline-block;"></span> 
                            Checked-In
                        </div>
                        <div class="mt-2 pt-2 border-top text-muted small">
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
                will-change: transform;
            }
            .sales-card:hover {
                transform: translateY(-2px);
                box-shadow: 0 10px 15px -3px rgba(0,0,0,0.1);
            }
            .sales-card.active-selection { 
                box-shadow: 0 0 0 3px rgba(56, 189, 248, 0.8); 
            }
            .card-header-row { display: flex; justify-content: space-between; width: 100%; align-items: flex-start; margin-bottom: 4px; }
            .rep-name { font-size: 16px; font-weight: 800; letter-spacing: 0.2px; color: #1e293b; line-height: 1.2; word-break: break-word; padding-right: 10px; }
            .speed-indicator { font-family: 'Monaco', 'Consolas', monospace; font-weight: 700; font-size: 14px; color: #64748b; white-space: nowrap; }
            .status-badge { display: inline-block; padding: 4px 10px; border-radius: 6px; font-size: 10px; font-weight: 800; text-transform: uppercase; letter-spacing: 1px; margin-bottom: 12px; }
            .card-meta { font-size: 13px; margin-bottom: 6px; color: #475569; display: flex; align-items: center; }
            .card-meta-icon { margin-right: 8px; width: 16px; text-align: center; color: #94a3b8; }
            .ping-dot { width: 10px; height: 10px; border-radius: 50%; display: inline-block; margin-right: 8px; flex-shrink: 0; }
            .ping-online { background: #22c55e; box-shadow: 0 0 8px rgba(34,197,94,0.5); animation: pulse-ring 2s infinite; }
            .ping-offline { background: #9ca3af; opacity: 0.5; }
            @keyframes pulse-ring { 
                0% { transform: scale(0.9); opacity: 1; } 
                50% { transform: scale(1.1); opacity: 0.7; } 
                100% { transform: scale(0.9); opacity: 1; } 
            }
            .theme-traveling { border-left-color: #3b82f6; }
            .theme-traveling .status-badge { background-color: #eff6ff; color: #1d4ed8; border: 1px solid #bfdbfe; }
            .theme-checked-in { border-left-color: #10b981; }
            .theme-checked-in .status-badge { background-color: #ecfdf5; color: #047857; border: 1px solid #a7f3d0; }
            .theme-offline { border-left-color: #94a3b8; background-color: #f8fafc; opacity: 0.85; }
            .theme-offline .status-badge { background-color: #f1f5f9; color: #475569; border: 1px solid #e2e8f0; }
            .theme-offline .rep-name { color: #475569; }

            .leaflet-marker-icon { transition: transform 0.8s linear !important; }
        </style>
    `);

    let latestSalesState = {};
    let renderedSalesState = {};
    let cardElementCache = {};
    let map = null;
    let sales_markers = {};
    let ws = null;
    let pingInterval = null;
    let renderLoopId = null;
    let staleCheckId = null;

    const FASTAPI_WS_URL = "wss://crystal-api.crystalapps.dev/telemetry/sales-ws";
    const TILE_SERVER_URL = "https://crystal-map.crystalapps.dev/styles/basic-preview/style.json";

    function startRenderLoop() {
        if (renderLoopId) clearInterval(renderLoopId);
        renderLoopId = setInterval(flushRenderQueue, 250);
    }

    function flushRenderQueue() {
        const currentEmails = Object.keys(latestSalesState);

        currentEmails.forEach(email => {
            const rep = latestSalesState[email];
            const prev = renderedSalesState[email];

            const statusChanged = !prev || prev.status !== rep.status;
            const customerChanged = !prev || prev.current_customer !== rep.current_customer;
            const speedChanged = !prev || Math.abs((prev.speed || 0) - (rep.speed || 0)) > 0.5;
            const positionChanged = !prev || prev.lat !== rep.lat || prev.lng !== rep.lng;
            const headingChanged = !prev || Math.abs((prev.heading || 0) - (rep.heading || 0)) > 2;
            const isNewRep = !prev;

            if (!isNewRep && !statusChanged && !customerChanged && !speedChanged && !positionChanged && !headingChanged) {
                return;
            }

            let $card = cardElementCache[email];
            if (!$card || $card.length === 0) {
                const safe_name = rep.full_name || email || "Unknown Rep";
                $card = create_card_html(email, safe_name);
                cardElementCache[email] = $card;
                $('#active-sales-container').append($card);
            }

            if ($card.parent().attr('id') !== 'active-sales-container') {
                $('#active-sales-container').append($card);
            }

            if (isNewRep || statusChanged) {
                $card.removeClass('theme-offline theme-traveling theme-checked-in');
                $card.addClass(rep.status === 'Checked-In' ? 'theme-checked-in' : 'theme-traveling');
                $card.find('.ping-dot').removeClass('ping-offline').addClass('ping-online');
                $card.find('.stat-text').text('Live Tracking').removeClass('text-muted').addClass('text-dark fw-bold');
                $card.find('.status-val').text(`● ${rep.status.toUpperCase()}`);
            }

            if (isNewRep || speedChanged) {
                const speedKmh = Math.round((rep.speed || 0) * 3.6);
                $card.find('.speed-val').text(`${speedKmh} km/h`);
            }

            if (isNewRep || customerChanged) {
                const customerDisplay = rep.current_customer && rep.current_customer !== 'None'
                    ? rep.current_customer
                    : 'In Transit';
                $card.find('.customer-val').text(customerDisplay);
            }

            if (rep.lat && rep.lng && (isNewRep || positionChanged || headingChanged || statusChanged)) {
                const color = rep.status === 'Checked-In' ? '#10b981' : '#3b82f6';
                const heading = rep.heading || 0;

                if (sales_markers[email]) {
                    sales_markers[email].setLatLng([rep.lat, rep.lng]);

                    if (isNewRep || statusChanged || headingChanged) {
                        const newIcon = build_marker_icon(color, heading);
                        sales_markers[email].setIcon(newIcon);
                    } else {
                        const iconEl = sales_markers[email].getElement();
                        if (iconEl) {
                            const arrow = iconEl.querySelector('.direction-ring');
                            if (arrow) arrow.style.transform = `rotate(${heading}deg)`;
                        }
                    }
                } else {
                    const icon = build_marker_icon(color, heading);
                    const safe_popup_name = rep.full_name || email || "Unknown";
                    const popupText = `<b>${safe_popup_name}</b><br><span class="text-muted small">${rep.status}</span>`;
                    sales_markers[email] = L.marker([rep.lat, rep.lng], { icon })
                        .addTo(map)
                        .bindPopup(`<div class="p-1">${popupText}</div>`);
                }
            }

            renderedSalesState[email] = { ...rep };
        });
    }

    function startStaleCheckLoop() {
        if (staleCheckId) clearInterval(staleCheckId);
        staleCheckId = setInterval(function() {
            const activeEmails = new Set(Object.keys(latestSalesState));

            Object.keys(cardElementCache).forEach(email => {
                if (!activeEmails.has(email)) {
                    const $card = cardElementCache[email];
                    if (!$card || $card.length === 0) return;

                    if ($card.parent().attr('id') !== 'standby-sales-container') {
                        $('#standby-sales-container').append($card);
                    }

                    $card.removeClass('theme-traveling theme-checked-in').addClass('theme-offline');
                    $card.find('.ping-dot').removeClass('ping-online').addClass('ping-offline');
                    $card.find('.stat-text').text('Offline').removeClass('text-dark fw-bold').addClass('text-muted');
                    $card.find('.speed-val').text('--');
                    $card.find('.status-val').text('● OFFLINE');
                    $card.find('.customer-val').text('None');

                    if (sales_markers[email]) {
                        map.removeLayer(sales_markers[email]);
                        delete sales_markers[email];
                    }

                    delete renderedSalesState[email];
                }
            });
        }, 5000);
    }

    function build_marker_icon(color, heading) {
        const htmlIcon = `
            <div style="position:relative;width:34px;height:34px;">
                <div class="direction-ring" style="position:absolute;top:0;left:0;width:100%;height:100%;transform:rotate(${heading}deg);transition:transform 0.5s linear;">
                    <svg width="34" height="34" viewBox="0 0 24 24" fill="none" stroke="${color}" stroke-width="2" style="opacity:0.6;">
                        <path d="M12 2L15 8L12 6L9 8L12 2Z" fill="${color}"/>
                    </svg>
                </div>
                <div style="position:absolute;top:4px;left:4px;background:#fff;border-radius:50%;width:26px;height:26px;box-shadow:0 2px 5px rgba(0,0,0,0.3);display:flex;align-items:center;justify-content:center;border:2px solid ${color};">
                    <svg width="16" height="16" viewBox="0 0 24 24" fill="${color}" xmlns="http://www.w3.org/2000/svg">
                        <path d="M12 12c2.21 0 4-1.79 4-4s-1.79-4-4-4-4 1.79-4 4 1.79 4 4 4zm0 2c-2.67 0-8 1.34-8 4v2h16v-2c0-2.66-5.33-4-8-4z"/>
                    </svg>
                </div>
            </div>`;
        return L.divIcon({ className: '', html: htmlIcon, iconSize: [34, 34], iconAnchor: [17, 17] });
    }

    function create_card_html(email, full_name) {
        const lower_name = full_name ? full_name.toLowerCase() : "";
        const display_email = email ? email.toLowerCase() : "";
        return $(`
            <div class="sales-card theme-offline" data-tid="${display_email}" data-name="${lower_name} ${display_email}">
                <div class="card-header-row">
                    <span class="rep-name">${full_name}</span>
                    <span class="speed-indicator speed-val">--</span>
                </div>
                <div style="font-size:11px;color:#64748b;margin-bottom:10px;">
                    <i class="fa fa-envelope-o me-1"></i> ${display_email}
                </div>
                <div class="status-badge status-val">● OFFLINE</div>
                <div class="card-meta">
                    <i class="fa fa-building card-meta-icon"></i>
                    <span class="customer-val text-truncate" style="max-width:180px;">None</span>
                </div>
                <div class="mt-2 d-flex align-items-center small rounded" style="background:rgba(0,0,0,0.03);padding:8px;">
                    <span class="ping-dot ping-offline"></span>
                    <span class="stat-text text-muted">Awaiting Connection...</span>
                </div>
            </div>
        `);
    }

    function refresh_sales_data() {
        frappe.call({
            method: "nexus_supply_chain.nexus_supply_chain.page.nexus_sales_dispatch.nexus_sales_dispatch.get_sales_team",
            callback: function(r) {
                const initial_team = r.message || [];
                render_baseline_team(initial_team);
            }
        });
    }

    function render_baseline_team(team) {
        $('#standby-sales-container').empty();
        $('#active-sales-container').empty();
        cardElementCache = {};
        renderedSalesState = {};

        if (team.length === 0) {
            $('#standby-sales-container').html(
                `<div class="text-center p-5 text-muted">No active Sales Personnel found in system.</div>`
            );
            return;
        }

        team.forEach(rep => {
            const email = (rep.email || "").toLowerCase();
            const safe_name = rep.full_name || email || "Unknown Rep";
            const $card = create_card_html(email, safe_name);
            cardElementCache[email] = $card;
            $('#standby-sales-container').append($card);
        });
    }

    function connectTelemetryWebSocket() {
        if (ws && ws.readyState === WebSocket.OPEN) return;

        ws = new WebSocket(FASTAPI_WS_URL);

        ws.onopen = () => {
            $('#conn-stat')
                .text('WS Live')
                .removeClass('text-danger border-danger')
                .addClass('text-success border-success');

            if (pingInterval) clearInterval(pingInterval);
            pingInterval = setInterval(() => {
                if (ws && ws.readyState === WebSocket.OPEN) {
                    ws.send(JSON.stringify({ action: "ping" }));
                }
            }, 30000);
        };

        ws.onmessage = (event) => {
            try {
                const data = JSON.parse(event.data);
                if (data.action === "pong") return;

                const raw_team = data.sales_team || {};
                
                const incoming = {};
                Object.keys(raw_team).forEach(k => {
                    incoming[k.toLowerCase()] = raw_team[k];
                });

                latestSalesState = incoming;

            } catch (e) {
                console.error("WS parse error:", e);
            }
        };

        ws.onclose = () => {
            $('#conn-stat')
                .text('Reconnecting...')
                .removeClass('text-success border-success')
                .addClass('text-danger border-danger');
            if (pingInterval) clearInterval(pingInterval);
            setTimeout(connectTelemetryWebSocket, 3000);
        };

        ws.onerror = () => { ws.close(); };
    }

    function applySearch(inputId, containerId) {
        $(inputId).on('keyup', function() {
            const val = $(this).val().toLowerCase();
            $(`${containerId} .sales-card`).each(function() {
                const name = $(this).attr('data-name');
                if (name) $(this).toggle(name.includes(val));
            });
        });
    }

    applySearch('#sales-search-active', '#active-sales-container');
    applySearch('#sales-search-standby', '#standby-sales-container');

    $(wrapper).on('click', '.sales-card', function() {
        const email = $(this).attr('data-tid');
        $('.sales-card').removeClass('active-selection');
        $(this).addClass('active-selection');

        if (sales_markers[email]) {
            map.flyTo(sales_markers[email].getLatLng(), 16, { duration: 1.2 });
            sales_markers[email].openPopup();
        } else {
            frappe.show_alert({ message: 'User is currently offline.', indicator: 'orange' });
        }
    });

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

        startRenderLoop();

        startStaleCheckLoop();
    });
};
