// apps/nexus_supply_chain/nexus_supply_chain/page/nexus_load_optimizer/nexus_load_optimizer.js

frappe.pages['nexus_load_optimizer'].on_page_load = function(wrapper) {
    let page = frappe.ui.make_app_page({
        parent: wrapper,
        title: 'NEXUS Load Optimizer',
        single_column: true
    });

    let page_fields = {
        company: page.add_field({label: 'Company', fieldtype: 'Link', options: 'Company', default: frappe.defaults.get_user_default("Company")}),
        customer: page.add_field({label: 'Customer', fieldtype: 'Link', options: 'Customer'}),
        sales_order_status: page.add_field({label: 'Sales Order Status', fieldtype: 'Select', options: '\nDraft\nTo Deliver\nTo Deliver and Bill', default: 'To Deliver and Bill'}),
        territory: page.add_field({label: 'Territory', fieldtype: 'Link', options: 'Territory'}),
        delivery_region: page.add_field({label: 'Delivery Region', fieldtype: 'Link', options: 'Delivery Region', reqd: 1}),
        vehicle_type: page.add_field({label: 'Vehicle Type', fieldtype: 'Link', options: 'Vehicle Type', reqd: 1}),
        transport_mode: page.add_field({label: 'Transport Mode', fieldtype: 'Select', options: '\nCompany Truck\nOn-Collection', default: 'Company Truck'}),
        from_date: page.add_field({label: 'From Date', fieldtype: 'Date'}),
        to_date: page.add_field({label: 'To Date', fieldtype: 'Date'})
    };

    page_fields.transport_mode.df.onchange = () => {
        const mode = page_fields.transport_mode.get_value();
        if (mode === 'On-Collection') {
            page_fields.vehicle_type.set_value('Customer Vehicle');
            page_fields.vehicle_type.df.read_only = 1;
        } else {
            page_fields.vehicle_type.set_value('');
            page_fields.vehicle_type.df.read_only = 0;
        }
        page_fields.vehicle_type.refresh();
    };

    let current_data = null;
    let route_map = null;
    let map_layers = [];
    let captured_route_data = {}; 
    
    // 🚨 0-Lag State Management for Margin Analytics
    let active_margin_data = null;
    let is_car_hire_mode = false;
    
    // 🚨 FLEET TELEMETRY STATE
    const FASTAPI_WS_URL = "wss://crystal-api.crystalapps.dev/telemetry/ws";
    const FASTAPI_URL = "https://crystal-api.crystalapps.dev";
    let fleetWS = null;
    let fleetPingInterval = null;
    let LIVE_FLEET_STATE = {};
    let fleet_markers = {};

    frappe.require([
        "/assets/nexus_supply_chain/leaflet/leaflet.css",
        "/assets/nexus_supply_chain/leaflet/leaflet.js",
        "https://cdn.jsdelivr.net/npm/sortablejs@1.15.0/Sortable.min.js" 
    ], function() {
        // Initialize the WebSocket engine as soon as the dependencies are loaded
        connectFleetWebSocket();
    });

    $('head').append(`
        <style>
            .nexus-route-panel {
                position: fixed; top: 0; right: -60vw; width: 55vw; height: 100vh;
                background-color: #ffffff; box-shadow: -10px 0 30px rgba(0,0,0,0.2);
                z-index: 9999; transition: right 0.35s cubic-bezier(0.4, 0, 0.2, 1);
                display: flex; flex-direction: column;
            }
            .nexus-route-panel.open { right: 0; }
            .nexus-panel-backdrop {
                position: fixed; top: 0; left: 0; width: 100vw; height: 100vh;
                background: rgba(15, 23, 42, 0.6); z-index: 9998; display: none; backdrop-filter: blur(3px);
            }
            .nexus-map-marker {
                background-color: #1e3a8a; color: white; border-radius: 50%; width: 28px; height: 28px;
                display: flex; align-items: center; justify-content: center; border: 2px solid white;
                box-shadow: 0 2px 6px rgba(0,0,0,0.4); font-size: 13px; font-weight: bold; pointer-events: auto !important;
            }
            .nexus-factory-marker {
                background-color: #0f172a; color: #fbbf24; border-radius: 6px; width: 36px; height: 36px;
                display: flex; align-items: center; justify-content: center; border: 2px solid white;
                box-shadow: 0 4px 10px rgba(0,0,0,0.5); font-size: 18px;
            }
            .waittime-badge { font-family: monospace; font-weight: bold; color: #b45309; background: #fffbeb; padding: 2px 6px; border-radius: 4px; border: 1px solid #fef3c7; }
            .gps-indicator { font-size: 10px; padding: 2px 6px; border-radius: 10px; text-transform: uppercase; font-weight: bold; }
            .gps-mapped { background: #dcfce7; color: #166534; }
            .gps-missing { background: #f1f5f9; color: #64748b; }
            
            .load-group-card { transition: border 0.3s, background-color 0.3s; border: 2px solid transparent !important; }
            .load-group-card.stale-map { border: 2px solid #fbbf24 !important; } 
            .load-group-card.overload { border: 2px solid #ef4444 !important; background: #fef2f2; }
            .order-row { cursor: grab; background: white; transition: background 0.2s; }
            .order-row:active { cursor: grabbing; }
            .order-row:hover { background: #f8fafc; }
            .ghost-class { opacity: 0.4; background: #dbeafe; }
            .update-map-btn { display: none; }
            .stale-map .update-map-btn { display: inline-block !important; }
            .stale-map .view-route-btn { display: none; }
            .overload-msg { display: none; color: #ef4444; font-weight: bold; font-size: 11px; margin-right: 15px; }
            .overload .overload-msg { display: inline-block; }

            /* Margin Analysis Styles */
            .nexus-margin-panel {
                position: fixed; top: 0; left: -60vw; width: 60vw; height: 100vh;
                background-color: #f8fafc; box-shadow: 10px 0 30px rgba(0,0,0,0.2);
                z-index: 9999; transition: left 0.35s cubic-bezier(0.4, 0, 0.2, 1);
                display: flex; flex-direction: column;
            }
            .nexus-margin-panel.open { left: 0; }
            .nexus-margin-backdrop {
                position: fixed; top: 0; left: 0; width: 100vw; height: 100vh;
                background: rgba(15, 23, 42, 0.6); z-index: 9998; display: none; backdrop-filter: blur(3px);
            }
            .margin-value { font-size: 1.50rem; font-weight: bold; }
            .profit-positive { color: #16a34a; }
            .profit-negative { color: #ef4444; }
            .tracking-wider { letter-spacing: 0.05em; }

            input[type=number]::-webkit-inner-spin-button, 
            input[type=number]::-webkit-outer-spin-button { 
                -webkit-appearance: none; margin: 0; 
            }
            input[type=number] { -moz-appearance: textfield; }

            /* 🚨 SMOOTH MARKER ANIMATION FOR LIVE FLEET */
            .leaflet-marker-icon { transition: transform 0.8s linear !important; }
        </style>
    `);

    if ($('#nexusRoutePanel').length === 0) {
        $('body').append(`
            <div class="nexus-panel-backdrop" id="nexusRouteBackdrop"></div>
            <div class="nexus-route-panel" id="nexusRoutePanel">
                <div class="p-4 bg-light border-bottom d-flex justify-content-between align-items-center">
                    <div class="d-flex align-items-center">
                        <h4 class="mb-0 fw-bold text-primary"><i class="fa fa-route me-2"></i> Sequence-Optimized Route</h4>
                        <span class="badge bg-success-subtle text-success border border-success ms-3" id="fleet-conn-stat" style="font-size: 10px;"><i class="fa fa-circle-o-notch fa-spin me-1"></i> Fleet WS</span>
                    </div>
                    <button class="btn btn-outline-secondary btn-sm rounded-circle" id="closeRoutePanel" style="width: 36px; height: 36px;"><i class="fa fa-times"></i></button>
                </div>
                <div class="flex-grow-1" id="offcanvas-map-container" style="background: #e2e8f0;"></div>
                <div class="p-4 bg-white border-top text-end shadow-sm"><span id="route-distance-label" class="fw-bold text-dark fs-5"></span></div>
            </div>
        `);
    }

    if ($('#nexusMarginPanel').length === 0) {
        $('body').append(`
            <div class="nexus-margin-backdrop" id="nexusMarginBackdrop"></div>
            <div class="nexus-margin-panel" id="nexusMarginPanel">
                <div class="p-4 bg-white border-bottom d-flex justify-content-between align-items-center shadow-sm">
                    <h4 class="mb-0 fw-bold text-dark" id="margin-panel-title"><i class="fa fa-calculator me-2"></i> Gross Margin Analysis</h4>
                    <div class="d-flex align-items-center gap-2">
                        <button class="btn btn-outline-primary fw-bold btn-sm shadow-sm me-2" id="toggle-car-hire-btn">Switch to Car Hire</button>
                        <button class="btn btn-light border btn-sm rounded-circle" id="closeMarginPanel" style="width: 36px; height: 36px;"><i class="fa fa-times"></i></button>
                    </div>
                </div>
                <div class="flex-grow-1 p-4 overflow-auto" id="margin-panel-content">
                    <div class="text-center text-muted py-5">Select a group and click "Analyze Margin"</div>
                </div>
            </div>
        `);
    }

    $(wrapper).find('.layout-main-section').append(`
        <div class="optimizer-controls mb-4 p-3 bg-white rounded shadow-sm border">
            <button class="btn btn-primary px-4 fw-bold" id="run-optimizer"><i class="fa fa-bolt me-2"></i> Run AI Optimizer</button>
            <button class="btn btn-light border ms-2 px-4" id="clear-filters">Clear Filters</button>
        </div>
        <div id="optimizer-sections-container"></div>
    `);

    function openRoutePanel() { $('#nexusRouteBackdrop').fadeIn(200); $('#nexusRoutePanel').addClass('open'); }
    function closeRoutePanel() { $('#nexusRoutePanel').removeClass('open'); $('#nexusRouteBackdrop').fadeOut(200); }
    $('#closeRoutePanel, #nexusRouteBackdrop').on('click', closeRoutePanel);

    function openMarginPanel() { $('#nexusMarginBackdrop').fadeIn(200); $('#nexusMarginPanel').addClass('open'); }
    function closeMarginPanel() { $('#nexusMarginPanel').removeClass('open'); $('#nexusMarginBackdrop').fadeOut(200); }
    $('#closeMarginPanel, #nexusMarginBackdrop').on('click', closeMarginPanel);

    // =========================================================================
    // 🚨 0-LAG WEBSOCKET FLEET ENGINE (Live Tracking on Optimizer Map)
    // =========================================================================

    function connectFleetWebSocket() {
        if (fleetWS && fleetWS.readyState === WebSocket.OPEN) return;

        fleetWS = new WebSocket(FASTAPI_WS_URL);

        fleetWS.onopen = () => {
            $('#fleet-conn-stat')
                .html('<i class="fa fa-wifi me-1"></i> Live Fleet')
                .removeClass('text-danger border-danger bg-danger-subtle')
                .addClass('text-success border-success bg-success-subtle');

            if (fleetPingInterval) clearInterval(fleetPingInterval);
            fleetPingInterval = setInterval(() => {
                if (fleetWS && fleetWS.readyState === WebSocket.OPEN) {
                    fleetWS.send(JSON.stringify({ action: "ping" }));
                }
            }, 30000);
        };

        fleetWS.onmessage = (event) => {
            try {
                const data = JSON.parse(event.data);
                if (data.action === "pong") return;
                
                if (data.fleet) {
                    LIVE_FLEET_STATE = data.fleet;
                    updateFleetMarkers();
                }
            } catch (e) {
                console.error("Fleet WS parse error:", e);
            }
        };

        fleetWS.onclose = () => {
            $('#fleet-conn-stat')
                .html('<i class="fa fa-exclamation-triangle me-1"></i> WS Dropped')
                .removeClass('text-success border-success bg-success-subtle')
                .addClass('text-danger border-danger bg-danger-subtle');
                
            if (fleetPingInterval) clearInterval(fleetPingInterval);
            setTimeout(connectFleetWebSocket, 3000); // Auto-reconnect
        };

        fleetWS.onerror = () => { fleetWS.close(); };
    }

    function build_truck_icon(heading) {
        const color = '#ef4444'; // Red Nexus Fleet Theme
        const htmlIcon = `
            <div style="position:relative;width:36px;height:36px;">
                <div class="direction-ring" style="position:absolute;top:0;left:0;width:100%;height:100%;transform:rotate(${heading}deg);transition:transform 0.5s linear;">
                    <svg width="36" height="36" viewBox="0 0 24 24" fill="none" stroke="${color}" stroke-width="2" style="opacity:0.8;">
                        <path d="M12 2L15 8L12 6L9 8L12 2Z" fill="${color}"/>
                    </svg>
                </div>
                <div style="position:absolute;top:5px;left:5px;background:#fff;border-radius:50%;width:26px;height:26px;box-shadow:0 2px 5px rgba(0,0,0,0.3);display:flex;align-items:center;justify-content:center;border:2px solid ${color};">
                    <i class="fa fa-truck" style="color:${color};font-size:12px;"></i>
                </div>
            </div>`;
        return L.divIcon({ className: '', html: htmlIcon, iconSize: [36, 36], iconAnchor: [18, 18] });
    }

    function updateFleetMarkers() {
        if (!route_map) return; // Prevent updates if map isn't open/rendered yet

        const currentActiveIds = new Set(Object.keys(LIVE_FLEET_STATE));

        Object.keys(LIVE_FLEET_STATE).forEach(tracking_id => {
            const truck = LIVE_FLEET_STATE[tracking_id];
            if (!truck.lat || !truck.lng) return;

            if (fleet_markers[tracking_id]) {
                // Update Location & Smooth Rotation
                fleet_markers[tracking_id].setLatLng([truck.lat, truck.lng]);
                
                const iconEl = fleet_markers[tracking_id].getElement();
                if (iconEl) {
                    const arrow = iconEl.querySelector('.direction-ring');
                    if (arrow) arrow.style.transform = `rotate(${truck.heading || 0}deg)`;
                }
            } else {
                // Generate New Truck Marker
                const icon = build_truck_icon(truck.heading || 0);
                const popupText = `<b>Driver: ${truck.driver.split('@')[0].toUpperCase()}</b><br><span class="text-muted small">Vehicle: <strong class="text-dark">${truck.vehicle}</strong></span><br><span class="text-muted small">Speed: ${Math.round((truck.speed||0)*3.6)} km/h</span>`;
                
                fleet_markers[tracking_id] = L.marker([truck.lat, truck.lng], { icon, zIndexOffset: 2000 })
                    .addTo(route_map)
                    .bindPopup(`<div class="p-1" style="font-size: 13px;">${popupText}</div>`);
            }
        });

        // 🚨 Ghost Truck Sweeper
        Object.keys(fleet_markers).forEach(tracking_id => {
            if (!currentActiveIds.has(tracking_id)) {
                route_map.removeLayer(fleet_markers[tracking_id]);
                delete fleet_markers[tracking_id];
            }
        });
    }

    // =========================================================================
    // 🚨 GROSS MARGIN CALCULATOR ENGINE
    // =========================================================================

    function updateMarginMath() {
        if (!active_margin_data) return;
        const data = active_margin_data;
        const currency = data.currency || 'KES';
        
        let logistics_cost = 0;

        if (is_car_hire_mode) {
            let days = $('#hire-days').length ? parseFloat($('#hire-days').val()) || 0 : 0;
            let rate = $('#hire-rate').length ? parseFloat($('#hire-rate').val()) || 0 : 0;
            logistics_cost = days * rate;
            
            if ($('#val-car-hire-cost').length) {
                $('#val-car-hire-cost').text(`${currency} ${logistics_cost.toLocaleString(undefined, {minimumFractionDigits: 2, maximumFractionDigits: 2})}`);
            }
        } else {
            logistics_cost = data.approximate_fuel_cost || 0;
        }

        if ($('#val-logistics-cost').length) {
            $('#val-logistics-cost').text(`${currency} ${logistics_cost.toLocaleString(undefined, {minimumFractionDigits: 2, maximumFractionDigits: 2})}`);
        }

        let net_profit = data.gross_profit - data.absorbed_overhead - logistics_cost;
        let net_margin_percentage = data.total_order_value > 0 ? (net_profit / data.total_order_value * 100) : 0;

        const profitClass = net_profit >= 0 ? 'profit-positive' : 'profit-negative';
        const profitIcon = net_profit >= 0 ? 'fa-arrow-up' : 'fa-arrow-down';
        
        $('#val-net-profit').html(`<i class="fa ${profitIcon} me-2"></i>${currency} ${Math.abs(net_profit).toLocaleString(undefined, {minimumFractionDigits: 2, maximumFractionDigits: 2})}`)
                             .removeClass('profit-positive profit-negative').addClass(profitClass);
        
        const alertClass = net_profit >= 0 ? 'alert-success' : 'alert-danger';
        const alertIcon = net_profit >= 0 ? 'fa-check-circle' : 'fa-exclamation-triangle';
        const alertText = net_profit >= 0 ? ' (Profitable)' : ' (Loss making)';
        
        $('#val-margin-alert').removeClass('alert-success alert-danger').addClass(alertClass)
                              .html(`<i class="fa ${alertIcon} me-2"></i><strong>Net Margin: ${net_margin_percentage.toFixed(2)}%</strong>${alertText}`);
    }

    function renderMarginUI() {
    if (!active_margin_data) return;
    const data = active_margin_data;
    const currency = data.currency || 'KES';
    
    let car_hire_html = '';

    if (is_car_hire_mode) {
        $('#margin-panel-title').html('<i class="fa fa-truck me-2 text-warning"></i> Projected Net Contribution (External Fleet)');
        $('#toggle-car-hire-btn').text('Switch to Company Fleet').removeClass('btn-outline-primary').addClass('btn-primary text-white border-0 me-2');
        
        car_hire_html = `
            <div class="bg-white p-4 rounded-4 border mb-4 shadow-sm" style="border-left: 5px solid #f59e0b !important;">
                <div class="d-flex justify-content-between align-items-center mb-3">
                    <h6 class="fw-bold mb-0 text-dark"><i class="fa fa-handshake me-2 text-warning"></i>Car Hire Parameters</h6>
                </div>
                <div class="row g-3 align-items-end">
                    <div class="col-md-4">
                        <label class="small text-muted fw-bold">Number of Days for Hire</label>
                        <input type="number" class="form-control form-control-lg fw-bold" id="hire-days" value="1" min="0">
                    </div>
                    <div class="col-md-4">
                        <label class="small text-muted fw-bold">Hire Cost per Day (${currency})</label>
                        <input type="number" class="form-control form-control-lg fw-bold" id="hire-rate" value="0" min="0">
                    </div>
                    <div class="col-md-4 text-end">
                        <div class="text-muted small fw-bold mb-1">Total Car Hire Cost</div>
                        <div class="fs-3 text-warning" id="val-car-hire-cost" style="font-weight: 900 !important;">${currency} 0.00</div>
                    </div>
                </div>
            </div>
        `;
    } else {
        $('#margin-panel-title').html('<i class="fa fa-calculator me-2 text-primary"></i> Projected Net Contribution');
        $('#toggle-car-hire-btn').text('Switch to Car Hire').removeClass('btn-primary text-white border-0').addClass('btn-outline-primary me-2');
    }

    let distance_warning = data.approximate_total_distance_km === 0 
        ? `<span class="badge bg-danger ms-2">Map Not Updated</span>` : '';

    let html = `
        ${car_hire_html}
        <div class="bg-white rounded-4 border p-4 shadow-sm">
            <h5 class="border-bottom pb-3 mb-4 fw-bold text-dark">Load Financial Summary <span class="badge bg-secondary ms-2" style="font-size:11px; font-weight:600;">Pre-Dispatch Projection</span></h5>
            
            <div class="row mb-4 g-4">
                <div class="col-6">
                    <div class="text-muted small text-uppercase tracking-wider fw-bold mb-1">Total Order Value</div>
                    <div class="margin-value text-dark">${currency} ${data.total_order_value.toLocaleString(undefined, {minimumFractionDigits: 2, maximumFractionDigits: 2})}</div>
                </div>
                <div class="col-6">
                    <div class="text-muted small text-uppercase tracking-wider fw-bold mb-1">Theoretical Production Cost</div>
                    <div class="margin-value text-secondary">${currency} ${data.total_theoretical_cost.toLocaleString(undefined, {minimumFractionDigits: 2, maximumFractionDigits: 2})}</div>
                    <div class="small text-muted mt-1"><i class="fa fa-info-circle me-1"></i>Standard Buying price rollup</div>
                </div>
                <div class="col-6">
                    <div class="text-muted small text-uppercase tracking-wider fw-bold mb-1">Projected Gross Margin</div>
                    <div class="margin-value ${data.gross_profit >= 0 ? 'text-success' : 'text-danger'}">${currency} ${data.gross_profit.toLocaleString(undefined, {minimumFractionDigits: 2, maximumFractionDigits: 2})}</div>
                </div>
                <div class="col-6">
                    <div class="text-muted small text-uppercase tracking-wider fw-bold mb-1">Projected Gross Margin (%)</div>
                    <div class="margin-value text-dark">${data.gross_margin_percentage.toFixed(2)}%</div>
                </div>
            </div>

            <div class="row mb-4 g-4 border-top pt-4 bg-light mx-0 p-2 rounded border shadow-sm">
                <div class="col-12">
                    <div class="text-muted small text-uppercase tracking-wider fw-bold mb-1">${is_car_hire_mode ? 'External Fleet Cost (Car Hire)' : 'Est. Fleet Fuel Cost'} ${distance_warning}</div>
                    <div class="margin-value text-secondary" id="val-logistics-cost"></div>
                    ${!is_car_hire_mode ? `
                    <div class="small text-muted mt-1 fw-medium">
                        <i class="fa fa-road me-1"></i> ${data.approximate_total_distance_km} km roundtrip &nbsp;|&nbsp; 
                        <i class="fa fa-gas-pump me-1"></i> ${data.approximate_fuel_consumption_ltrs} Ltrs consumed
                    </div>` : ''}
                </div>
            </div>

            <div class="row mb-4 g-4 border-top pt-4">
                <div class="col-6">
                    <div class="text-muted small text-uppercase tracking-wider fw-bold mb-1">Absorbed Company Overhead</div>
                    <div class="margin-value text-secondary">${currency} ${data.absorbed_overhead.toLocaleString(undefined, {minimumFractionDigits: 2, maximumFractionDigits: 2})}</div>
                    <div class="small text-muted mt-1 fw-medium">
                        <i class="fa fa-info-circle me-1"></i>
                        ${data.overhead_ratio_percentage.toFixed(1)}% of order value absorbed
                    </div>
                </div>
                <div class="col-6">
                    <div class="text-muted small text-uppercase tracking-wider fw-bold mb-1">Company Standard Overhead Rate</div>
                    <div class="margin-value text-secondary">${data.overhead_ratio_percentage.toFixed(2)}%</div>
                    <div class="small text-muted mt-1 fw-medium">
                        <i class="fa fa-building me-1"></i>
                        KES ${(data.overhead_standard_overheads || 26000000).toLocaleString()} OH
                        &nbsp;/&nbsp;
                        KES ${(data.overhead_standard_revenue || 80000000).toLocaleString()} avg monthly revenue
                    </div>
                </div>
            </div>

            <div class="row mb-3 g-3 mt-4 border-top pt-4 bg-light rounded-bottom p-3 mx-0 shadow-sm border">
                <div class="col-12 text-center">
                    <div class="text-muted small fw-bold text-uppercase tracking-wider mb-1">
                        Estimated Trip Net Contribution
                        ${is_car_hire_mode ? '<span class="badge bg-warning text-dark ms-1">Incl. Car Hire</span>' : ''}
                        <span class="badge bg-info text-dark ms-1" style="font-size:10px;">Pre-Dispatch Estimate</span>
                    </div>
                    <div class="fs-1" id="val-net-profit" style="font-size: 3rem !important; font-weight: 900 !important;"></div>
                </div>
            </div>

            <div class="alert mt-4 text-center fs-4 border-2" id="val-margin-alert"></div>
        </div>
        
        <div class="text-muted small mt-4 pt-3 text-center opacity-75">
            <i class="fa fa-info-circle me-1"></i> All figures are pre-dispatch projections based on Standard Buying price BOM rollup. Realised margins are tracked in Dispatch Intelligence after delivery.
        </div>
    `;
    
    $('#margin-panel-content').html(html);
    updateMarginMath();

    if (is_car_hire_mode) {
        $('#hire-days, #hire-rate').off('input').on('input', function() {
            if ($(this).val() < 0) {
                $(this).val(Math.abs($(this).val()));
            }
            updateMarginMath(); 
        });
    }
}

    $('body').off('click', '#toggle-car-hire-btn').on('click', '#toggle-car-hire-btn', function() {
        is_car_hire_mode = !is_car_hire_mode;
        renderMarginUI();
    });

    // =========================================================================
    // 🚚 LOGISTICS DATA RENDERERS
    // =========================================================================

    function format_weight(kg) {
        if (!kg || kg <= 0) return "0.0 T";
        return `${(kg / 1000).toFixed(2)} Tonnes (${Math.round(kg)} kg)`;
    }

    function get_current_group_data(idx) {
        let card = $(`#group-card-${idx}`);
        let sos = [];
        let total_weight = 0;
        let total_amount = 0;
        
        card.find('.order-row').each(function() {
            let so = $(this).data('so');
            sos.push(so);
            total_weight += parseFloat($(this).data('weight') || 0);
            total_amount += parseFloat($(this).data('amount') || 0);
        });
        
        let base_group = current_data.groups[idx];
        return {
            ...base_group,
            sales_orders: sos,
            total_tonnage: total_weight,
            total_amount: total_amount,
            utilization: (total_weight / base_group.max_capacity) * 100
        };
    }

    function recalculate_group_totals() {
        $('.load-group-card').each(function() {
            let card = $(this);
            let max = parseFloat(card.data('max'));
            let current_weight = 0;
            let current_amount = 0;
            
            card.find('.order-row').each(function() { 
                current_weight += parseFloat($(this).data('weight') || 0); 
                current_amount += parseFloat($(this).data('amount') || 0);
            });
            
            let util = (current_weight / max) * 100;
            
            card.find('.util-badge').text(util.toFixed(1) + '% Full');
            card.find('.total-value-display').text('KES ' + current_amount.toLocaleString(undefined, {minimumFractionDigits: 0, maximumFractionDigits: 0}));
            card.find('.tonnage-sum-display').text((current_weight/1000).toFixed(2) + ' T');
            card.find('.load-state-display').text(util.toFixed(1) + '% Full');
            
            card.find('.load-state-display').removeClass('text-success text-warning text-danger');
            if (util >= 90) { card.find('.load-state-display').addClass('text-success'); } 
            else if (util >= 70) { card.find('.load-state-display').addClass('text-warning'); } 
            else { card.find('.load-state-display').addClass('text-danger'); }

            card.toggleClass('overload', current_weight > max);
            if (current_weight > max) {
                card.find('.overload-msg').html(`<i class="fa fa-exclamation-triangle"></i> OVERLOADED BY ${Math.round(current_weight - max)} kg`);
            }
        });
    }

    function render_groups(groups) {
        if (!groups || groups.length === 0) {
            $('#optimizer-sections-container').html(`<div class="alert alert-info text-center p-5 rounded-4 border-0">No matching orders available for optimization in this region.</div>`);
            return;
        }

        captured_route_data = {}; 
        let container = $('#optimizer-sections-container').empty();

        groups.forEach((group, idx) => {
            let util_color = group.utilization >= 90 ? 'text-success' : group.utilization >= 70 ? 'text-warning' : 'text-danger';
            
            let html = `
                <div class="card mb-5 shadow-sm border-0 rounded-4 overflow-hidden load-group-card" id="group-card-${idx}" data-max="${group.max_capacity}">
                    <div class="card-header text-white p-4" style="background: #1e3a8a;">
                        <div class="d-flex justify-content-between align-items-center">
                            <h5 class="mb-0 fw-bold fs-4">Load Group ${idx + 1}</h5>
                            <div class="d-flex align-items-center gap-2">
                                <span class="overload-msg"></span>
                                <span class="badge bg-white text-dark px-3 py-2 rounded-pill fw-bold util-badge">${group.utilization.toFixed(1)}% Full</span>
                                <span class="badge bg-white bg-opacity-25 text-white px-3 py-2 rounded-pill">${format_weight(group.max_capacity)} Max</span>
                            </div>
                        </div>
                    </div>
                    
                    <div class="card-body bg-light p-4 border-bottom text-center">
                        <div class="row g-3">
                            <div class="col-md-4 border-end">
                                <div class="small text-muted text-uppercase tracking-wider">Total Value</div>
                                <div class="fs-3 fw-bold text-dark total-value-display">KES ${group.total_amount.toLocaleString(undefined, {minimumFractionDigits: 0, maximumFractionDigits: 0})}</div>
                            </div>
                            <div class="col-md-4 border-end">
                                <div class="small text-muted text-uppercase tracking-wider">Tonnage Sum</div>
                                <div class="fs-3 fw-bold text-dark tonnage-sum-display">${(group.total_tonnage/1000).toFixed(2)} T</div>
                            </div>
                            <div class="col-md-4">
                                <div class="small text-muted text-uppercase tracking-wider">Load State</div>
                                <div class="fs-3 fw-bold load-state-display ${util_color}">${group.utilization.toFixed(1)}% Full</div>
                            </div>
                        </div>
                    </div>

                    <div class="table-responsive" style="min-height: 100px;">
                        <table class="table table-hover align-middle mb-0">
                            <thead class="table-light text-secondary small text-uppercase">
                                <tr>
                                    <th class="ps-4">Readiness</th>
                                    <th>Sales Order</th>
                                    <th>Customer Info</th>
                                    <th>Payment Terms</th>
                                    <th>Wait Time</th>
                                    <th>Items</th>
                                    <th class="text-end">Amount</th>
                                    <th class="text-end pe-4">Weight</th>
                                </tr>
                            </thead>
                            <tbody class="drag-container" id="sortable-group-${idx}">
            `;

            group.sales_orders.forEach(so => {
                let readiness_class = so.readiness === 'Ready' ? 'bg-success' : so.readiness === 'Partial Shortage' ? 'bg-warning text-dark' : 'bg-danger';
                let has_gps = (so.latitude && so.longitude && so.latitude !== 0);
                let gps_badge = has_gps ? `<span class="gps-indicator gps-mapped ms-2"><i class="fa fa-map-marker-alt"></i> Mapped</span>` : `<span class="gps-indicator gps-missing ms-2">No GPS</span>`;
                
                let safe_so_json = JSON.stringify(so).replace(/'/g, "&apos;");

                html += `
                    <tr class="order-row" data-so='${safe_so_json}' data-weight="${so.total_weight}" data-amount="${so.amount || 0}">
                        <td class="ps-4"><span class="badge ${readiness_class} px-3 py-2 rounded-pill">${so.readiness}</span></td>
                        <td>
                            <a href="/app/sales-order/${so.sales_order}" target="_blank" class="fw-bold text-primary">${so.sales_order}</a>
                            <div class="small text-muted mt-1">${so.so_status}</div>
                        </td>
                        <td>
                            <div class="fw-bold">${so.customer_name} ${gps_badge}</div>
                            <div class="small text-muted">${so.customer}</div>
                        </td>
                        <td><div class="text-dark small fw-medium">${so.payment_terms}</div></td>
                        <td><span class="waittime-badge"><i class="fa fa-clock me-1"></i>${so.wait_time}</span></td>
                        <td><div class="small text-muted" title="${so.altered_items_full}">${so.altered_items || 'Standard'}</div></td>
                        <td class="text-end fw-bold text-success">KES ${(so.amount || 0).toLocaleString(undefined, {minimumFractionDigits: 0, maximumFractionDigits: 0})}</td>
                        <td class="text-end pe-4 fw-bold text-dark">${format_weight(so.total_weight)}</td>
                    </tr>
                `;
            });

            let can_map = group.sales_orders.some(so => so.latitude && so.longitude);

            html += `
                            </tbody>
                        </table>
                    </div>
                    <div class="card-footer bg-white p-4 d-flex justify-content-between gap-3 rounded-bottom-4">
                        <div class="d-flex gap-2">
                            <button class="btn btn-success fw-bold px-4 margin-analyze-btn" data-idx="${idx}"><i class="fa fa-chart-line me-2"></i> Calculate Theoretical Cost & Gross Margin</button>
                        </div>
                        <div class="d-flex gap-2">
                            <button class="btn btn-warning fw-bold px-4 update-map-btn" data-idx="${idx}"><i class="fa fa-refresh me-2"></i> Update Map</button>
                            <button class="btn btn-outline-primary fw-bold px-4 view-route-btn ${can_map ? '' : 'disabled'}" data-idx="${idx}"><i class="fa fa-map-marked-alt me-2"></i> View Optimized Route</button>
                            <button class="btn btn-light border fw-bold px-4 create-soft" data-idx="${idx}">Create Soft Plan</button>
                            <button class="btn btn-primary fw-bold px-5 create-reserve" data-idx="${idx}">Save & Reserve Plan</button>
                        </div>
                    </div>
                </div>
            `;
            container.append(html);

            new Sortable(document.getElementById(`sortable-group-${idx}`), {
                group: 'shared-dispatch', 
                animation: 150,
                ghostClass: 'ghost-class',
                onEnd: function(evt) {
                    let from_idx = evt.from.id.split('-')[2];
                    let to_idx = evt.to.id.split('-')[2];
                    $(`#group-card-${from_idx}, #group-card-${to_idx}`).addClass('stale-map');
                    
                    recalculate_group_totals();
                }
            });
        });
    }

    function draw_group_route(group, idx) {
        let start_lat = parseFloat(current_data.factory_lat);
        let start_lng = parseFloat(current_data.factory_lng);

        if (!route_map) {
            route_map = L.map('offcanvas-map-container', { zoomControl: false }).setView([start_lat, start_lng], 12);
            L.control.zoom({ position: 'topright' }).addTo(route_map);
            L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', { attribution: 'Nexus Spatial Engine' }).addTo(route_map);
        }

        // Only clear routing layers, leave the fleet markers intact!
        map_layers.forEach(layer => route_map.removeLayer(layer));
        map_layers = [];
        
        $('#route-distance-label').html('<i class="fa fa-spinner fa-spin me-2"></i> Orchestrating VROOM Sequence...');

        let valid_stops = group.sales_orders.filter(so => so.latitude && so.longitude && so.latitude !== 0);
        
        let coordinates = [[start_lng, start_lat]];
        valid_stops.forEach(so => coordinates.push([parseFloat(so.longitude), parseFloat(so.latitude)]));
        coordinates.push([start_lng, start_lat]); 

        fetch(`${FASTAPI_URL}/calculate-route`, {
            method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ coordinates: coordinates })
        })
        .then(res => res.json())
        .then(data => {
            if (data.error) {
                $('#route-distance-label').html(`<span class="text-danger"><i class="fa fa-exclamation-triangle"></i> Engine Error: Check Console</span>`);
                return;
            }

            captured_route_data[idx] = data;
            $(`#group-card-${idx}`).removeClass('stale-map');

            let routeLayer = L.geoJSON(data, { style: { color: '#3b82f6', weight: 6, opacity: 0.85, dashArray: '10, 6' } }).addTo(route_map);
            map_layers.push(routeLayer);

            let factoryIcon = L.divIcon({ className: '', html: `<div class="nexus-factory-marker"><i class="fa fa-industry" style="color:white;"></i></div>`, iconSize: [36, 36], iconAnchor: [18, 18] });
            map_layers.push(L.marker([start_lat, start_lng], { icon: factoryIcon, zIndexOffset: 1000 }).addTo(route_map).bindPopup("Factory Dispatch"));

            valid_stops.forEach((so, i) => {
                let icon = L.divIcon({ 
                    className: '', html: `<div class="nexus-map-marker">${i+1}</div>`, iconSize: [28, 28], iconAnchor: [14, 14] 
                });
                let m = L.marker([so.latitude, so.longitude], { icon: icon }).addTo(route_map)
                        .bindPopup(`<b>Stop ${i+1}: ${so.customer_name}</b><br>Tonnage: ${(so.total_weight/1000).toFixed(2)} T`);
                map_layers.push(m);
            });

            route_map.fitBounds(routeLayer.getBounds(), { padding: [60, 60] });
            
            let dist = (data.features[0].properties.summary.distance / 1000).toFixed(1);
            let time = Math.round(data.features[0].properties.summary.duration / 60);
            $('#route-distance-label').html(`<span class="text-primary me-4"><i class="fa fa-road me-2"></i>${dist} km Roundtrip</span> <span><i class="fa fa-clock me-2"></i>Est. ${time} mins</span>`);
            
            // Re-render live fleet trucks to place them on top of the newly drawn route
            updateFleetMarkers();
        })
        .catch(err => {
            $('#route-distance-label').html(`<span class="text-danger">API Connection Failed</span>`);
        });
    }

    function showMarginAnalysis(groupIdx) {
        const dynamic_group = get_current_group_data(groupIdx);
        const company = page_fields.company.get_value();
        const vehicle = page_fields.vehicle_type.get_value();
        
        const route_geojson = captured_route_data[groupIdx] ? JSON.stringify(captured_route_data[groupIdx]) : null;

        if (!route_geojson) {
            frappe.show_alert({message: "Map could not be generated. Distance & Fuel estimation will be 0.", indicator: "orange"});
        }

        frappe.call({
            method: "nexus_supply_chain.nexus_supply_chain.page.nexus_load_optimizer.nexus_load_optimizer.get_group_margin_data",
            args: {
                group: dynamic_group,
                company_name: company,
                route_geojson: route_geojson,
                vehicle_type: vehicle
            },
            callback: function(r) {
                if (r.message && !r.message.error) {
                    active_margin_data = r.message;
                    renderMarginUI(); 
                } else {
                    $('#margin-panel-content').html(`
                        <div class="alert alert-danger mt-4 mx-3">
                            <i class="fa fa-exclamation-triangle me-2"></i> 
                            ${r.message?.error || 'Failed to compute margin. Ensure all items have default BOMs and vehicle metrics are configured.'}
                        </div>
                    `);
                }
            },
            error: function(err) {
                $('#margin-panel-content').html(`
                    <div class="alert alert-danger mt-4 mx-3">
                        <i class="fa fa-exclamation-triangle me-2"></i> 
                        Server error: ${err.message}
                    </div>
                `);
            }
        });
    }

    $(wrapper).on('click', '#run-optimizer', () => {
        let filters = {};
        Object.keys(page_fields).forEach(k => { let v = page_fields[k].get_value(); if(v) filters[k] = v; });

        if (!filters.vehicle_type || !filters.delivery_region) {
            frappe.msgprint(__("Select Vehicle Type and Region first."));
            return;
        }

        frappe.call({
            method: "nexus_supply_chain.nexus_supply_chain.page.nexus_load_optimizer.nexus_load_optimizer.run_optimizer",
            args: { filters: filters },
            freeze: true,
            callback: (r) => { if (r.message) { current_data = r.message; render_groups(r.message.groups); } }
        });
    });

    $(wrapper).on('click', '.view-route-btn, .update-map-btn', function() {
        if ($(this).hasClass('disabled')) return;
        const idx = $(this).data('idx');
        const dynamic_group = get_current_group_data(idx);
        openRoutePanel();
        setTimeout(() => { if(route_map) route_map.invalidateSize(); draw_group_route(dynamic_group, idx); }, 400);
    });

    $(wrapper).on('click', '.margin-analyze-btn', function() {
        const idx = $(this).data('idx');
        const dynamic_group = get_current_group_data(idx);
        const card = $(`#group-card-${idx}`);

        is_car_hire_mode = false;
        $('#margin-panel-content').html(`
            <div class="text-center py-5 mt-5">
                <i class="fa fa-spinner fa-spin fa-3x text-primary mb-4"></i>
                <p class="text-muted fs-5 fw-bold">Orchestrating Spatial Routing & Financial Data...</p>
            </div>
        `);
        openMarginPanel();

        if (card.hasClass('stale-map') || !captured_route_data[idx]) {
            let start_lat = parseFloat(current_data.factory_lat);
            let start_lng = parseFloat(current_data.factory_lng);
            let valid_stops = dynamic_group.sales_orders.filter(so => so.latitude && so.longitude && so.latitude !== 0);
            
            let coordinates = [[start_lng, start_lat]];
            valid_stops.forEach(so => coordinates.push([parseFloat(so.longitude), parseFloat(so.latitude)]));
            coordinates.push([start_lng, start_lat]); 

            fetch(`${FASTAPI_URL}/calculate-route`, {
                method: 'POST', headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ coordinates: coordinates })
            })
            .then(res => res.json())
            .then(data => {
                if (!data.error) {
                    captured_route_data[idx] = data; 
                }
                showMarginAnalysis(idx); 
            })
            .catch(err => {
                showMarginAnalysis(idx); 
            });
        } else {
            showMarginAnalysis(idx); 
        }
    });

    $(wrapper).on('click', '.create-soft, .create-reserve', function() {
        const idx = $(this).data('idx');
        const card = $(`#group-card-${idx}`);
        
        if (card.hasClass('overload')) {
            frappe.msgprint({title: "Action Required", indicator: "red", message: "This truck is overloaded. Please move some orders to another group before saving."});
            return;
        }

        const dynamic_group = get_current_group_data(idx);
        const action = $(this).hasClass('create-reserve') ? 'reserve' : 'soft';

        frappe.call({
            method: "nexus_supply_chain.nexus_supply_chain.page.nexus_load_optimizer.nexus_load_optimizer.create_load_plan",
            args: { 
                group: dynamic_group, 
                action: action, 
                route_geojson: captured_route_data[idx] ? JSON.stringify(captured_route_data[idx]) : "",
                vehicle_type: page_fields.vehicle_type.get_value(),
                transport_mode: page_fields.transport_mode.get_value(),
                company: page_fields.company.get_value(),
                delivery_region: page_fields.delivery_region.get_value()
            },
            freeze: true,
            callback: (r) => {
                if (r.message?.status === "success") {
                    frappe.show_alert({message: r.message.message, indicator: 'green'});
                    setTimeout(() => $('#run-optimizer').trigger('click'), 1200);
                }
            }
        });
    });

    $(wrapper).on('click', '#clear-filters', () => {
        Object.values(page_fields).forEach(f => f.set_value(f.df.default || ''));
        $('#optimizer-sections-container').empty();
        current_data = null;
        captured_route_data = {};
        active_margin_data = null;
    });
};