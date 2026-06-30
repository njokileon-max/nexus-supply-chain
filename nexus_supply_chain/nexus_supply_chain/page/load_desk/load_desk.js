// apps/nexus_supply_chain/nexus_supply_chain/page/load_desk/load_desk.js

frappe.pages['load_desk'].on_page_load = function(wrapper) {

    var page = frappe.ui.make_app_page({
        parent: wrapper,
        title: __('Nexus Load Desk'),
        single_column: true
    });

    // ── Global State ──────────────────────────────────────────────────────
    var state = {
        load_plans:               [],
        sales_orders:             [],
        unplanned_orders:         [],
        unplanned_exploded_items: [],
        unplanned_count:          0,
        active_lp:                null,
        date_filter:              'today',
        sort_order:               'desc',
        custom_start:             '',
        custom_end:               ''
    };

    // ── Page Layout ───────────────────────────────────────────────────────
    $(page.main).html(`
        <div style="display:flex; gap:20px; height:90vh; padding-top:15px;">

            <!-- SIDEBAR -->
            <div style="width:315px; flex-shrink:0; overflow-y:auto;
                        border-right:1px solid #d1d8dd; padding-right:15px;
                        display:flex; flex-direction:column; gap:0;">

                <!-- Styled Text Buttons matching the screenshot -->
                <div style="display:flex; gap:8px; margin-bottom:15px;">
                    <button id="btn-bulk-loading" class="btn btn-sm btn-default" style="flex:1; font-weight: 600; font-size: 13px; padding: 6px; background: #f4f5f6; color: #2490ef; border: 1px solid #e2e8f0;">
                        <i class="fa fa-truck me-1"></i> Bulk Loading
                    </button>
                    <button id="btn-bulk-packing" class="btn btn-sm btn-default" style="flex:1; font-weight: 600; font-size: 13px; padding: 6px; background: #f4f5f6; color: #2490ef; border: 1px solid #e2e8f0;">
                        <i class="fa fa-box me-1"></i> Bulk Packing
                    </button>
                </div>

                <!-- Unplanned Orders Sidebar Card -->
                <div id="unplanned-card-container"></div>

                <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:10px;">
                    <div class="h5 mb-0">Load Plans</div>
                    <button id="btn-refresh-plans" class="btn btn-xs btn-default" title="Refresh list">↺ Refresh</button>
                </div>
                
                <!-- Filter & Sort UI -->
                <div style="margin-bottom:10px; padding:10px; background:#f8f9fa; border-radius:4px; border:1px solid #d1d8dd;">
                    <div style="display:flex; gap:5px; margin-bottom:5px;">
                        <select id="lp-date-filter" class="form-control form-control-sm" style="flex:1;">
                            <option value="today" selected>Today</option>
                            <option value="yesterday">Yesterday</option>
                            <option value="all">All Dates</option>
                            <option value="custom">Custom Range</option>
                        </select>
                        <button id="btn-sort-date" class="btn btn-sm btn-default" title="Toggle Sort">
                            ⬇ Newest
                        </button>
                    </div>
                    <div id="custom-date-wrap" style="display:none; flex-direction:column; gap:5px;">
                        <input type="date" id="lp-date-start" class="form-control form-control-sm" placeholder="Start Date">
                        <input type="date" id="lp-date-end" class="form-control form-control-sm" placeholder="End Date">
                    </div>
                </div>

                <div id="lp-list"><div class="text-muted small">Loading…</div></div>
            </div>

            <!-- MAIN CANVAS -->
            <div id="lp-canvas" style="flex:1; overflow-y:auto;">
                <div class="text-muted" style="margin-top:20vh; text-align:center;">
                    <svg class="icon icon-xl"><use href="#icon-dashboard"></use></svg><br><br>
                    <span style="font-size: 16px;">Select a Load Plan to oversee Sales Orders and issue Delivery Notes.</span>
                </div>
            </div>

        </div>
    `);

    // ── Sidebar UI Bindings ───────────────────────────────────────────────
    $('#lp-date-filter').on('change', function() {
        state.date_filter = $(this).val();
        $('#custom-date-wrap').toggle(state.date_filter === 'custom');
        render_sidebar();
    });

    $('#lp-date-start, #lp-date-end').on('change', function() {
        state.custom_start = $('#lp-date-start').val();
        state.custom_end = $('#lp-date-end').val();
        render_sidebar();
    });

    $('#btn-sort-date').on('click', function() {
        state.sort_order = state.sort_order === 'desc' ? 'asc' : 'desc';
        $(this).html(state.sort_order === 'desc' ? '⬇ Newest' : '⬆ Oldest');
        render_sidebar();
    });

    $('#btn-refresh-plans').on('click', function() {
        fetch_initial_data(() => {
            if (state.active_lp === 'unplanned') {
                render_unplanned_canvas(false);
            } else if (state.active_lp) {
                let lp = state.load_plans.find(l => l.name === state.active_lp);
                if (lp) select_load_plan(lp);
            }
        });
    });

    // ── Sidebar Logic & Rendering ─────────────────────────────────────────
    function filter_and_sort_lps(lps) {
        let filtered = lps.filter(lp => {
            if (!lp.creation) return true;
            let lp_date_str = lp.creation.split(' ')[0]; 
            let today = frappe.datetime.get_today();
            let yesterday = frappe.datetime.add_days(today, -1);

            if (state.date_filter === 'today') return lp_date_str === today;
            if (state.date_filter === 'yesterday') return lp_date_str === yesterday;
            if (state.date_filter === 'custom') {
                if (state.custom_start && lp_date_str < state.custom_start) return false;
                if (state.custom_end && lp_date_str > state.custom_end) return false;
                return true;
            }
            return true;
        });

        filtered.sort((a, b) => {
            let d1 = new Date(a.creation).getTime();
            let d2 = new Date(b.creation).getTime();
            return state.sort_order === 'desc' ? d2 - d1 : d1 - d2;
        });

        return filtered;
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

        let processed_lps = filter_and_sort_lps(state.load_plans);
        var pending_lps = processed_lps.filter(l => !['Partially Dispatched', 'Fully Dispatched'].includes(l.dispatch_status));
        var dispatched_lps = processed_lps.filter(l => ['Partially Dispatched', 'Fully Dispatched'].includes(l.dispatch_status));

        var html = '';

        if (pending_lps.length) {
            html += `
    <div style="font-size:13px; font-weight:800; color:#1a5276; letter-spacing:.8px; text-transform:uppercase; background:#d6eaf8; padding:6px 10px; border-radius:4px; border-left:4px solid #2490ef; margin-bottom:8px; margin-top:6px;">
        🚛 ACTIVE (${pending_lps.length})
    </div>`;
            pending_lps.forEach(lp => html += _lp_card_html(lp));
        }

        if (dispatched_lps.length) {
            html += `
    <div style="font-size:13px; font-weight:800; color:#155724; letter-spacing:.8px; text-transform:uppercase; background:#c3e6cb; padding:6px 10px; border-radius:4px; border-left:4px solid #28a745; margin-top:14px; margin-bottom:8px;">
        ✅ DISPATCHED (${dispatched_lps.length})
    </div>`;
            dispatched_lps.forEach(lp => html += _lp_card_html(lp));
        }

        if (!html) html = '<div class="text-muted small">No Load Plans match criteria.</div>';

        $('#lp-list').html(html);
        
        $('#lp-list .lp-card').on('click', function() {
            var name = $(this).data('name');
            var lp = state.load_plans.find(l => l.name === name);
            if (lp) select_load_plan(lp);
        });

        // Group-Level Print Buttons
        $('#lp-list .action-print-group').on('click', function(e) {
            e.stopPropagation();
            let group = $(this).data('group');
            let print_type = $(this).data('type');
            
            let current_processed_lps = filter_and_sort_lps(state.load_plans);
            let target_lps = group === 'active' 
                ? current_processed_lps.filter(l => !['Partially Dispatched', 'Fully Dispatched'].includes(l.dispatch_status))
                : current_processed_lps.filter(l => ['Partially Dispatched', 'Fully Dispatched'].includes(l.dispatch_status));
            
            let names_array = target_lps.map(l => l.name);
            if(names_array.length > 0) {
                trigger_print(names_array, print_type);
            }
        });
    }

    function _lp_card_html(lp) {
        var is_active = state.active_lp === lp.name;
        var disp_status = lp.dispatch_status || 'Pending';
        var is_dispatched = ['Partially Dispatched', 'Fully Dispatched'].includes(disp_status);
        var created_date = lp.creation ? frappe.datetime.str_to_user(lp.creation.split(' ')[0]) : '';

        let fill_bg = '';
        let percentage_display = '';
        
        // Dynamic Progress Bar Painting using precise quantity percentage calculated in Python
        if (is_dispatched) {
            let pct = parseFloat(lp.delivered_percentage) || 0; 
            if (disp_status === 'Fully Dispatched') pct = 100;
            
            let fill_color = disp_status === 'Fully Dispatched' ? '#d4edda' : '#fdebd0';
            
            fill_bg = `linear-gradient(to right, ${fill_color} ${pct}%, #fff ${pct}%)`;
            percentage_display = `<span style="font-size:10px; color:#64748b; font-weight:700;">${pct.toFixed(0)}% DELIVERED</span>`;
        } else {
            fill_bg = is_active ? '#f1f5f9' : '#fff';
        }

        var border_color = is_active ? '#94a3b8' : '#e2e8f0';
        var left_color = is_dispatched ? (disp_status === 'Fully Dispatched' ? '#28a745' : '#e67e22') : (lp.docstatus === 1 ? '#2490ef' : '#adb5bd');
        var res_badge = (!is_dispatched && lp.reservation_status) ? get_reservation_badge(lp.reservation_status) : '';
        
        let badge_class = 'badge-secondary';
        if(disp_status === 'Fully Dispatched') badge_class = 'badge-success';
        if(disp_status === 'Partially Dispatched') badge_class = 'badge-warning';

        return `
            <div class="lp-card" data-name="${lp.name}"
                 style="border:1px solid ${border_color}; box-shadow:0 1px 3px rgba(0,0,0,0.04);
                        border-left:4px solid ${left_color}; margin-bottom:10px; background:${fill_bg};
                        border-radius:6px; transition: all 0.2s ease; cursor:pointer; padding:10px 12px;">
                <div style="display:flex; justify-content:space-between; align-items:flex-start; margin-bottom:4px;">
                    <div style="font-weight:700; font-size:13px; color:#1e293b;">${lp.name}</div>
                    <div style="font-size:10px; color:#64748b;">${created_date}</div>
                </div>
                <div class="text-muted" style="font-size:11px; margin-bottom:5px;">
                    ${lp.vehicle_type || '—'} · ${lp.transport_mode || '—'}
                </div>
                <div style="display:flex; justify-content:space-between; align-items:center;">
                    <div style="display:flex; gap:5px; flex-wrap:wrap;">
                        <span class="badge ${badge_class}" style="font-size:10px;">${disp_status}</span>
                        ${res_badge}
                    </div>
                    ${percentage_display}
                </div>
            </div>`;
    }

    // ── Main Canvas: Unplanned Orders (Two-Layer System) ──────────────────
    function render_unplanned_canvas(exploded = false) {
        if (!exploded) {
            // Layer 1: High Level Sales Orders View
            var header = `
                <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:16px;">
                    <div>
                        <div class="h4 mb-0" style="font-weight:700; color:#c57b00;">🗂 Unplanned Confirmed Orders</div>
                        <div class="text-muted mt-1" style="font-size:13px;"><b>${state.unplanned_count}</b> orders pending allocation</div>
                    </div>
                    <button class="btn btn-sm btn-primary" id="btn-explode-unplanned" style="font-size:13px; font-weight:600;">
                        💥 Explode Items
                    </button>
                </div>`;

            let rows = state.unplanned_orders.map(so => `
                <tr class="item-row">
                    <td style="padding: 12px;"><a href="/app/sales-order/${so.sales_order}" target="_blank" style="color:#2490ef; font-weight:bold;">${so.sales_order}</a></td>
                    <td style="padding: 12px; font-weight: 500;">${so.customer_name || '—'}</td>
                    <td style="padding: 12px;">${so.region || '—'}</td>
                    <td style="text-align:right; padding: 12px; font-weight:bold; font-size:14.5px;">${format_currency(so.grand_total, 'KES')}</td>
                    <td style="text-align:center; padding: 12px;">${frappe.datetime.str_to_user(so.transaction_date)}</td>
                </tr>
            `).join('');

            let table = `
                <div style="overflow-x:auto; background: #fff; border: 1px solid #d1d8dd; border-radius: 4px;">
                    <table class="table table-bordered" style="margin-bottom:0; font-size:14.5px; min-width:960px;">
                        <thead class="bg-light">
                            <tr>
                                <th style="padding:12px;">Sales Order</th>
                                <th style="padding:12px;">Customer</th>
                                <th style="padding:12px;">Region</th>
                                <th style="text-align:right; padding:12px;">Total Amount</th>
                                <th style="text-align:center; padding:12px;">Order Date</th>
                            </tr>
                        </thead>
                        <tbody>${rows || `<tr><td colspan="5" class="text-center text-muted" style="padding:30px; font-size:15px;">No unplanned orders available. All caught up!</td></tr>`}</tbody>
                    </table>
                </div>`;
                
            $('#lp-canvas').html(header + table);

            // Bind Explosion Button
            $('#btn-explode-unplanned').on('click', function() {
                $('#lp-canvas').html(`<div class="text-muted" style="padding:40px; text-align:center;"><div class="spinner-border spinner-border-sm"></div> &nbsp; <span style="font-size:14.5px;">Exploding Items…</span></div>`);
                frappe.call({
                    method: 'nexus_supply_chain.nexus_supply_chain.page.load_desk.load_desk.get_exploded_unplanned_items',
                    callback: function(r) {
                        if (r.message) {
                            state.unplanned_exploded_items = r.message.items || [];
                            render_unplanned_canvas(true);
                        }
                    }
                });
            });
            
        } else {
            // Layer 2: Exploded Items View
            var header = `
                <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:16px;">
                    <div>
                        <div class="h4 mb-0" style="font-weight:700; color:#c57b00;">💥 Exploded Unplanned Items</div>
                        <div class="text-muted mt-1" style="font-size:13px;">Item-level breakdown of all unplanned orders</div>
                    </div>
                    <button class="btn btn-sm btn-default" id="btn-back-unplanned" style="font-size:13px; font-weight:600;">
                        ← Back to Orders
                    </button>
                </div>`;

            let rows = state.unplanned_exploded_items.map(item => {
                let bal_color = item.balance < 0 ? 'color:#c0392b;' : 'color:#27ae60;';
                return `
                <tr class="item-row">
                    <td style="padding: 12px;"><b style="font-size:14.5px;">${item.item_code}</b></td>
                    <td style="padding: 12px; font-weight: 500;">${item.item_name || ''}</td>
                    <td style="padding: 12px;"><a href="/app/sales-order/${item.sales_order}" target="_blank" style="color:#2490ef; font-weight:bold;">${item.sales_order}</a></td>
                    <td style="padding: 12px;">${item.customer_name || '—'}</td>
                    <td style="text-align:right; padding: 12px; font-weight:bold;">${item.required_qty}</td>
                    <td style="text-align:right; padding: 12px;">${item.actual_bin || 0}</td>
                    <td class="bal-cell" style="text-align:right; padding: 12px; font-weight:bold; font-size:15px; ${bal_color}">${item.balance}</td>
                </tr>
            `}).join('');

            let table = `
                <div style="overflow-x:auto; background: #fff; border: 1px solid #d1d8dd; border-radius: 4px;">
                    <table class="table table-bordered" id="exploded-table" style="margin-bottom:0; font-size:14.5px; min-width:960px;">
                        <thead class="bg-light">
                            <tr>
                                <th style="padding:12px;">Item Code</th>
                                <th style="padding:12px;">Item Name</th>
                                <th style="padding:12px;">Sales Order</th>
                                <th style="padding:12px;">Customer</th>
                                <th style="text-align:right; padding:12px;">Req. Qty</th>
                                <th style="text-align:right; padding:12px;">Available Qty (Bin)</th>
                                <th class="sort-bal-header" data-sort="none" style="text-align:right; padding:12px; cursor:pointer; user-select:none; background:#e9ecef;" title="Click to sort by Balance">
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

            // Sorting Logic via Balance Header click
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

    // ── Main Canvas (Load Plan Sales Orders) ──────────────────────────────
    function render_canvas() {
        if (!state.active_lp || state.active_lp === 'unplanned') return;

        var header = `
            <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:16px; flex-wrap:wrap; gap:10px;">
                <div class="h4 mb-0" style="font-weight:700;">${state.active_lp} - Sales Orders</div>
                
                <div style="display:flex; gap:8px;">
                    <button class="btn btn-sm btn-default btn-print-packing" data-lp="${state.active_lp}" style="font-size:13px; font-weight:600;">
                        <i class="fa fa-print text-muted me-1"></i> Print Packing List
                    </button>
                    <button class="btn btn-sm btn-default btn-print-loading" data-lp="${state.active_lp}" style="font-size:13px; font-weight:600;">
                        <i class="fa fa-truck text-muted me-1"></i> Print Loading Sheet
                    </button>
                </div>
            </div>`;

        var rows = '';
        state.sales_orders.forEach(function(row) {
            let is_soft = row.reservation_status === 'Soft';
            let is_dispatched = row.so_dispatched;
            let btn_state = is_soft ? 'disabled' : '';
            let btn_class = is_soft ? 'btn-default' : 'btn-primary';
            
            let action_btn = is_dispatched ? 
                `<span class="text-success fw-bold"><i class="fa fa-check"></i> Manifested</span>` :
                `<button class="btn btn-sm ${btn_class} action-create-dn" data-so="${row.sales_order}" ${btn_state}>Create DN</button>`;

            // 1. Quantity Fulfillment Math & Formatting
            let ord_qty = parseFloat(row.so_ordered_qty) || 0;
            let del_qty = parseFloat(row.so_delivered_qty) || 0;
            let pct = parseFloat(row.so_qty_perc) || 0;
            
            // Dispatch floor logic: Pure Green if perfectly satisfied, Normal Bold Black if short.
            let fulfill_color = pct >= 99.99 ? '#27ae60' : '#000000';
            let fulfill_weight = '700'; // Normal bold text weight applied universally as requested
            
            let fulfill_html = `
                <div class="fulfill-cell" data-pct="${pct}" style="font-weight:${fulfill_weight}; color:${fulfill_color}; font-size:14.5px; white-space:nowrap; text-align:center;">
                    ${del_qty} / ${ord_qty}
                    <div style="font-size:11.5px; margin-top:2px;">(${pct.toFixed(0)}%)</div>
                </div>`;

            rows += `
                <tr style="${is_dispatched ? 'opacity:0.6; background:#f0faf2;' : ''}" class="so-row">
                    <td style="padding: 12px; vertical-align: middle;">
                        <a href="/app/sales-order/${row.sales_order}" target="_blank" style="color:#2490ef; font-weight:bold;">
                            ${row.sales_order}
                        </a>
                    </td>
                    <td style="padding: 12px; font-weight: 500; vertical-align: middle;">${row.customer_name}</td>
                    <td style="padding: 12px; vertical-align: middle;">${row.region || '—'}</td>
                    
                    <td style="text-align:center; padding: 12px; vertical-align: middle;">${fulfill_html}</td>
                    
                    <td style="text-align:center; padding: 12px; vertical-align: middle;">${get_reservation_badge(row.reservation_status)}</td>
                    <td style="text-align:center; padding: 12px; vertical-align: middle;">${get_delivery_badge(row.delivery_status)}</td>
                    <td style="text-align:center; padding: 12px; vertical-align: middle;">${action_btn}</td>
                </tr>`;
        });

        var table = `
            <div style="overflow-x:auto; background: #fff; border: 1px solid #d1d8dd; border-radius: 4px;">
                <table id="canvas-table" class="table table-bordered" style="margin-bottom:0; font-size:14.5px; min-width:960px;">
                    <thead class="bg-light">
                        <tr>
                            <th style="padding:12px; color:#475569; font-weight:700;">Sales Order</th>
                            <th style="padding:12px; color:#475569; font-weight:700;">Customer</th>
                            <th style="padding:12px; color:#475569; font-weight:700;">Region</th>
                            <th class="sort-fulfill-header" data-sort="none" style="text-align:center; padding:12px; color:#475569; font-weight:700; cursor:pointer; user-select:none; background:#e9ecef;" title="Click to sort by Fulfillment">
                                SO Fulfillment (Qty) <span class="sort-icon text-muted">↕</span>
                            </th>
                            <th style="text-align:center; padding:12px; color:#475569; font-weight:700;">Reservation Status</th>
                            <th style="text-align:center; padding:12px; color:#475569; font-weight:700;">Dispatch Status</th>
                            <th style="text-align:center; padding:12px; color:#475569; font-weight:700;">Action</th>
                        </tr>
                    </thead>
                    <tbody>${rows || `<tr><td colspan="7" class="text-center text-muted" style="padding:20px;">No Orders Found.</td></tr>`}</tbody>
                </table>
            </div>`;

        $('#lp-canvas').html(header + table);

        // Fulfillment percentage sorting logic (0-Lag DOM manipulation)
        $('#lp-canvas').off('click', '.sort-fulfill-header').on('click', '.sort-fulfill-header', function() {
            var th = $(this);
            var currentSort = th.data('sort') || 'none';
            var tbody = $('#canvas-table tbody');
            var rows = tbody.find('tr.so-row').toArray();

            if (rows.length === 0) return; // Guard for empty states

            rows.sort(function(a, b) {
                var valA = parseFloat($(a).find('.fulfill-cell').data('pct')) || 0;
                var valB = parseFloat($(b).find('.fulfill-cell').data('pct')) || 0;

                if (currentSort === 'desc') {
                    return valA - valB; // Ascending (Lowest to Highest / Worst Shortages first)
                } else {
                    return valB - valA; // Descending (Highest to Lowest / Fully Satisfied first)
                }
            });

            var newSort = currentSort === 'desc' ? 'asc' : 'desc';
            th.data('sort', newSort);
            th.find('.sort-icon').text(newSort === 'asc' ? '↑' : '↓').removeClass('text-muted').css('color', '#2490ef');

            tbody.empty().append(rows);
        });

        $('#lp-canvas .btn-print-packing').on('click', function() {
            trigger_print([$(this).data('lp')], 'packing_list');
        });

        $('#lp-canvas .btn-print-loading').on('click', function() {
            trigger_print([$(this).data('lp')], 'loading_sheet');
        });

        $('#lp-canvas .action-create-dn').on('click', function() {
            var so = $(this).data('so');
            frappe.confirm(`Draft a Delivery Note for <b>${so}</b> matching the dispatch floor's confirmed quantities?`, () => {
                frappe.call({
                    method: 'nexus_supply_chain.nexus_supply_chain.page.load_desk.load_desk.create_dn_from_so',
                    args: { sales_order: so, load_plan_name: state.active_lp },
                    freeze: true,
                    freeze_message: "Drafting & Mapping quantities...",
                    callback: function(r) {
                        if(r.message && r.message.name) {
                            frappe.show_alert({message: `Delivery Note ${r.message.name} Drafted. Opening...`, indicator: 'green'});
                            window.open(frappe.urllib.get_full_url('/app/delivery-note/' + r.message.name), '_blank');
                            select_load_plan(state.load_plans.find(l => l.name === state.active_lp)); 
                        }
                    }
                });
            });
        });
    }

    // ── Backend API Calls ─────────────────────────────────────────────────
    function fetch_initial_data(callback) {
        frappe.call({
            method: 'nexus_supply_chain.nexus_supply_chain.page.load_desk.load_desk.get_load_desk_plans',
            callback: function(r) {
                state.load_plans = r.message || [];
                
                // Fetch Unplanned Orders Count & Sales Orders
                frappe.call({
                    method: 'nexus_supply_chain.nexus_supply_chain.page.load_desk.load_desk.get_unplanned_confirmed_orders',
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

    function select_load_plan(lp) {
        state.active_lp = lp.name;
        render_sidebar();
        $('#lp-canvas').html(`<div class="text-muted" style="padding:40px; text-align:center;"><div class="spinner-border spinner-border-sm"></div> &nbsp; <span style="font-size:14.5px;">Fetching Orders…</span></div>`);

        frappe.call({
            method: 'nexus_supply_chain.nexus_supply_chain.page.load_desk.load_desk.get_load_plan_sales_orders',
            args: { load_plan_name: lp.name },
            callback: function(r) {
                if (r.message) {
                    state.sales_orders = r.message.sales_orders || [];
                }
                render_canvas();
            }
        });
    }

    // ── HTML Print Engine (High-Contrast Ink-Friendly Styling) ────────────
    function trigger_print(load_plans_array, print_type) {
        frappe.call({
            method: 'nexus_supply_chain.nexus_supply_chain.page.load_desk.load_desk.get_print_data',
            args: { load_plans: JSON.stringify(load_plans_array), print_type: print_type },
            freeze: true,
            freeze_message: "Rendering Document...",
            callback: function(r) {
                if(r.message) {
                    let html = build_print_html(r.message, print_type);
                    let printWindow = window.open('', '_blank');
                    printWindow.document.write(html);
                    printWindow.document.close();
                }
            }
        });
    }

    function build_print_html(data, print_type) {
        let date_str = frappe.datetime.str_to_user(frappe.datetime.get_today());
        
        // CSS engineered for clean, ink-saving printing with solid borders
        let style = `
            <style>
                @media all {
                    body { font-family: "Helvetica Neue", Arial, sans-serif; background-color: #f4f5f6; margin: 0; padding: 0; color: #000; line-height: 1.4; }
                    
                    .print-toolbar { 
                        background-color: #ffffff; border-bottom: 1px solid #d1d8dd; padding: 12px 24px; 
                        display: flex; justify-content: flex-end; align-items: center; 
                        position: sticky; top: 0; z-index: 1000; box-shadow: 0 1px 3px rgba(0,0,0,0.05);
                    }
                    .print-toolbar span { flex-grow: 1; font-weight: bold; color: #333; font-size: 15px; }
                    .print-btn { 
                        background-color: #2490ef; color: white; border: none; padding: 8px 16px; 
                        border-radius: 4px; font-weight: 600; cursor: pointer; font-size: 13px; transition: background 0.2s;
                    }
                    .print-btn:hover { background-color: #1a74c2; }
                    
                    .page-content { 
                        background-color: #ffffff; margin: 30px auto; padding: 40px; 
                        max-width: 210mm; min-height: 297mm; box-shadow: 0 2px 10px rgba(0,0,0,0.1); 
                    }

                    .page-header { border-bottom: 2px solid #000; padding-bottom: 10px; margin-bottom: 20px; }
                    h1 { margin: 0 0 5px 0; font-size: 26px; font-weight: 900; text-transform: uppercase; letter-spacing: 0.5px; }
                    .meta { color: #222; font-size: 13px; font-weight: 500; }
                    
                    table.print-table { width: 100%; border-collapse: collapse; margin-bottom: 20px; font-size: 13px; }
                    table.print-table th, table.print-table td { border: 1px solid #000; padding: 10px 12px; text-align: left; vertical-align: middle; color: #000; }
                    table.print-table th { background-color: transparent !important; color: #000 !important; font-weight: 800; border: 2px solid #000; border-bottom: 3px solid #000; text-transform: uppercase; }
                    table.print-table tr { page-break-inside: avoid; }
                    
                    .qty { text-align: right !important; font-weight: 900; width: 100px; font-size: 14px; }
                    .index-col { width: 40px; text-align: center !important; font-weight: bold; }
                    .cust-header { font-weight: 900; background-color: transparent !important; border: 2px solid #000; padding: 10px 12px; border-bottom: none; font-size: 15px; text-transform: uppercase; margin-top: 30px; }
                    .signature-line { display: flex; justify-content: flex-start; gap: 80px; margin-top: 25px; margin-bottom: 40px; padding-bottom: 20px; border-bottom: 2px dashed #000; page-break-inside: avoid; font-weight: bold;}
                }
                
                @media print {
                    body { background-color: #ffffff; }
                    .print-toolbar { display: none !important; }
                    .page-content { box-shadow: none; margin: 0; padding: 0; max-width: 100%; border: none; }
                    @page { size: A4 portrait; margin: 15mm; }
                    thead { display: table-header-group; }
                    tfoot { display: table-footer-group; }
                }
            </style>`;

        let title_str = print_type.replace('_', ' ').toUpperCase();
        let company_header = `<h2>${data.company || 'Nexus'}</h2>`;
        let body = '';

        if (print_type === 'loading_sheet') {
            body += `<div class="page-header">
                        ${company_header}
                        <h1>${title_str}</h1>
                        <div class="meta">Date: ${date_str} &nbsp;|&nbsp; Orders: <b>${data.orders_count}</b> &nbsp;|&nbsp; Items: <b>${data.total_items}</b></div>
                     </div>
                     <table class="print-table">
                        <thead>
                            <tr>
                                <th class="index-col">#</th>
                                <th>Item Code</th>
                                <th>Description</th>
                                <th class="qty">Qty</th>
                            </tr>
                        </thead>
                        <tbody>`;
            data.items.forEach((item, idx) => {
                body += `<tr>
                            <td class="index-col">${idx+1}</td>
                            <td><b>${item.item_code}</b></td>
                            <td>${item.description}</td>
                            <td class="qty">${parseFloat(item.qty).toFixed(2)}</td>
                         </tr>`;
            });
            body += `</tbody></table>`;
            
        } else if (print_type === 'packing_list') {
            body += `<div class="page-header">
                        ${company_header}
                        <h1>${title_str}</h1>
                        <div class="meta">Date: ${date_str} &nbsp;|&nbsp; Customers: <b>${data.customers.length}</b></div>
                     </div>`;
                     
            data.customers.forEach(cust => {
                body += `<div class="cust-header">${cust.customer}</div>
                         <table class="print-table" style="margin-bottom: 10px;">
                            <thead>
                                <tr>
                                    <th class="index-col">#</th>
                                    <th>Item Code</th>
                                    <th>Description</th>
                                    <th class="qty">Qty</th>
                                </tr>
                            </thead>
                            <tbody>`;
                let subtotal = 0;
                cust.items.forEach((item, idx) => {
                    subtotal += parseFloat(item.qty);
                    body += `<tr>
                                <td class="index-col">${idx+1}</td>
                                <td><b>${item.item_code}</b></td>
                                <td>${item.description}</td>
                                <td class="qty">${parseFloat(item.qty).toFixed(2)}</td>
                             </tr>`;
                });
                body += `<tr>
                            <td colspan="3" style="text-align:right; font-weight:900; border-top: 3px solid #000; font-size:14px;">Subtotal</td>
                            <td class="qty" style="border-top: 3px solid #000; font-size:15px;">${subtotal.toFixed(2)}</td>
                         </tr>`;
                body += `</tbody></table>
                         <div class="signature-line">
                             <div>Received by: ________________________</div>
                             <div>Signature: ________________________</div>
                         </div>`;
            });
        }

        return `<!DOCTYPE html>
                <html lang="en">
                <head>
                    <meta charset="UTF-8">
                    <title>${title_str}</title>
                    ${style}
                </head>
                <body>
                    <div class="print-toolbar">
                        <span>📄 Document Preview</span>
                        <button class="print-btn" onclick="window.print()">
                            <svg style="width:14px; height:14px; margin-right:5px; vertical-align:middle; fill:currentColor;" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 512 512"><path d="M128 0C92.7 0 64 28.7 64 64v96h64V64H354.7L384 93.3V160h64V93.3c0-17-6.7-33.3-18.7-45.3L400 18.7C388 6.7 371.7 0 354.7 0H128zM512 256c0-35.3-28.7-64-64-64H64c-35.3 0-64 28.7-64 64v153.6c0 17.7 14.3 32 32 32h32v40c0 35.3 28.7 64 64 64H384c35.3 0 64-28.7 64-64v-40h32c17.7 0 32-14.3 32-32V256zM160 396.8h192v67.2H160v-67.2zm192-32H160V288h192v76.8z"/></svg> 
                            Print
                        </button>
                    </div>
                    
                    <div class="page-content">
                        ${body}
                    </div>
                </body>
                </html>`;
    }

    // ── Bulk Modal Binding (Fixed: buttons live outside #lp-list) ─────────
    $(page.main).on('click', '#btn-bulk-loading, #btn-bulk-packing', function() {
        let print_type = $(this).attr('id') === 'btn-bulk-loading' ? 'loading_sheet' : 'packing_list';
        let modal_title = print_type === 'loading_sheet' ? 'Master Loading Sheet' : 'Master Packing List';
        
        let active_plans = state.load_plans.filter(l => !['Fully Dispatched'].includes(l.dispatch_status));
        
        if (active_plans.length === 0) {
            frappe.msgprint("No active Load Plans available for bulk printing.");
            return;
        }

        let select_all_html = `
            <div style="margin-bottom:12px; padding-bottom:12px; border-bottom:2px solid #e2e8f0;">
                <label style="font-weight:700; cursor:pointer; font-size:15px; color:#2490ef;">
                    <input type="checkbox" id="bulk-select-all"> &nbsp;Select All Active Plans
                </label>
            </div>`;

        let options = active_plans.map(lp => `
            <div style="margin-bottom:10px;">
                <label style="font-weight:500; cursor:pointer; font-size:14.5px; display:flex; align-items:center; gap:8px;">
                    <input type="checkbox" class="bulk-lp-check" value="${lp.name}">
                    <span><b>${lp.name}</b> <span class="text-muted" style="font-size:12px;">(${lp.vehicle_type || 'No Vehicle'} · ${lp.transport_mode || '—'})</span></span>
                </label>
            </div>`).join('');

        let d = new frappe.ui.Dialog({
            title: `📋 Select Plans — ${modal_title}`,
            fields: [{ fieldtype: 'HTML', fieldname: 'selector_html' }],
            primary_action_label: `🖨 Generate ${print_type === 'loading_sheet' ? 'Loading Sheet' : 'Packing List'}`,
            primary_action() {
                let selected = [];
                d.$wrapper.find('.bulk-lp-check:checked').each(function() {
                    selected.push($(this).val());
                });
                if (selected.length === 0) {
                    frappe.msgprint("Please select at least one Load Plan.");
                    return;
                }
                trigger_print(selected, print_type);
                d.hide();
            }
        });

        d.fields_dict.selector_html.$wrapper.html(`
            <div style="max-height:350px; overflow-y:auto; padding:15px; border:1px solid #d1d8dd; border-radius:6px; background:#fff;">
                ${select_all_html}
                ${options}
            </div>`);

        // Select All logic
        d.$wrapper.on('change', '#bulk-select-all', function() {
            d.$wrapper.find('.bulk-lp-check').prop('checked', $(this).is(':checked'));
        });

        d.$wrapper.on('change', '.bulk-lp-check', function() {
            let total   = d.$wrapper.find('.bulk-lp-check').length;
            let checked = d.$wrapper.find('.bulk-lp-check:checked').length;
            d.$wrapper.find('#bulk-select-all').prop('checked', total === checked);
        });

        d.show();
    });

    // ── Visual helpers ────────────────────────────────────────────────────
    function get_reservation_badge(status) {
        var map = {
            'Reserved': 'badge-success',
            'Partially Reserved': 'badge-warning',
            'Soft': 'badge-secondary',
            'Consumed': 'badge-primary'
        };
        return `<span class="badge ${map[status] || 'badge-secondary'}">${status || 'Soft'}</span>`;
    }

    function get_delivery_badge(status) {
        var map = {
            'Fully Delivered': ['badge-success', 'Delivered'],
            'Partially Delivered': ['badge-warning', 'Partial'],
            'Pending': ['badge-secondary','Pending'],
            'On Hold': ['badge-danger', 'On Hold'],
            'Closed': ['badge-dark', 'Closed'],
            'Dispatched': ['badge-success', 'Dispatched']
        };
        var e = map[status] || ['badge-secondary', status || 'Pending'];
        return `<span class="badge ${e[0]}" style="font-size:12px;">${e[1]}</span>`;
    }

    // ── Boot ─────────────────────────────────────────────────────────────
    fetch_initial_data();
};