// apps/nexus_supply_chain/nexus_supply_chain/page/nexus_dispatch_intelligence/nexus_dispatch_intelligence.js

frappe.pages['nexus_dispatch_intelligence'].on_page_load = function(wrapper) {

    var page = frappe.ui.make_app_page({
        parent: wrapper,
        title: __('Nexus Dispatch Intelligence'),
        single_column: true
    });

    var state = {
        metrics: {},
        load_plans: [],
        audit_sos: [],
        audit_summary: {},
        
        unplanned_orders: [],
        unplanned_exploded_items: [],
        unplanned_count: 0,
        
        active_lp: null, // Can be 'unplanned' or an actual Load Plan name
        date_filter: 'today',
        sort_order: 'desc',
        custom_start: '',
        custom_end: ''
    };

    $(page.main).html(`
        <div style="background-color: #f4f6f8; min-height: 100vh; padding: 20px; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif;">
            
            <!-- TOP METRICS HEADER -->
            <div id="metrics-container" style="display:flex; gap:15px; margin-bottom: 25px; flex-wrap: wrap;">
                <!-- Populated dynamically -->
            </div>

            <!-- MAIN LAYOUT -->
            <div style="display:flex; gap:20px; height: calc(100vh - 160px);">

                <!-- SIDEBAR -->
                <div style="width:320px; flex-shrink:0; background: #fff; border-radius: 8px; box-shadow: 0 1px 3px rgba(0,0,0,0.05); border: 1px solid #e2e8f0; display:flex; flex-direction:column;">
                    
                    <div style="padding: 15px; border-bottom: 1px solid #e2e8f0; background: #f8fafc; border-radius: 8px 8px 0 0;">
                        
                        <!-- Unplanned Orders Sidebar Card -->
                        <div id="unplanned-card-container"></div>
                        
                        <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:12px;">
                            <div class="h5 mb-0" style="font-weight: 700; color: #1e293b;">Load Plans</div>
                            <button id="btn-refresh-plans" class="btn btn-xs btn-default" style="background:#fff; font-weight:600; padding:4px 10px;" title="Force Refresh All Data">
    <i class="fa fa-sync-alt text-primary"></i> Refresh
</button>
                        </div>
                        
                        <div style="display:flex; gap:5px; margin-bottom:8px;">
                            <select id="lp-date-filter" class="form-control form-control-sm" style="flex:1; font-weight: 500;">
                                <option value="today" selected>Today</option>
                                <option value="yesterday">Yesterday</option>
                                <option value="all">All Dates</option>
                                <option value="custom">Custom Range</option>
                            </select>
                            <button id="btn-sort-date" class="btn btn-sm btn-default" style="background:#fff; font-weight:600;">⬇ Newest</button>
                        </div>
                        <div id="custom-date-wrap" style="display:none; flex-direction:column; gap:5px;">
                            <input type="date" id="lp-date-start" class="form-control form-control-sm">
                            <input type="date" id="lp-date-end" class="form-control form-control-sm">
                        </div>
                    </div>

                    <div id="lp-list" style="flex:1; overflow-y:auto; padding: 15px;">
                        <div class="text-muted small">Loading analytics...</div>
                    </div>
                </div>

                <!-- MAIN CANVAS -->
                <div id="lp-canvas" style="flex:1; background: #fff; border-radius: 8px; box-shadow: 0 1px 3px rgba(0,0,0,0.05); border: 1px solid #e2e8f0; overflow-y:auto;">
                    <!-- Empty State mimicking the screenshot -->
                    <div style="display:flex; flex-direction:column; justify-content:center; align-items:center; height: 100%; color: #475569;">
                        <img src="https://img.icons8.com/color/96/000000/truck.png" style="width: 60px; margin-bottom: 15px; opacity: 0.9;" alt="Truck"/>
                        <h4 style="font-weight: 700; margin-bottom: 5px; color: #1e293b;">No dispatched plans selected</h4>
                        <p style="font-size: 14px;">Select a plan from the sidebar to view detailed financial and dispatch audits.</p>
                    </div>
                </div>

            </div>
        </div>
    `);

    // ── Bindings ──────────────────────────────────────────────────────────
    $('#lp-date-filter').on('change', function() {
        state.date_filter = $(this).val();
        $('#custom-date-wrap').toggle(state.date_filter === 'custom');
        fetch_metrics();
        render_sidebar();
    });

    $('#lp-date-start, #lp-date-end').on('change', function() {
        state.custom_start = $('#lp-date-start').val();
        state.custom_end = $('#lp-date-end').val();
        fetch_metrics();
        render_sidebar();
    });

    $('#btn-sort-date').on('click', function() {
        state.sort_order = state.sort_order === 'desc' ? 'asc' : 'desc';
        $(this).html(state.sort_order === 'desc' ? '⬇ Newest' : '⬆ Oldest');
        render_sidebar();
    });

    $('#btn-refresh-plans').on('click', function() {
        let btn = $(this);

        // Disable button and show spinner + loading state matching load_desk pattern
        btn.prop('disabled', true)
           .html('<i class="fa fa-sync-alt fa-spin text-primary"></i> Refreshing...');

        // Show skeleton loading state in sidebar list
        $('#lp-list').html(`
            <div style="display:flex; flex-direction:column; gap:8px; margin-top:4px;">
                ${[1,2,3].map(() => `
                    <div style="padding:12px; border:1px solid #e2e8f0; border-radius:6px; background:#f8fafc; animation: pulse 1.4s ease-in-out infinite;">
                        <div style="height:12px; background:#e2e8f0; border-radius:4px; width:70%; margin-bottom:8px;"></div>
                        <div style="height:10px; background:#e2e8f0; border-radius:4px; width:45%; margin-bottom:8px;"></div>
                        <div style="height:10px; background:#e2e8f0; border-radius:4px; width:30%;"></div>
                    </div>
                `).join('')}
            </div>
            <style>
                @keyframes pulse {
                    0%, 100% { opacity: 1; }
                    50% { opacity: 0.5; }
                }
            </style>
        `);

        fetch_all_data(() => {
            // Restore active canvas if one was selected
            if (state.active_lp === 'unplanned') {
                render_unplanned_canvas(false);
            } else if (state.active_lp) {
                let lp = state.load_plans.find(l => l.name === state.active_lp);
                if (lp) select_load_plan(lp);
            }

            // Restore button after data is rendered
            setTimeout(() => {
                btn.prop('disabled', false)
                   .html('<i class="fa fa-sync-alt text-primary"></i> Refresh');
            }, 400);
        });
    });

    // ── Data Fetching ─────────────────────────────────────────────────────
    function fetch_all_data(callback) {
        fetch_metrics();
        
        frappe.call({
            method: 'nexus_supply_chain.nexus_supply_chain.page.nexus_dispatch_intelligence.nexus_dispatch_intelligence.get_intelligence_plans',
            callback: function(r) {
                state.load_plans = r.message || [];
                
                frappe.call({
                    method: 'nexus_supply_chain.nexus_supply_chain.page.nexus_dispatch_intelligence.nexus_dispatch_intelligence.get_unplanned_confirmed_orders',
                    callback: function(r2) {
                        if (r2.message) {
                            state.unplanned_orders = r2.message.sales_orders || [];
                            state.unplanned_count = r2.message.count || 0;
                        }
                        render_sidebar();
                        if (callback) callback();
                    }
                });
            }
        });
    }

    function fetch_metrics() {
        frappe.call({
            method: 'nexus_supply_chain.nexus_supply_chain.page.nexus_dispatch_intelligence.nexus_dispatch_intelligence.get_dashboard_metrics',
            args: {
                filter_date: state.date_filter,
                start_date: state.custom_start,
                end_date: state.custom_end
            },
            callback: function(r) {
                if (r.message) {
                    state.metrics = r.message;
                    render_metrics_header();
                }
            }
        });
    }

    function select_load_plan(lp) {
        state.active_lp = lp.name;
        render_sidebar(); 
        
        $('#lp-canvas').html(`<div class="text-muted" style="padding:50px; text-align:center;"><div class="spinner-border text-primary" role="status"></div><br><br><b>Analyzing Exact Financial Line Items...</b></div>`);
        
        frappe.call({
            method: 'nexus_supply_chain.nexus_supply_chain.page.nexus_dispatch_intelligence.nexus_dispatch_intelligence.get_load_plan_audit',
            args: { load_plan_name: lp.name },
            callback: function(r) {
                if (r.message) {
                    state.audit_sos = r.message.sales_orders || [];
                    state.audit_summary = r.message.summary || {};
                    render_canvas();
                }
            }
        });
    }

    // ── UI Rendering ──────────────────────────────────────────────────────
    function render_metrics_header() {
        let m = state.metrics;
        let date_label = state.date_filter === 'today' ? 'TODAY' : (state.date_filter === 'yesterday' ? 'YESTERDAY' : 'PERIOD');
        
        let cardStyle = `flex:1; background:#fff; padding:15px 20px; border-radius:8px; box-shadow:0 1px 3px rgba(0,0,0,0.05); border:1px solid #e2e8f0; border-top: 3px solid;`;
        
        let html = `
            <div style="${cardStyle} border-top-color: #64748b;">
                <div style="font-size:11px; font-weight:700; color:#64748b; letter-spacing:0.5px; margin-bottom:5px;">OUTSTANDING (UNBILLED)</div>
                <div style="font-size:22px; font-weight:800; color:#0f172a; margin-bottom:2px;">${format_currency(m.total_outstanding_value, 'KES')}</div>
                <div style="font-size:11px; color:#94a3b8;">Delivered but pending invoice</div>
            </div>
            <div style="${cardStyle} border-top-color: #10b981;">
                <div style="font-size:11px; font-weight:700; color:#64748b; letter-spacing:0.5px; margin-bottom:5px;">TOTAL DELIVERED ${date_label}</div>
                <div style="font-size:22px; font-weight:800; color:#0f172a; margin-bottom:2px;">${format_currency(m.filtered_delivered_total, 'KES')}</div>
                <div style="font-size:11px; color:#94a3b8;">From ${m.fully_dispatched + m.partially_dispatched} active dispatches</div>
            </div>
            <div style="${cardStyle} border-top-color: #ef4444;">
                <div style="font-size:11px; font-weight:700; color:#64748b; letter-spacing:0.5px; margin-bottom:5px;">DELIVERED MARKET COGS</div>
                <div style="font-size:22px; font-weight:800; color:#0f172a; margin-bottom:2px;">${format_currency(m.delivered_market_cogs, 'KES')}</div>
                <div style="font-size:11px; color:#94a3b8;">Current replacement cost</div>
            </div>
            <div style="${cardStyle} border-top-color: #f59e0b;">
                <div style="font-size:11px; font-weight:700; color:#64748b; letter-spacing:0.5px; margin-bottom:5px;">TRUE GROSS MARGIN</div>
                <div style="font-size:22px; font-weight:800; color:#0f172a; margin-bottom:2px;">${format_currency(m.true_gross_margin, 'KES')}</div>
                <div style="font-size:11px; font-weight:700; color:#10b981;">${m.true_gross_margin_perc.toFixed(1)}% Yield on Delivered</div>
            </div>
            <div style="${cardStyle} border-top-color: #3b82f6;">
                <div style="font-size:11px; font-weight:700; color:#64748b; letter-spacing:0.5px; margin-bottom:5px;">ACTIVE PLANS</div>
                <div style="font-size:22px; font-weight:800; color:#0f172a; margin-bottom:2px;">${m.active_plans}</div>
                <div style="font-size:11px; color:#94a3b8;">Pending / Staging</div>
            </div>
        `;
        $('#metrics-container').html(html);
    }

    function render_sidebar() {
        let is_unp_active = state.active_lp === 'unplanned';
        let unp_bg = is_unp_active ? '#fdf1d6' : '#fff8e6';
        
        $('#unplanned-card-container').html(`
            <div id="card-unplanned" style="cursor:pointer; padding:12px; margin-bottom:15px; background:${unp_bg}; border:1px solid #f5c542; border-left:4px solid #e67e22; border-radius:6px; transition: all 0.2s ease;">
                <div style="display:flex; justify-content:space-between; align-items:center;">
                    <div style="font-weight:700; font-size:13px; color:#c57b00;">🗂 Unplanned Orders</div>
                    <span class="badge badge-warning" style="font-size:12px;">${state.unplanned_count}</span>
                </div>
                <div class="text-muted" style="font-size:11px; margin-top:4px;">
                    Confirmed SOs not yet allocated to any vehicle plan.
                </div>
            </div>
        `);

        $('#card-unplanned').on('click', function() {
            state.active_lp = 'unplanned';
            render_sidebar(); 
            render_unplanned_canvas(false);
        });

        let filtered = state.load_plans.filter(lp => {
            if (!lp.creation) return true;
            let lp_date_str = lp.creation.split(' ')[0]; 
            let today = frappe.datetime.get_today();
            let yesterday = frappe.datetime.add_days(today, -1);

            if (state.date_filter === 'today') return lp_date_str === today;
            if (state.date_filter === 'yesterday') return lp_date_str === yesterday;
            if (state.date_filter === 'custom') {
                if (state.custom_start && lp_date_str < state.custom_start) return false;
                if (state.custom_end && lp_date_str > state.custom_end) return false;
            }
            return true;
        });

        filtered.sort((a, b) => {
            let d1 = new Date(a.creation).getTime();
            let d2 = new Date(b.creation).getTime();
            return state.sort_order === 'desc' ? d2 - d1 : d1 - d2;
        });

        let html = '';
        
        var pending_lps = filtered.filter(l => !['Partially Dispatched', 'Fully Dispatched'].includes(l.dispatch_status));
        var dispatched_lps = filtered.filter(l => ['Partially Dispatched', 'Fully Dispatched'].includes(l.dispatch_status));

        if (pending_lps.length) {
            html += `<div style="font-size:13px; font-weight:800; color:#6c757d; letter-spacing:.6px; text-transform:uppercase; margin-bottom:8px; margin-top:6px;">ACTIVE</div>`;
            pending_lps.forEach(lp => html += _lp_card_html(lp));
        }

        if (dispatched_lps.length) {
            html += `<div style="font-size:13px; font-weight:800; color:#28a745; letter-spacing:.6px; text-transform:uppercase; margin-top:14px; margin-bottom:8px; border-top:1px solid #d1d8dd; padding-top:10px;">DISPATCHED</div>`;
            dispatched_lps.forEach(lp => html += _lp_card_html(lp));
        }

        if (!html) html = `
            <div style="display:flex; flex-direction:column; align-items:center; opacity:0.6; margin-top:20px;">
                <img src="https://img.icons8.com/color/48/000000/truck.png" style="width:40px; margin-bottom:10px;"/>
                <span class="text-muted small">No plans for this period</span>
            </div>`;

        $('#lp-list').html(html);

        $('#lp-list .lp-card').on('click', function() {
            let name = $(this).data('name');
            let lp = state.load_plans.find(l => l.name === name);
            if (lp) select_load_plan(lp);
        });
    }

    function _lp_card_html(lp) {
        var is_active = state.active_lp === lp.name;
        var disp_status = lp.dispatch_status || 'Pending';
        var is_dispatched = ['Partially Dispatched', 'Fully Dispatched'].includes(disp_status);
        var created_date = lp.creation ? frappe.datetime.str_to_user(lp.creation.split(' ')[0]) : '';
        
        let fill_bg = '';
        let percentage_display = '';
        let left_color = '';
        
        // Dynamic Progress Bar Painting using precise Physical Box percentage calculated in Python
        if (is_dispatched) {
            let pct = parseFloat(lp.delivered_percentage) || 0; 
            if (disp_status === 'Fully Dispatched') pct = 100;
            
            // Financial Color Triage (Matching Load Desk & Dispatch Board UI)
            let fill_color = disp_status === 'Fully Dispatched' ? '#d4edda' : '#fdebd0';
            left_color = disp_status === 'Fully Dispatched' ? '#28a745' : '#ca6f1e';
            
            // Uses CSS linear-gradient to fill the card background exactly to the percentage
            fill_bg = `linear-gradient(to right, ${fill_color} ${pct}%, #fff ${pct}%)`;
            percentage_display = `<span style="font-size:10px; color:#64748b; font-weight:700;">${pct.toFixed(0)}% DELIVERED</span>`;
        } else {
            fill_bg = is_active ? '#f1f5f9' : '#fff';
            left_color = (lp.docstatus === 1) ? '#2490ef' : '#adb5bd';
        }

        var border_color = is_active ? '#94a3b8' : '#e2e8f0';
        
        let badge_class = 'badge-secondary';
        if(disp_status === 'Fully Dispatched') badge_class = 'badge-success';
        if(disp_status === 'Partially Dispatched') badge_class = 'badge-warning';

        return `
            <div class="lp-card" data-name="${lp.name}"
                 style="padding:12px; margin-bottom:10px; background:${fill_bg}; border:1px solid ${border_color}; border-left:4px solid ${left_color}; border-radius:6px; cursor:pointer; transition:all 0.2s; box-shadow: 0 1px 2px rgba(0,0,0,0.03);">
                <div style="display:flex; justify-content:space-between; align-items:flex-start; margin-bottom:4px;">
                    <div style="font-weight:700; font-size:13px; color:#1e293b;">${lp.name}</div>
                    <div style="font-size:10px; color:#64748b;">${created_date}</div>
                </div>
                <div class="text-muted" style="font-size:11px; margin-bottom:6px;">${lp.vehicle_type || '—'}</div>
                <div style="display:flex; justify-content:space-between; align-items:center;">
                    <span class="badge ${badge_class}" style="font-size:10px;">${disp_status}</span>
                    ${percentage_display}
                </div>
            </div>
        `;
    }

    // ── Main Canvas: Unplanned Orders (Two-Layer System) ──────────────────
    function render_unplanned_canvas(exploded = false) {
        if (!exploded) {
            var header = `
                <div style="padding: 20px 25px; border-bottom: 1px solid #e2e8f0; background: #f8fafc; border-radius: 8px 8px 0 0;">
                    <div style="display:flex; justify-content:space-between; align-items:center;">
                        <div>
                            <div class="h3 mb-0" style="font-weight:800; color:#c57b00; margin-bottom:4px;">🗂 Unplanned Confirmed Orders</div>
                            <div class="text-muted" style="font-size:13px;"><b>${state.unplanned_count}</b> orders pending allocation</div>
                        </div>
                        <button class="btn btn-sm btn-primary" id="btn-explode-unplanned" style="font-size:13px; font-weight:600;">
                            💥 Explode Items
                        </button>
                    </div>
                </div>`;

            let rows = state.unplanned_orders.map(so => `
                <tr class="item-row">
                    <td style="padding: 12px;"><a href="/app/sales-order/${so.sales_order}" target="_blank" style="color:#2490ef; font-weight:bold;">${so.sales_order}</a></td>
                    <td style="padding: 12px; font-weight: 500;">${so.customer_name || '—'}</td>
                    <td style="padding: 12px;">${so.region || '—'}</td>
                    <td style="padding: 12px;">${so.payment_terms || '—'}</td>
                    <td style="text-align:right; padding: 12px; font-weight:bold; font-size:14.5px;">${format_currency(so.grand_total, 'KES')}</td>
                    <td style="text-align:center; padding: 12px;">${frappe.datetime.str_to_user(so.transaction_date)}</td>
                </tr>
            `).join('');

            let table = `
                <div style="padding: 20px;">
                    <table class="table table-bordered table-hover" style="margin-bottom:0; font-size:13px; min-width:960px; background:#fff;">
                        <thead class="bg-light">
                            <tr>
                                <th style="padding:12px; color:#475569; font-weight:700;">Sales Order</th>
                                <th style="padding:12px; color:#475569; font-weight:700;">Customer</th>
                                <th style="padding:12px; color:#475569; font-weight:700;">Region</th>
                                <th style="padding:12px; color:#475569; font-weight:700;">Payment Terms</th>
                                <th style="text-align:right; padding:12px; color:#475569; font-weight:700;">Total Amount</th>
                                <th style="text-align:center; padding:12px; color:#475569; font-weight:700;">Order Date</th>
                            </tr>
                        </thead>
                        <tbody>${rows || `<tr><td colspan="6" class="text-center text-muted" style="padding:30px; font-size:15px;">No unplanned orders available. All caught up!</td></tr>`}</tbody>
                    </table>
                </div>`;
                
            $('#lp-canvas').html(header + table);

            $('#btn-explode-unplanned').on('click', function() {
                $('#lp-canvas').html(`<div class="text-muted" style="padding:40px; text-align:center;"><div class="spinner-border spinner-border-sm"></div> &nbsp; <span style="font-size:14.5px;">Exploding Items & Evaluating Bins…</span></div>`);
                frappe.call({
                    method: 'nexus_supply_chain.nexus_supply_chain.page.nexus_dispatch_intelligence.nexus_dispatch_intelligence.get_exploded_unplanned_items',
                    callback: function(r) {
                        if (r.message) {
                            state.unplanned_exploded_items = r.message.items || [];
                            render_unplanned_canvas(true);
                        }
                    }
                });
            });
            
        } else {
            var header = `
                <div style="padding: 20px 25px; border-bottom: 1px solid #e2e8f0; background: #f8fafc; border-radius: 8px 8px 0 0;">
                    <div style="display:flex; justify-content:space-between; align-items:center;">
                        <div>
                            <div class="h3 mb-0" style="font-weight:800; color:#c57b00; margin-bottom:4px;">💥 Exploded Unplanned Items</div>
                            <div class="text-muted" style="font-size:13px;">Item-level breakdown with real-time Bin Availability</div>
                        </div>
                        <button class="btn btn-sm btn-default" id="btn-back-unplanned" style="font-size:13px; font-weight:600;">
                            ← Back to Orders
                        </button>
                    </div>
                </div>`;

            let rows = state.unplanned_exploded_items.map(item => {
                let bal_color = item.balance < 0 ? 'color:#c0392b;' : 'color:#27ae60;';
                return `
                <tr class="item-row">
                    <td style="padding: 12px;"><b style="font-size:13px;">${item.item_code}</b></td>
                    <td style="padding: 12px; font-weight: 500;">${item.item_name || ''}</td>
                    <td style="padding: 12px;"><a href="/app/sales-order/${item.sales_order}" target="_blank" style="color:#2490ef; font-weight:bold;">${item.sales_order}</a></td>
                    <td style="padding: 12px;">${item.customer_name || '—'}</td>
                    <td style="text-align:right; padding: 12px; font-weight:bold;">${item.required_qty}</td>
                    <td style="text-align:right; padding: 12px;">${item.actual_bin || 0}</td>
                    <td class="bal-cell" style="text-align:right; padding: 12px; font-weight:bold; font-size:14.5px; ${bal_color}">${item.balance}</td>
                </tr>
            `}).join('');

            let table = `
                <div style="padding: 20px;">
                    <table class="table table-bordered table-hover" id="exploded-table" style="margin-bottom:0; font-size:13px; min-width:960px; background:#fff;">
                        <thead class="bg-light">
                            <tr>
                                <th style="padding:12px; color:#475569; font-weight:700;">Item Code</th>
                                <th style="padding:12px; color:#475569; font-weight:700;">Item Name</th>
                                <th style="padding:12px; color:#475569; font-weight:700;">Sales Order</th>
                                <th style="padding:12px; color:#475569; font-weight:700;">Customer</th>
                                <th style="text-align:right; padding:12px; color:#475569; font-weight:700;">Req. Qty</th>
                                <th style="text-align:right; padding:12px; color:#475569; font-weight:700;">Available Qty (Bin)</th>
                                <th class="sort-bal-header" data-sort="none" style="text-align:right; padding:12px; cursor:pointer; user-select:none; background:#e9ecef; color:#475569; font-weight:700;" title="Click to sort by Balance">
                                    Balance <span class="sort-icon text-muted">↕</span>
                                </th>
                            </tr>
                        </thead>
                        <tbody id="exploded-tbody">${rows || `<tr><td colspan="7" class="text-center text-muted" style="padding:30px; font-size:15px;">No items found.</td></tr>`}</tbody>
                    </table>
                </div>`;

            $('#lp-canvas').html(header + table);

            $('#btn-back-unplanned').on('click', function() {
                render_unplanned_canvas(false);
            });

            $('#lp-canvas').on('click', '.sort-bal-header', function() {
                var th = $(this);
                var currentSort = th.data('sort') || 'none';
                var tbody = $('#exploded-tbody');
                var rows = tbody.find('tr.item-row').toArray();

                rows.sort(function(a, b) {
                    var valA = parseFloat($(a).find('.bal-cell').text().replace(/[^\d.\-]/g, '')) || 0;
                    var valB = parseFloat($(b).find('.bal-cell').text().replace(/[^\d.\-]/g, '')) || 0;
                    return currentSort === 'desc' ? valA - valB : valB - valA; 
                });

                var newSort = currentSort === 'desc' ? 'asc' : 'desc';
                th.data('sort', newSort);
                th.find('.sort-icon').text(newSort === 'asc' ? '↑' : '↓').removeClass('text-muted').css('color', '#2490ef');

                tbody.empty().append(rows);
            });
        }
    }

    // ── Main Canvas (Load Plan Audit) ─────────────────────────────────────
    function render_canvas() {
        if (!state.active_lp || state.active_lp === 'unplanned') return;

        let s = state.audit_summary;
        let margin_diff = (s.delivered_margin_perc || 0) - (s.planned_margin_perc || 0);
        let trend_icon = margin_diff >= 0 ? '<i class="fa fa-arrow-up text-success"></i>' : '<i class="fa fa-arrow-down text-danger"></i>';
        let trend_color = margin_diff >= 0 ? '#10b981' : '#ef4444';

        let header = `
            <div style="padding: 20px 25px; border-bottom: 1px solid #e2e8f0; background: #f8fafc; border-radius: 8px 8px 0 0;">
                <div style="display:flex; justify-content:space-between; align-items:flex-start; margin-bottom:20px;">
                    <div>
                        <div class="h3" style="font-weight:800; color:#0f172a; margin-bottom:4px;">${state.active_lp}</div>
                        <div class="text-muted" style="font-size:13px;">Financial & Dispatch Execution Audit</div>
                    </div>
                    <div style="text-align:right; background:#fff; padding:10px 15px; border:1px solid #e2e8f0; border-radius:6px; box-shadow:0 1px 2px rgba(0,0,0,0.05);">
                        <div style="font-size:11px; font-weight:700; color:#64748b; margin-bottom:2px;">TRUE DELIVERED MARGIN</div>
                        <div style="font-size:20px; font-weight:800; color:#0f172a;">
                            ${(s.delivered_margin_perc || 0).toFixed(2)}% 
                            <span style="font-size:13px; color:${trend_color}; margin-left:5px;">${trend_icon} ${Math.abs(margin_diff).toFixed(2)}% vs Plan</span>
                        </div>
                    </div>
                </div>

                <!-- Sub Metrics Strip (Segmented for Clarity) -->
                <div style="display:flex; gap:30px; align-items: center;">
                    
                    <!-- Group 1: Ordered / Billed Reference -->
                    <div style="display:flex; gap:25px;">
                        <div>
                            <div style="font-size:10px; font-weight:700; color:#64748b; letter-spacing:0.5px;">TOTAL REVENUE</div>
                            <div style="font-size:15px; font-weight:700; color:#1e293b;">${format_currency(s.total_revenue, 'KES')}</div>
                        </div>
                        <div>
                            <div style="font-size:10px; font-weight:700; color:#64748b; letter-spacing:0.5px;">INVOICED VALUE</div>
                            <div style="font-size:15px; font-weight:700; color:#38bdf8;">${format_currency(s.total_invoiced_value, 'KES')}</div>
                        </div>
                    </div>
                    
                    <div style="height: 30px; width: 2px; background-color: #cbd5e1;"></div>
                    
                    <!-- Group 2: True Physical Execution & Profit -->
                    <div style="display:flex; gap:25px;">
                        <div>
                            <div style="font-size:10px; font-weight:700; color:#64748b; letter-spacing:0.5px;">DELIVERED VALUE</div>
                            <div style="font-size:15px; font-weight:700; color:#10b981;">${format_currency(s.total_delivered_value, 'KES')}</div>
                        </div>
                        <div>
                            <div style="font-size:10px; font-weight:700; color:#64748b; letter-spacing:0.5px;">DELIVERED COGS</div>
                            <div style="font-size:15px; font-weight:700; color:#ef4444;">${format_currency(s.delivered_market_cogs, 'KES')}</div>
                        </div>
                        <div>
                            <div style="font-size:10px; font-weight:700; color:#64748b; letter-spacing:0.5px;">DELIVERED GROSS MARGIN</div>
                            <div style="font-size:15px; font-weight:700; color:#0f172a;">${format_currency(s.delivered_gross_margin, 'KES')}</div>
                        </div>
                    </div>

                </div>
            </div>
        `;

        let rows = '';
        state.audit_sos.forEach(row => {
            
            // 3-Bar Financial Progress Math (Retained purely for CEO Shilling Dashboard)
            let revenue = parseFloat(row.revenue) || 1; 
            let pct_delivered = (parseFloat(row.delivered_value) / revenue) * 100;
            let pct_invoiced = (parseFloat(row.invoiced_value) / revenue) * 100;

            // Cap at 100% for UI purposes
            if (pct_delivered > 100) pct_delivered = 100;
            if (pct_invoiced > 100) pct_invoiced = 100;

            let d_color = pct_delivered === 100 ? '#10b981' : (pct_delivered > 0 ? '#f59e0b' : '#e2e8f0');
            let b_color = pct_invoiced === 100 ? '#38bdf8' : (pct_invoiced > 0 ? '#7dd3fc' : '#e2e8f0');

            rows += `
                <tr>
                    <td style="padding:12px;">
                        <a href="/app/sales-order/${row.sales_order}" target="_blank" style="font-weight:700; color:#2490ef;">${row.sales_order}</a><br>
                        <span style="font-size:11px; color:#64748b;">${row.customer_name}</span>
                    </td>
                    <td style="padding:12px; font-weight: 500;">${row.region || '—'}</td>
                    <td style="padding:12px; font-size:12px; color:#475569;">${row.payment_terms || '—'}</td>
                    <td style="padding:12px; text-align:center;">
                        <div style="font-weight:800; font-size: 14px; color:#0f172a;">${format_currency(row.revenue, 'KES')}</div>
                    </td>
                    <td style="padding:12px; text-align:center;">
                        <div style="font-weight:700; color:#ef4444;">${format_currency(row.delivered_cogs, 'KES')}</div>
                        <div style="font-size:11px; font-weight:700; color:#10b981;">Margin: ${(row.delivered_margin_perc || 0).toFixed(1)}%</div>
                    </td>
                    <td style="padding:12px; width: 260px;">
                        <!-- Revenue Base Bar -->
                        <div style="display:flex; justify-content:space-between; font-size:10px; font-weight:700; margin-bottom:2px; color:#475569;">
                            <span>Ordered</span> <span>100%</span>
                        </div>
                        <div style="background:#f1f5f9; height:4px; border-radius:2px; margin-bottom:6px;">
                            <div style="background:#cbd5e1; height:100%; width:100%;"></div>
                        </div>
                        
                        <!-- Delivered Execution Bar -->
                        <div style="display:flex; justify-content:space-between; font-size:10px; font-weight:700; margin-bottom:2px; color:#10b981;">
                            <span>Delivered</span> <span>${pct_delivered.toFixed(0)}%</span>
                        </div>
                        <div style="background:#f1f5f9; height:4px; border-radius:2px; margin-bottom:6px;">
                            <div style="background:${d_color}; height:100%; width:${pct_delivered}%;"></div>
                        </div>
                        
                        <!-- Invoiced Execution Bar -->
                        <div style="display:flex; justify-content:space-between; font-size:10px; font-weight:700; margin-bottom:2px; color:#38bdf8;">
                            <span>Invoiced</span> <span>${pct_invoiced.toFixed(0)}%</span>
                        </div>
                        <div style="background:#f1f5f9; height:4px; border-radius:2px;">
                            <div style="background:${b_color}; height:100%; width:${pct_invoiced}%;"></div>
                        </div>
                    </td>
                </tr>
            `;
        });

        let table = `
            <div style="padding: 20px;">
                <table class="table table-hover table-bordered" style="font-size:13px; margin-bottom:0; border:1px solid #e2e8f0; background:#fff;">
                    <thead style="background:#f8fafc; border-bottom:2px solid #e2e8f0;">
                        <tr>
                            <th style="padding:12px; color:#475569; font-weight:700;">Sales Order</th>
                            <th style="padding:12px; color:#475569; font-weight:700;">Region</th>
                            <th style="padding:12px; color:#475569; font-weight:700;">Payment Terms</th>
                            <th style="padding:12px; text-align:center; color:#475569; font-weight:700;">Ordered Revenue</th>
                            <th style="padding:12px; text-align:center; color:#475569; font-weight:700;">Delivered COGS & Margin</th>
                            <th style="padding:12px; color:#475569; font-weight:700;">Financial Execution</th>
                        </tr>
                    </thead>
                    <tbody>${rows || `<tr><td colspan="6" class="text-center text-muted" style="padding:30px;">No SO data available.</td></tr>`}</tbody>
                </table>
            </div>`;

        $('#lp-canvas').html(header + table);
    }

    // ── Boot ─────────────────────────────────────────────────────────────
    frappe.model.with_doctype('Sales Order', function() {
        fetch_all_data();
    });
};