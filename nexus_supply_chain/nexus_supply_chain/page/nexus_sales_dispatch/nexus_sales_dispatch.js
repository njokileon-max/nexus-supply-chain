frappe.pages['nexus_sales_dispatch'].on_page_load = function(wrapper) {
    let page = frappe.ui.make_app_page({
        parent: wrapper,
        title: 'Nexus Sales Team Command Center',
        single_column: true 
    });

    page.add_inner_button('Pull Sales Attendance', function() {
    try {
        console.log("🔔 [Nexus Dispatch] Pull Sales Attendance clicked.");
        show_attendance_dialog();
    } catch (err) {
        console.error("❌ [Nexus Dispatch] Dialog failed to open:", err);
        frappe.msgprint({
            title: 'Error',
            indicator: 'red',
            message: 'Could not open Attendance dialog. Check browser console for details.'
        });
    }
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
            
            /* 🚨 ADDED: Animation and states */
            .ping-online { background: #22c55e; box-shadow: 0 0 8px rgba(34,197,94,0.5); animation: pulse-ring 2s infinite; }
            .ping-syncing { background: #f59e0b; box-shadow: 0 0 8px rgba(245,158,11,0.5); }
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
            
            /* 🚨 ADDED: Syncing/Degraded theme */
            .theme-syncing { border-left-color: #f59e0b; }
            .theme-syncing .status-badge { background-color: #fef3c7; color: #b45309; border: 1px solid #fde68a; }
            
            .theme-offline { border-left-color: #94a3b8; background-color: #f8fafc; opacity: 0.85; }
            .theme-offline .status-badge { background-color: #f1f5f9; color: #475569; border: 1px solid #e2e8f0; }
            .theme-offline .rep-name { color: #475569; }

            .leaflet-marker-icon { transition: transform 0.8s linear !important; }

            /* 🚨 FIX: Replaces Bootstrap's .btn-close (broken/missing icon in
               Frappe desk theme — no reliable SVG background asset) with a
               self-contained FontAwesome-based close button. Used both by
               the Route Summary panel's close (X) and available for any
               other overlay/card close action on this page. */
            .route-close-btn {
                background: #f1f5f9;
                border: 1px solid #e2e8f0;
                color: #64748b;
                width: 24px;
                height: 24px;
                min-width: 24px;
                border-radius: 50%;
                display: flex;
                align-items: center;
                justify-content: center;
                font-size: 12px;
                cursor: pointer;
                transition: background 0.15s ease, color 0.15s ease, border-color 0.15s ease;
                padding: 0;
                line-height: 1;
                flex-shrink: 0;
            }
            .route-close-btn:hover {
                background: #ef4444;
                color: #ffffff;
                border-color: #ef4444;
            }
            .route-close-btn:focus {
                outline: none;
                box-shadow: 0 0 0 2px rgba(239,68,68,0.25);
            }
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

    let suppressLiveMarkers = false;
    let routeSuppressTimeout = null;
    let routeLayerGroup = null;
    let routeAnimInterval = null;

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
            const wasStale = prev && prev.is_stale === true && rep.is_stale === false; // 🚨 Check if recovered from weak signal
            const isNewRep = !prev;

            if (!isNewRep && !statusChanged && !customerChanged && !speedChanged && !positionChanged && !headingChanged && !wasStale) {
                return;
            }

            if (rep.is_stale) return; 

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

            if (isNewRep || statusChanged || wasStale) {
                $card.removeClass('theme-offline theme-traveling theme-checked-in theme-syncing');
                $card.addClass(rep.status === 'Checked-In' ? 'theme-checked-in' : 'theme-traveling');
                $card.find('.ping-dot').removeClass('ping-offline ping-syncing').addClass('ping-online');
                $card.find('.stat-text').text('Live Tracking').removeClass('text-muted text-warning').addClass('text-dark fw-bold');
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

            if (!suppressLiveMarkers && rep.lat && rep.lng && (isNewRep || positionChanged || headingChanged || statusChanged || wasStale)) {
                const color = rep.status === 'Checked-In' ? '#10b981' : '#3b82f6';
                const heading = rep.heading || 0;

                if (sales_markers[email]) {
                    sales_markers[email].setLatLng([rep.lat, rep.lng]);

                    if (isNewRep || statusChanged || headingChanged || wasStale) {
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

    function handleRepLogout(email) {
        const $card = cardElementCache[email];
        if ($card && $card.length > 0) {
            if ($card.parent().attr('id') !== 'standby-sales-container') {
                $('#standby-sales-container').append($card);
            }

            $card.removeClass('theme-traveling theme-checked-in theme-syncing').addClass('theme-offline');
            $card.find('.ping-dot').removeClass('ping-online ping-syncing').addClass('ping-offline');
            $card.find('.stat-text').text('Offline').removeClass('text-dark fw-bold text-warning').addClass('text-muted');
            $card.find('.speed-val').text('--');
            $card.find('.status-val').text('● OFFLINE');
            $card.find('.customer-val').text('None');
        }

        if (sales_markers[email]) {
            map.removeLayer(sales_markers[email]);
            delete sales_markers[email];
        }

        delete latestSalesState[email];
        delete renderedSalesState[email];
    }

    function startStaleCheckLoop() {
        if (staleCheckId) clearInterval(staleCheckId);

        const WEAK_SIGNAL_THRESHOLD_MS = 150000; // 2.5 minutes

        staleCheckId = setInterval(function() {
            const now = Date.now();

            Object.keys(latestSalesState).forEach(email => {
                const rep = latestSalesState[email];
                if (!rep || !rep.last_updated) return;

                // Server sends last_updated as epoch SECONDS (time.time()).
                const lastUpdatedMs = rep.last_updated * 1000;
                const elapsed = now - lastUpdatedMs;
                const shouldBeStale = elapsed > WEAK_SIGNAL_THRESHOLD_MS;

                if (shouldBeStale && !rep.is_stale) {
                    rep.is_stale = true;
                    if (renderedSalesState[email]) renderedSalesState[email].is_stale = true;

                    const $card = cardElementCache[email];
                    if ($card && $card.length > 0) {
                        $card.removeClass('theme-traveling theme-checked-in theme-offline').addClass('theme-syncing');
                        $card.find('.ping-dot').removeClass('ping-online ping-offline').addClass('ping-syncing');
                        $card.find('.stat-text').text('Weak Signal...').removeClass('text-dark fw-bold text-muted').addClass('text-warning fw-bold');
                        $card.find('.status-val').text('● WEAK SIGNAL');
                    }

                    if (sales_markers[email]) {
                        const heading = rep.heading || 0;
                        sales_markers[email].setIcon(build_marker_icon('#f59e0b', heading));
                    }

                } else if (!shouldBeStale && rep.is_stale) {
                    rep.is_stale = false;
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
                <button type="button" class="btn btn-outline-primary btn-sm view-route-btn w-100 mt-2">
                    <i class="fa fa-route me-1"></i> View Route
                </button>
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
            console.log("✅ [Nexus Dispatch] WebSocket Connected Successfully.");
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
                const incoming_emails = new Set(Object.keys(raw_team).map(k => k.toLowerCase()));

                Object.keys(latestSalesState).forEach(email => {
                    if (!incoming_emails.has(email)) {
                        handleRepLogout(email);
                    }
                });

                const now = Date.now();

                Object.keys(raw_team).forEach(k => {
                    const email = k.toLowerCase();
                    const incoming_rep = raw_team[k];

                    latestSalesState[email] = {
                        ...latestSalesState[email],  // Retain existing state (incl. is_stale)
                        ...incoming_rep,              // Overwrite with new payload (incl. fresh last_updated)
                        last_ping_ms: now             // Kept for diagnostics only — no longer drives staleness
                    };
                });

            } catch (e) {
                console.error("WS parse error:", e);
            }
        };

        ws.onclose = () => {
            console.warn("⚠️ [Nexus Dispatch] WebSocket Disconnected.");
            $('#conn-stat')
                .text('Reconnecting...')
                .removeClass('text-success border-success')
                .addClass('text-danger border-danger');
            if (pingInterval) clearInterval(pingInterval);
            setTimeout(connectTelemetryWebSocket, 3000);
        };

        ws.onerror = (e) => { 
            console.error("❌ [Nexus Dispatch] WebSocket Error:", e);
            ws.close(); 
        };
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

    $(wrapper).on('click', '.view-route-btn', function(e) {
        e.stopPropagation();
        const $card = $(this).closest('.sales-card');
        const email = $card.attr('data-tid');
        const repName = $card.find('.rep-name').text() || email;
        show_route_dialog(email, repName);
    });

    $(wrapper).on('click', '.leaflet-popup-close-button', function(e) {
        e.preventDefault();
        e.stopPropagation();
    });

    function show_attendance_dialog() {
        let d = new frappe.ui.Dialog({
            title: 'Pull Sales Attendance',
            size: 'extra-large', // 🚨 FIX: Expanded modal size to prevent squeezing
            fields: [
                { 
                    fieldtype: 'Select', 
                    fieldname: 'date_filter', 
                    label: 'Date Range', 
                    options: ['Today', 'Yesterday', 'Custom Range'], 
                    default: 'Today', 
                    reqd: 1
                },
                { fieldtype: 'Column Break' },
                { 
                    fieldtype: 'Link', 
                    fieldname: 'sales_person', 
                    label: 'Sales Person', 
                    options: 'Sales Person', 
                    description: 'Leave empty to pull data for all active reps.' 
                },
                { 
                    fieldtype: 'Section Break', 
                    fieldname: 'custom_dates_sec', 
                    depends_on: 'eval:doc.date_filter === "Custom Range"' 
                },
                { 
                    fieldtype: 'Date', 
                    fieldname: 'start_date', 
                    label: 'Start Date', 
                    depends_on: 'eval:doc.date_filter === "Custom Range"',
                    mandatory_depends_on: 'eval:doc.date_filter === "Custom Range"'
                },
                { 
                    fieldtype: 'Column Break', 
                    depends_on: 'eval:doc.date_filter === "Custom Range"' 
                },
                { 
                    fieldtype: 'Date', 
                    fieldname: 'end_date', 
                    label: 'End Date', 
                    depends_on: 'eval:doc.date_filter === "Custom Range"',
                    mandatory_depends_on: 'eval:doc.date_filter === "Custom Range"'
                },
                { fieldtype: 'Section Break' },
                { fieldtype: 'HTML', fieldname: 'results_html' }
            ],
            primary_action_label: 'Pull Data',
            primary_action(values) {
                if (values.date_filter === 'Custom Range') {
                    if (values.start_date > values.end_date) {
                        frappe.msgprint({title: 'Validation Error', indicator: 'red', message: 'Start Date cannot be greater than End Date.'});
                        return;
                    }
                }

                d.get_field('results_html').$wrapper.html('<div class="text-center p-5"><i class="fa fa-spinner fa-spin fa-2x text-primary"></i><br><span class="text-muted mt-2 d-inline-block">Extracting Visit Data...</span></div>');
                
                frappe.call({
                    method: 'nexus_supply_chain.nexus_supply_chain.page.nexus_sales_dispatch.nexus_sales_dispatch.get_sales_attendance',
                    args: {
                        date_filter: values.date_filter,
                        start_date: values.start_date,
                        end_date: values.end_date,
                        sales_person: values.sales_person
                    },
                    callback: function(r) {
                        if (!r.exc && r.message) {
                            d.attendance_data = r.message;
                            render_attendance_table(r.message, d);
                        } else {
                            render_attendance_table([], d);
                        }
                    },
                    error: function(err) {
                        d.get_field('results_html').$wrapper.html('<div class="alert alert-danger text-center mt-3">Connection error. Could not extract records.</div>');
                    }
                });
            }
        });

        d.show();

        let $export_btn = $(`<button type="button" class="btn btn-default btn-sm">Export to Excel</button>`);
        $export_btn.on('click', function() {
            if (!d.attendance_data || d.attendance_data.length === 0) {
                frappe.msgprint('No attendance data available to export. Please pull data first.');
                return;
            }
            export_attendance_excel(d.attendance_data);
        });
        d.$wrapper.find('.modal-footer').prepend($export_btn);
    }

function render_attendance_table(data, d) {
        if (!data || data.length === 0) {
            d.get_field('results_html').$wrapper.html(
                '<div class="alert alert-warning text-center mt-3 mb-0" style="font-size: 14px;">No attendance or visit records found for the selected criteria.</div>'
            );
            return;
        }

        let grand_visits            = 0;
        let grand_onsite            = 0;
        let grand_offsite           = 0;
        let grand_orders            = 0;
        let grand_order_val         = 0;
        let grand_confirmed_orders  = 0;
        let grand_confirmed_val     = 0;
        let grand_invoices          = 0;
        let grand_invoiced          = 0;
        let grand_returns           = 0;
        let grand_returned          = 0;
        let grand_distance          = 0;

        data.forEach(row => {
            grand_visits           += parseInt(row.total_visits || 0);
            grand_onsite           += parseInt(row.onsite_visits || 0);
            grand_offsite          += parseInt(row.offsite_visits || 0);
            grand_orders           += parseInt(row.total_orders || 0);
            grand_order_val        += parseFloat(row.total_order_value || 0);
            grand_confirmed_orders += parseInt(row.total_confirmed_orders || 0);
            grand_confirmed_val    += parseFloat(row.total_confirmed_value || 0);
            grand_invoices         += parseInt(row.total_invoices || 0);
            grand_invoiced         += parseFloat(row.invoiced_amount || 0);
            grand_returns           += parseInt(row.total_returns || 0);
            grand_returned          += parseFloat(row.returned_amount || 0);
            grand_distance          += parseFloat(row.distance_recorded_km || 0);
        });

        const fmt_currency = (val) => {
            return 'Sh ' + parseFloat(val || 0).toLocaleString('en-KE', {
                minimumFractionDigits: 2,
                maximumFractionDigits: 2
            });
        };

        const fmt_time_only = (time_str) => {
            if (!time_str) return '—';
            // time_str comes in as HH:MM:SS from SQL TIME()
            const parts = String(time_str).split(':');
            if (parts.length < 2) return time_str;
            let hh = parseInt(parts[0], 10);
            const mm = parts[1];
            const ampm = hh >= 12 ? 'PM' : 'AM';
            hh = hh % 12;
            if (hh === 0) hh = 12;
            return `${hh}:${mm} ${ampm}`;
        };

        let html = `
            <div class="table-responsive border rounded" style="max-height: 500px; overflow-y: auto;">
                <table class="table table-bordered table-hover m-0" style="font-size: 14px; background: #fff; white-space: nowrap;">
                    <thead class="table-light position-sticky top-0" style="z-index: 10; border-bottom: 2px solid #e2e8f0;">
                        <tr>
                            <th class="p-3 text-uppercase text-muted fw-bold align-middle" style="font-size: 12px; min-width:160px;">Sales Person</th>
                            <th class="p-3 text-uppercase text-muted align-middle" style="font-size: 12px;">Date</th>
                            <th class="p-3 text-uppercase text-muted text-center fw-bold align-middle" style="font-size: 12px;">Total Visits</th>
                            <th class="p-3 text-uppercase text-muted text-center fw-bold align-middle" style="font-size: 12px;">On-Site Visits</th>
                            <th class="p-3 text-uppercase text-muted text-center fw-bold align-middle" style="font-size: 12px;">Off-Site Visits</th>
                            <th class="p-3 text-uppercase text-muted text-center fw-bold align-middle" style="font-size: 12px;">Total Orders</th>
                            <th class="p-3 text-uppercase text-muted text-end fw-bold align-middle" style="font-size: 12px;">Total Order Value</th>
                            <th class="p-3 text-uppercase text-muted text-center fw-bold align-middle" style="font-size: 12px;">Confirmed Orders</th>
                            <th class="p-3 text-uppercase text-muted text-end fw-bold align-middle" style="font-size: 12px;">Confirmed Value</th>
                            <th class="p-3 text-uppercase text-muted text-center fw-bold align-middle" style="font-size: 12px;">Invoices</th>
                            <th class="p-3 text-uppercase text-muted text-end fw-bold align-middle" style="font-size: 12px;">Invoiced Value</th>
                            <th class="p-3 text-uppercase text-muted text-center fw-bold align-middle" style="font-size: 12px;">Returns</th>
                            <th class="p-3 text-uppercase text-muted text-end fw-bold align-middle" style="font-size: 12px;">Returned Value</th>
                            <th class="p-3 text-uppercase text-muted align-middle" style="font-size: 12px;">First Check-In</th>
                            <th class="p-3 text-uppercase text-muted align-middle" style="font-size: 12px;">Last Check-In</th>
                            <th class="p-3 text-uppercase text-muted text-end fw-bold align-middle" style="font-size: 12px;">Distance Recorded</th>
                        </tr>
                    </thead>
                    <tbody>
        `;

        data.forEach(row => {
            const name              = row.sales_person_name || row.email || 'Unknown';
            const period_date       = row.period_start_date === row.period_end_date
                                        ? frappe.datetime.str_to_user(row.period_start_date)
                                        : `${frappe.datetime.str_to_user(row.period_start_date)} → ${frappe.datetime.str_to_user(row.period_end_date)}`;

            const f_visit_time      = fmt_time_only(row.first_visit_time);
            const l_visit_time      = fmt_time_only(row.last_visit_time);

            const visits            = parseInt(row.total_visits || 0);
            const onsite             = parseInt(row.onsite_visits || 0);
            const offsite            = parseInt(row.offsite_visits || 0);
            const orders             = parseInt(row.total_orders || 0);
            const order_val          = parseFloat(row.total_order_value || 0);
            const confirmed_orders   = parseInt(row.total_confirmed_orders || 0);
            const confirmed_val      = parseFloat(row.total_confirmed_value || 0);
            const invoices           = parseInt(row.total_invoices || 0);
            const invoiced           = parseFloat(row.invoiced_amount || 0);
            const returns            = parseInt(row.total_returns || 0);
            const returned           = parseFloat(row.returned_amount || 0);
            const distance_km        = parseFloat(row.distance_recorded_km || 0);

            html += `
                <tr>
                    <td class="p-3 text-dark align-middle">${name}</td>
                    <td class="p-3 align-middle text-dark" style="font-size: 13px;">${period_date}</td>
                    <td class="p-3 text-center align-middle text-dark" style="font-size: 14px;">${visits}</td>
                    <td class="p-3 text-center align-middle text-dark" style="font-size: 14px;">${onsite}</td>
                    <td class="p-3 text-center align-middle text-dark" style="font-size: 14px;">${offsite}</td>
                    <td class="p-3 text-center align-middle text-dark" style="font-size: 14px;">${orders}</td>
                    <td class="p-3 text-end align-middle text-dark fw-semibold" style="font-size: 14px;">${fmt_currency(order_val)}</td>
                    <td class="p-3 text-center align-middle text-dark" style="font-size: 14px;">${confirmed_orders}</td>
                    <td class="p-3 text-end align-middle text-dark fw-semibold" style="font-size: 14px;">${fmt_currency(confirmed_val)}</td>
                    <td class="p-3 text-center align-middle text-dark" style="font-size: 14px;">${invoices}</td>
                    <td class="p-3 text-end align-middle text-dark fw-semibold" style="font-size: 14px;">${fmt_currency(invoiced)}</td>
                    <td class="p-3 text-center align-middle text-dark" style="font-size: 14px;">${returns}</td>
                    <td class="p-3 text-end align-middle text-dark fw-semibold" style="font-size: 14px;">${fmt_currency(returned)}</td>
                    <td class="p-3 align-middle text-dark" style="font-size: 13px;">${f_visit_time}</td>
                    <td class="p-3 align-middle text-dark" style="font-size: 13px;">${l_visit_time}</td>
                    <td class="p-3 text-end align-middle text-dark fw-semibold" style="font-size: 14px;">${distance_km.toFixed(2)} km</td>
                </tr>
            `;
        });

        html += `
                    </tbody>
                    <tfoot style="border-top: 3px solid #cbd5e1; background-color: #fef9c3;">
                        <tr>
                            <td class="p-3 fw-semibold text-dark align-middle" style="font-size: 12px; text-transform: uppercase;">
                                TOTALS
                            </td>
                            <td class="p-3 align-middle"></td>
                            <td class="p-3 text-center text-dark fw-semibold align-middle" style="font-size: 15px;">${grand_visits}</td>
                            <td class="p-3 text-center text-dark fw-semibold align-middle" style="font-size: 15px;">${grand_onsite}</td>
                            <td class="p-3 text-center text-dark fw-semibold align-middle" style="font-size: 15px;">${grand_offsite}</td>
                            <td class="p-3 text-center text-dark fw-semibold align-middle" style="font-size: 15px;">${grand_orders}</td>
                            <td class="p-3 text-end text-dark fw-semibold align-middle" style="font-size: 15px;">${fmt_currency(grand_order_val)}</td>
                            <td class="p-3 text-center text-dark fw-semibold align-middle" style="font-size: 15px;">${grand_confirmed_orders}</td>
                            <td class="p-3 text-end text-dark fw-semibold align-middle" style="font-size: 15px;">${fmt_currency(grand_confirmed_val)}</td>
                            <td class="p-3 text-center text-dark fw-semibold align-middle" style="font-size: 15px;">${grand_invoices}</td>
                            <td class="p-3 text-end text-dark fw-semibold align-middle" style="font-size: 15px;">${fmt_currency(grand_invoiced)}</td>
                            <td class="p-3 text-center text-dark fw-semibold align-middle" style="font-size: 15px;">${grand_returns}</td>
                            <td class="p-3 text-end text-dark fw-semibold align-middle" style="font-size: 15px;">${fmt_currency(grand_returned)}</td>
                            <td colspan="2" class="p-3 text-muted fw-semibold align-middle" style="font-size: 12px; font-style: italic;">
                                <i class="fa fa-info-circle me-1"></i> Aggregated period totals
                            </td>
                            <td class="p-3 text-end text-dark fw-semibold align-middle" style="font-size: 15px;">${grand_distance.toFixed(2)} km</td>
                        </tr>
                    </tfoot>
                </table>
            </div>
        `;

        d.get_field('results_html').$wrapper.html(html);
    }

    function export_attendance_excel(data) {
        let csv = 'Sales Person Name,Email,Date,Total Visits,On-Site Visits,Off-Site Visits,Total Orders,Total Order Value (Sh),Confirmed Orders,Confirmed Order Value (Sh),Total Invoices,Invoiced Amount (Sh),Total Returns,Returned Amount (Sh),First Check-In,Last Check-In,Distance Recorded (km)\n';

        const fmt_time_only_csv = (time_str) => {
            if (!time_str) return '';
            const parts = String(time_str).split(':');
            if (parts.length < 2) return time_str;
            let hh = parseInt(parts[0], 10);
            const mm = parts[1];
            const ampm = hh >= 12 ? 'PM' : 'AM';
            hh = hh % 12;
            if (hh === 0) hh = 12;
            return `${hh}:${mm} ${ampm}`;
        };

        data.forEach(row => {
            let name              = row.sales_person_name || 'Unknown';
            let email             = row.email              || '';
            let period_date       = row.period_start_date === row.period_end_date
                                        ? (row.period_start_date || '')
                                        : `${row.period_start_date || ''} to ${row.period_end_date || ''}`;

            let visits            = row.total_visits            || 0;
            let onsite            = row.onsite_visits           || 0;
            let offsite           = row.offsite_visits          || 0;
            let orders            = row.total_orders            || 0;
            let order_val         = parseFloat(row.total_order_value || 0).toFixed(2);
            let confirmed_orders  = row.total_confirmed_orders  || 0;
            let confirmed_val     = parseFloat(row.total_confirmed_value || 0).toFixed(2);
            let invoices          = row.total_invoices          || 0;
            let invoiced          = parseFloat(row.invoiced_amount || 0).toFixed(2);
            let returns           = row.total_returns           || 0;
            let returned          = parseFloat(row.returned_amount || 0).toFixed(2);
            let f_visit_time      = fmt_time_only_csv(row.first_visit_time);
            let l_visit_time      = fmt_time_only_csv(row.last_visit_time);
            let distance_km       = parseFloat(row.distance_recorded_km || 0).toFixed(2);

            csv += `"${name}","${email}","${period_date}",${visits},${onsite},${offsite},${orders},${order_val},${confirmed_orders},${confirmed_val},${invoices},${invoiced},${returns},${returned},"${f_visit_time}","${l_visit_time}",${distance_km}\n`;
        });

        let blob = new Blob([csv], { type: 'text/csv;charset=utf-8;' });
        let url  = URL.createObjectURL(blob);
        let link = document.createElement("a");
        link.setAttribute("href", url);
        link.setAttribute("download", `Sales_Attendance_Export_${frappe.datetime.nowdate()}.csv`);
        document.body.appendChild(link);
        link.click();
        document.body.removeChild(link);
    }

    function show_route_dialog(email, repName) {
        let d = new frappe.ui.Dialog({
            title: `View Route — ${repName}`,
            fields: [
                {
                    fieldtype: 'Date',
                    fieldname: 'route_date',
                    label: 'Date',
                    default: frappe.datetime.nowdate(),
                    reqd: 1
                }
            ],
            primary_action_label: 'View Route',
            primary_action(values) {
                d.hide();
                toggleRouteView(email, values.route_date, repName);
            }
        });
        d.show();
    }

    function toggleRouteView(email, route_date, repName) {

        suppressLiveMarkers = true;
        if (routeSuppressTimeout) clearTimeout(routeSuppressTimeout);
        routeSuppressTimeout = setTimeout(() => {
            suppressLiveMarkers = false;
        }, 10000);

        frappe.show_alert({ message: `Loading route for ${repName} on ${route_date}...`, indicator: 'blue' });

        frappe.call({
            method: "nexus_supply_chain.nexus_supply_chain.page.nexus_sales_dispatch.nexus_sales_dispatch.get_sales_person_route",
            args: { sales_person_email: email, route_date: route_date },
            callback: function(r) {
                if (!r.exc && r.message && r.message.status === 'success') {
                    render_route_overlay(r.message, repName, route_date);
                } else {
                    frappe.msgprint({ title: 'No Route Data', indicator: 'orange', message: 'No check-in records found for this rep on the selected date.' });
                }
            }
        });
    }

    function strip_microseconds(ts) {
        if (!ts) return null;
        return String(ts).split('.')[0];
    }

    function render_route_overlay(data, repName, route_date) {
        if (routeLayerGroup) {
            map.removeLayer(routeLayerGroup);
            routeLayerGroup = null;
        }
        if (routeAnimInterval) {
            clearInterval(routeAnimInterval);
            routeAnimInterval = null;
        }
        $('#route-summary-panel').remove();

        const checkpoints = data.checkpoints || [];
        if (checkpoints.length === 0) {
            frappe.msgprint({ title: 'No Route Data', indicator: 'orange', message: 'No check-in records found for this rep on the selected date.' });
            return;
        }

        routeLayerGroup = L.layerGroup().addTo(map);

        const latlngs = checkpoints.map(cp => [cp.lat, cp.lng]);
        if (data.route_geometry) {
            L.geoJSON(data.route_geometry, {
                style: { color: '#3b82f6', weight: 5, opacity: 0.85, lineJoin: 'round' }
            }).addTo(routeLayerGroup);
        } else if (latlngs.length > 1) {
            const routeLine = L.polyline(latlngs, {
                color: '#3b82f6',
                weight: 4,
                opacity: 0.85,
                dashArray: '10, 8',
                lineJoin: 'round'
            }).addTo(routeLayerGroup);

            let dashOffset = 0;
            routeAnimInterval = setInterval(() => {
                dashOffset = (dashOffset - 1) % 18;
                const el = routeLine.getElement();
                if (el) el.style.strokeDashoffset = dashOffset;
            }, 60);
        }

        let totalOrderValue = 0;
        checkpoints.forEach((cp, idx) => {
            totalOrderValue += (cp.order_value || 0);

            const pinHtml = `
                <div style="position:relative;width:28px;height:40px;">
                    <svg width="28" height="40" viewBox="0 0 28 40" xmlns="http://www.w3.org/2000/svg">
                        <path d="M14 0C6.3 0 0 6.3 0 14c0 10.5 14 26 14 26s14-15.5 14-26c0-7.7-6.3-14-14-14z" fill="#EA4335"/>
                        <circle cx="14" cy="14" r="6" fill="#ffffff"/>
                    </svg>
                    <div style="position:absolute;top:6px;left:0;width:28px;text-align:center;font-size:10px;font-weight:800;color:#7f1d1d;">${idx + 1}</div>
                </div>`;
            const pinIcon = L.divIcon({ className: '', html: pinHtml, iconSize: [28, 40], iconAnchor: [14, 40] });

            const orderValStr = cp.order_value > 0
                ? `Sh ${cp.order_value.toLocaleString('en-KE', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`
                : 'No orders';

            const checkInStr = strip_microseconds(cp.check_in_time) || '—';
            const checkOutStr = strip_microseconds(cp.check_out_time) || 'Still open';

            L.marker([cp.lat, cp.lng], { icon: pinIcon })
                .addTo(routeLayerGroup)
                .bindPopup(`
                    <div class="p-1" style="font-size:12px; line-height:1.6;">
                        <div style="margin-bottom:8px; font-size:13px;"><b>Stop ${idx + 1}: ${cp.customer_name}</b></div>
                        <div class="text-muted" style="margin-bottom:5px;">Check-In: ${checkInStr}</div>
                        <div class="text-muted" style="margin-bottom:8px;">Check-Out: ${checkOutStr}</div>
                        <div><b>Order Value: ${orderValStr}</b></div>
                    </div>
                `, { minWidth: 230 });
        });

        map.fitBounds(L.latLngBounds(latlngs), { padding: [40, 40] });

        const $panel = $(`
            <div id="route-summary-panel" class="position-absolute p-3 bg-white shadow rounded border"
                 style="z-index:999; bottom:16px; left:16px; font-size:12px; min-width:220px; border-color:#e5e7eb !important;">
                <div class="d-flex justify-content-between align-items-center mb-2">
                    <span class="fw-bold text-dark">${repName} — ${route_date}</span>
                    <button type="button" class="route-close-btn" id="close-route-view" title="Close">
                        <i class="fa fa-times"></i>
                    </button>
                </div>
                <div class="mb-1"><i class="fa fa-route text-primary me-1"></i> Distance Traveled: <b>${data.distance_source === 'ors' ? (data.total_km || 0).toFixed(2) + ' km' : '<span class="text-danger">Unavailable</span>'}</b></div>
                <div class="mb-1"><i class="fa fa-map-marker-alt text-danger me-1"></i> Checkpoints: <b>${checkpoints.length}</b></div>
                <div><i class="fa fa-coins text-success me-1"></i> Total Order Value: <b>Sh ${totalOrderValue.toLocaleString('en-KE', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}</b></div>
            </div>
        `);
        $('#fleet-map').parent().append($panel);

        $('#close-route-view').on('click', function() {
            if (routeLayerGroup) {
                map.removeLayer(routeLayerGroup);
                routeLayerGroup = null;
            }
            if (routeAnimInterval) {
                clearInterval(routeAnimInterval);
                routeAnimInterval = null;
            }
            $('#route-summary-panel').remove();
        });
    }

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

        setTimeout(() => {
            map.invalidateSize();
            console.log("🗺️ [Nexus Dispatch] Map rendered and size invalidated.");
        }, 500);

        refresh_sales_data();
        connectTelemetryWebSocket();
        startRenderLoop();
        startStaleCheckLoop();
    });
};
