// apps/nexus_supply_chain/nexus_supply_chain/nexus_supply_chain/page/dispatch_status/dispatch_status.js

frappe.pages['dispatch_status'].on_page_load = function(wrapper) {

    var page = frappe.ui.make_app_page({
        parent: wrapper,
        title: __('Nexus Sort & Dispatch Board'),
        single_column: true
    });

    // ── Global State ──────────────────────────────────────────────────────
    var state = {
        load_plans:      [],
        items:           [],
        current_state:   {},
        competing_plans: {},
        active_lp:       null,
        unattended_count: 0,
        // New Sidebar Filter & Sort State
        date_filter:     'all',
        sort_order:      'desc',
        custom_start:    '',
        custom_end:      ''
    };

    // ── Page Layout ───────────────────────────────────────────────────────
    $(page.main).html(`
        <div style="display:flex; gap:20px; height:90vh; padding-top:15px;">

            <!-- SIDEBAR -->
            <div style="width:295px; flex-shrink:0; overflow-y:auto;
                        border-right:1px solid #d1d8dd; padding-right:15px;
                        display:flex; flex-direction:column; gap:0;">

                <!-- Special overview cards -->
                <div id="special-cards" style="margin-bottom:10px;">
                    <div id="card-all-planned"
                         style="cursor:pointer; padding:10px 12px; margin-bottom:7px;
                                background:#f0f6ff; border:1px solid #b6d0f7;
                                border-left:4px solid #2490ef; border-radius:4px;">
                        <div style="font-weight:700; font-size:12px; color:#2490ef;">📦 All Planned Items</div>
                        <div class="text-muted" style="font-size:11px; margin-top:2px;">
                            Items across all active load plans (no D-Note)
                        </div>
                    </div>
                    <div id="card-unplanned"
                         style="cursor:pointer; padding:10px 12px; margin-bottom:7px;
                                background:#fff8e6; border:1px solid #f5c542;
                                border-left:4px solid #e67e22; border-radius:4px;">
                        <div style="font-weight:700; font-size:12px; color:#c57b00;">🗂 Unplanned Orders</div>
                        <div class="text-muted" style="font-size:11px; margin-top:2px;">
                            Confirmed SOs not yet in any load plan
                        </div>
                    </div>
                </div>

                <!-- Unattended count banner -->
                <div id="unattended-banner" style="margin-bottom:10px; display:none;">
                    <div style="background:#fff3cd; border:1px solid #ffc107; border-radius:4px;
                                padding:7px 11px; font-size:12px; color:#856404;">
                        ⚠️ <span id="unattended-text"></span> load plan(s) pending dispatch
                    </div>
                </div>

                <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:10px;">
                    <div class="h5 mb-0">Load Plans</div>
                    <button id="btn-refresh-plans" class="btn btn-xs btn-default" title="Refresh list">↺ Refresh</button>
                </div>
                
                <!-- Filter & Sort UI -->
                <div style="margin-bottom:10px; padding:10px; background:#f8f9fa; border-radius:4px; border:1px solid #d1d8dd;">
                    <div style="display:flex; gap:5px; margin-bottom:5px;">
                        <select id="lp-date-filter" class="form-control form-control-sm" style="flex:1;">
                            <option value="all">All Dates</option>
                            <option value="today">Today</option>
                            <option value="yesterday">Yesterday</option>
                            <option value="custom">Custom Range</option>
                        </select>
                        <button id="btn-sort-date" class="btn btn-sm btn-default" title="Sort by Creation Date">
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
                    <svg class="icon icon-xl"><use href="#icon-truck"></use></svg><br><br>
                    Select a Load Plan to view and allocate stock.
                </div>
            </div>

        </div>
    `);

    // ── Sidebar UI Bindings ───────────────────────────────────────────────
    $('#lp-date-filter').on('change', function() {
        state.date_filter = $(this).val();
        if (state.date_filter === 'custom') {
            $('#custom-date-wrap').show();
        } else {
            $('#custom-date-wrap').hide();
        }
        render_sidebar();
    });

    $('#lp-date-start').on('change', function() {
        state.custom_start = $(this).val();
        render_sidebar();
    });

    $('#lp-date-end').on('change', function() {
        state.custom_end = $(this).val();
        render_sidebar();
    });

    $('#btn-sort-date').on('click', function() {
        if (state.sort_order === 'desc') {
            state.sort_order = 'asc';
            $(this).html('⬆ Oldest');
        } else {
            state.sort_order = 'desc';
            $(this).html('⬇ Newest');
        }
        render_sidebar();
    });


    // ── Shared helpers ────────────────────────────────────────────────────

    /** Pill search bar; id defaults to 'popup-search-bar' */
    function make_search_bar(placeholder, id) {
        id = id || 'popup-search-bar';
        return `
            <div style="position:relative; margin-bottom:14px;">
                <span style="position:absolute; left:10px; top:50%; transform:translateY(-50%);
                             font-size:14px; color:#aaa; pointer-events:none;">🔍</span>
                <input type="text" id="${id}"
                       placeholder="${placeholder || 'Search…'}"
                       autocomplete="off"
                       style="width:100%; padding:8px 12px 8px 34px; border:1px solid #d1d8dd;
                              border-radius:20px; font-size:13px; outline:none;
                              box-shadow:0 1px 4px rgba(0,0,0,0.07); transition:border 0.2s;"
                       onfocus="this.style.borderColor='#2490ef'"
                       onblur="this.style.borderColor='#d1d8dd'">
            </div>`;
    }

    /** Balance filter bar */
    function make_balance_filter(targetTableId, balanceColIdx) {
        return `
            <div id="balance-filter-wrap" style="display:flex; gap:6px; margin-bottom:12px; align-items:center;">
                <span style="font-size:12px; color:#6c757d; font-weight:600;">Filter:</span>
                <button class="btn btn-xs bal-filter-btn active-filter"
                        data-filter="all"
                        style="border-radius:12px; padding:3px 12px; font-size:11px;
                               background:#2490ef; color:#fff; border:1px solid #2490ef;">
                    All
                </button>
                <button class="btn btn-xs bal-filter-btn"
                        data-filter="surplus"
                        style="border-radius:12px; padding:3px 12px; font-size:11px;
                               background:#fff; color:#27ae60; border:1px solid #27ae60;">
                    Surplus ✓
                </button>
                <button class="btn btn-xs bal-filter-btn"
                        data-filter="shortfall"
                        style="border-radius:12px; padding:3px 12px; font-size:11px;
                               background:#fff; color:#c0392b; border:1px solid #c0392b;">
                    Shortfall ✗
                </button>
            </div>`;
    }

    /** Wire up balance filter buttons */
    function bind_balance_filter(wrapper, tableId, balColIdx) {
        wrapper.on('click', '.bal-filter-btn', function() {
            wrapper.find('.bal-filter-btn').each(function() {
                $(this).css({ background: '#fff', color: $(this).data('filter') === 'surplus' ? '#27ae60'
                                                       : $(this).data('filter') === 'shortfall' ? '#c0392b'
                                                       : '#6c757d',
                              'border-color': $(this).data('filter') === 'surplus' ? '#27ae60'
                                            : $(this).data('filter') === 'shortfall' ? '#c0392b'
                                            : '#adb5bd' });
            });
            var f = $(this).data('filter');
            $(this).css({ background: f === 'surplus' ? '#27ae60'
                                     : f === 'shortfall' ? '#c0392b' : '#2490ef',
                          color: '#fff',
                          'border-color': f === 'surplus' ? '#27ae60'
                                        : f === 'shortfall' ? '#c0392b' : '#2490ef' });

            wrapper.find('#' + tableId + ' tbody tr').each(function() {
                var cell_text = $(this).find('.bal-cell').text().trim();
                var val = parseFloat(cell_text.replace(/[^\d.\-]/g, '')) || 0;
                if (f === 'all')           $(this).show();
                else if (f === 'surplus')  $(this).toggle(val >= 0);
                else                       $(this).toggle(val < 0);
            });
        });
    }

    function format_k(val) {
        let num = parseFloat(val) || 0;
        if (num === 0) return '0';
        return (num / 1000).toFixed(1).replace(/\.0$/, '') + 'k';
    }

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
        let processed_lps = filter_and_sort_lps(state.load_plans);

        let html = '';
        
        // 1. SPLIT INTO GROUPS BASED ON BACKEND STATUS
        var pending_lps = processed_lps.filter(l => !['Partially Dispatched', 'Fully Dispatched'].includes(l.dispatch_status));
        var dispatched_lps = processed_lps.filter(l => ['Partially Dispatched', 'Fully Dispatched'].includes(l.dispatch_status));

        // 2. RENDER ACTIVE (PENDING) GROUP
        if (pending_lps.length) {
            html += `<div style="font-size:13px; font-weight:800; color:#6c757d; letter-spacing:.6px; text-transform:uppercase; margin-bottom:8px; margin-top:6px;">ACTIVE</div>`;
            pending_lps.forEach(lp => html += _lp_card_html(lp));
        }

        // 3. RENDER DISPATCHED GROUP
        if (dispatched_lps.length) {
            html += `<div style="font-size:13px; font-weight:800; color:#28a745; letter-spacing:.6px; text-transform:uppercase; margin-top:14px; margin-bottom:8px; border-top:1px solid #d1d8dd; padding-top:10px;">DISPATCHED</div>`;
            dispatched_lps.forEach(lp => html += _lp_card_html(lp));
        }

        if (!html) {
            html = '<div class="text-muted small">No Load Plans match criteria.</div>';
        }

        $('#lp-list').html(html);
        $('#lp-list .lp-card').on('click', function() {
            var name = $(this).data('name');
            var lp   = state.load_plans.find(l => l.name === name);
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
        
        // 4. DYNAMIC PROGRESS BAR PAINTING
        if (is_dispatched) {
            // Pulls the precise physical percentage calculated in Python
            let pct = parseFloat(lp.delivered_percentage) || 0; 
            if (disp_status === 'Fully Dispatched') pct = 100;
            
            // Financial Color Triage
            let fill_color = disp_status === 'Fully Dispatched' ? '#d4edda' : '#fdebd0';
            left_color = disp_status === 'Fully Dispatched' ? '#28a745' : '#ca6f1e';
            
            // Uses CSS linear-gradient to fill the card background exactly to the percentage
            fill_bg = `linear-gradient(to right, ${fill_color} ${pct}%, #fff ${pct}%)`;
            percentage_display = `<span style="font-size:10px; color:#64748b; font-weight:700;">${pct.toFixed(0)}% DELIVERED</span>`;
        } else {
            fill_bg = is_active ? '#f1f5f9' : '#fff';
            left_color = get_status_color(lp);
        }

        var docstatus_badge = lp.docstatus === 0
            ? '<span class="badge badge-warning">Draft</span>'
            : '<span class="badge badge-primary">Submitted</span>';

        // Dispatch status badges
        var disp_badge_class = 'badge-secondary';
        if (disp_status === 'Fully Dispatched') disp_badge_class = 'badge-success';
        if (disp_status === 'Partially Dispatched') disp_badge_class = 'badge-warning';
        if (disp_status === 'Production/Loading') disp_badge_class = 'badge-info';

        var disp_badge = `<span class="badge ${disp_badge_class}">${is_dispatched ? '✓ ' : ''}${disp_status}</span>`;
        if(disp_status === 'Pending') disp_badge = `<span class="badge badge-secondary">⏳ Pending</span>`;

        var res_badge = '';
        if (!is_dispatched && lp.reservation_status) {
            res_badge = get_reservation_badge(lp.reservation_status);
        }

        var border_color = is_active ? '#94a3b8' : '#e2e8f0';

        return `
            <div class="lp-card" data-name="${lp.name}"
                 style="border:1px solid ${border_color}; box-shadow:0 1px 3px rgba(0,0,0,0.04);
                        border-left:4px solid ${left_color}; cursor:pointer;
                        padding:10px 12px; margin-bottom:10px; background:${fill_bg};
                        border-radius:6px; transition: all 0.2s ease;">
                <div style="display:flex; justify-content:space-between; align-items:flex-start; margin-bottom:4px;">
                    <div style="font-weight:700; font-size:13px; color:#1e293b;">${lp.name}</div>
                    <div style="font-size:10px; color:#64748b;">${created_date}</div>
                </div>
                <div class="text-muted" style="font-size:11px; margin-bottom:5px;">
                    ${lp.vehicle_type || '—'} · ${lp.transport_mode || '—'}
                </div>
                <div style="display:flex; justify-content:space-between; align-items:center;">
                    <div style="display:flex; gap:5px; flex-wrap:wrap;">
                        ${docstatus_badge}
                        ${disp_badge}
                        ${res_badge}
                    </div>
                    ${percentage_display}
                </div>
            </div>`;
    }

    // ── Unattended banner ─────────────────────────────────────────────────
    function update_unattended_banner() {
        var count = state.load_plans.filter(l => !['Partially Dispatched', 'Fully Dispatched'].includes(l.dispatch_status)).length;
        state.unattended_count = count;
        if (count > 0) {
            $('#unattended-text').text(count);
            $('#unattended-banner').show();
        } else {
            $('#unattended-banner').hide();
        }
    }

    // ── Main Canvas ───────────────────────────────────────────────────────
    function render_canvas() {
        if (!state.active_lp) return;

        var dispatched_items = state.items.filter(i =>  i.so_dispatched);
        var total     = Object.keys(state.current_state).length;
        var confirmed = Object.values(state.current_state).filter(v => v.state === 'Confirmed').length;
        var reserved  = Object.values(state.current_state).filter(v => v.state === 'Reserved').length;
        var pending   = total - confirmed - reserved;

        var header = `
            <div style="display:flex; justify-content:space-between; align-items:center;
                        margin-bottom:12px; flex-wrap:wrap; gap:10px;">
                <div>
                    <div class="h4 mb-1">${state.active_lp}</div>
                    <div class="text-muted small">
                        ${total} items &nbsp;·&nbsp;
                        <span class="text-success">${confirmed} Confirmed</span> &nbsp;·&nbsp;
                        <span style="color:#e67e22;">${reserved} Reserved</span> &nbsp;·&nbsp;
                        <span class="text-muted">${pending} Pending</span>
                        ${dispatched_items.length
                            ? `&nbsp;·&nbsp;<span class="text-success">${dispatched_items.length} Dispatched</span>`
                            : ''}
                    </div>
                </div>
                <div style="display:flex; gap:8px; flex-wrap:wrap;">
                    <button class="btn btn-xs btn-default" id="btn-refresh-stock">↺ Refresh Stock</button>
                    <button class="btn btn-xs btn-default" id="btn-unreserve-all">Unreserve All</button>
                    <button class="btn btn-xs btn-warning" id="btn-reserve-all">Reserve All</button>
                    <button class="btn btn-xs btn-primary" id="btn-confirm-all">Confirm All</button>
                </div>
            </div>`;

        var search = make_search_bar('Search item, Sales Order…', 'canvas-search');
        var rows = _build_canvas_rows(state.items);

        var table = `
            <div style="overflow-x:auto;">
                <table id="canvas-table" class="table table-bordered table-sm"
                       style="font-size:13px; min-width:960px;">
                    <thead class="bg-light">
                        <tr>
                            <th>Item</th>
                            <th>Sales Order</th>
                            <th class="sort-fulfill-header" data-sort="none" style="text-align:center; cursor:pointer; user-select:none; background:#e9ecef;" title="Click to sort by Fulfillment">
                                SO Fulfillment (Qty) <span class="sort-icon text-muted">↕</span>
                            </th>
                            <th style="text-align:center;">D-Note Status</th>
                            <th style="text-align:right;">Req. Qty</th>
                            <th style="text-align:right;">Bin Stock</th>
                            <th style="text-align:right; color:#e67e22;">Others Reserved</th>
                            <th style="text-align:right;">Virtual Avail</th>
                            <th style="text-align:right;">Shortfall / Competing</th>
                            <th style="text-align:center;">Status</th>
                            <th>Actions</th>
                        </tr>
                    </thead>
                    <tbody id="canvas-tbody">${rows}</tbody>
                </table>
            </div>`;

        $('#lp-canvas').html(header + search + table);
        bind_canvas_events();

        $('#canvas-search').on('input', function() {
            var q = $(this).val().toLowerCase().trim();
            $('#canvas-table tbody tr').each(function() {
                $(this).toggle(!q || $(this).text().toLowerCase().includes(q));
            });
        });
    }

    function _build_canvas_rows(items) {
        var rows = '';
        items.forEach(function(row) {
            
            // 1. Quantity Fulfillment Math & Formatting
            let ord_qty = parseFloat(row.so_ordered_qty) || 0;
            let del_qty = parseFloat(row.so_delivered_qty) || 0;
            let pct = parseFloat(row.so_qty_perc) || 0;
            
            // Dispatch floor logic: Pure Green if perfectly satisfied, Heavy Black Bold if short.
            let fulfill_color = pct >= 99.99 ? '#27ae60' : '#000000';
            let fulfill_weight = pct >= 99.99 ? '700' : '900'; // 900 for extra bold black
            
            let fulfill_html = `
                <div class="fulfill-cell" data-pct="${pct}" style="font-weight:${fulfill_weight}; color:${fulfill_color}; font-size:14px; white-space:nowrap;">
                    ${del_qty} / ${ord_qty}
                    <div style="font-size:11px; margin-top:2px;">(${pct.toFixed(0)}%)</div>
                </div>`;

            if (row.so_dispatched) {
                rows += `
                    <tr style="opacity:0.6; background:#f0faf2;">
                        <td>
                            <div style="font-weight:600; font-size:13px;">${row.item_code}</div>
                            <div class="text-muted" style="font-size:11px;">${row.item_name || ''}</div>
                        </td>
                        <td style="vertical-align:middle;">
                            <a href="/app/sales-order/${row.sales_order}" target="_blank"
                               style="color:#2490ef; font-size:12px; font-weight:bold;">${row.sales_order}</a>
                        </td>
                        <td style="text-align:center; vertical-align:middle;">${fulfill_html}</td>
                        <td style="text-align:center; vertical-align:middle;">
                            <span class="badge badge-success" style="font-size:10px;">Dispatched</span>
                        </td>
                        <td style="text-align:right; font-size:14px; vertical-align:middle;"><b>${row.required_qty}</b></td>
                        <td style="text-align:right; font-size:14px; vertical-align:middle;">${row.actual_bin}</td>
                        <td style="text-align:right; font-size:14px; vertical-align:middle;">—</td>
                        <td style="text-align:right; font-size:14px; vertical-align:middle;">—</td>
                        <td style="text-align:right; vertical-align:middle;">—</td>
                        <td style="text-align:center; vertical-align:middle;">
                            <span class="badge badge-success">Dispatched</span>
                        </td>
                        <td style="vertical-align:middle;"><span class="text-muted small">D-Note Submitted</span></td>
                    </tr>`;
                return;
            }

            var has_shortfall = row.less_amount < 0;
            var row_bg        = has_shortfall ? '#fff8f8' : '';

            var shortfall_cell = has_shortfall
                ? `<span class="text-danger font-weight-bold" style="font-size:14px;">${row.less_amount}</span>`
                : `<span class="text-success" style="font-size:14px;">✓</span>`;

            var competing_html = '';
            if (row.competing && row.competing.length) {
                competing_html = '<div style="margin-top:3px;">' +
                    row.competing.map(c =>
                        `<span class="badge badge-light"
                               style="margin:1px; font-size:10px; cursor:pointer;"
                               data-plan="${c.plan}"
                               title="${c.plan}: ${c.qty} ${c.state}">
                            ${c.plan.split('-').slice(-1)[0]} (${c.qty})
                        </span>`
                    ).join('') + '</div>';
            }

            var actions = '<div class="btn-group">';
            if (row.status !== 'Confirmed') {
                actions += `<button class="btn btn-xs btn-default action-btn"
                                    data-row="${row.item_row_name}"
                                    data-action="Reserved">Reserve</button>`;
                actions += `<button class="btn btn-xs btn-primary action-btn"
                                    data-row="${row.item_row_name}"
                                    data-action="Confirmed">Confirm</button>`;
            }
            if (row.status !== 'Pending') {
                actions += `<button class="btn btn-xs btn-danger action-btn"
                                    data-row="${row.item_row_name}"
                                    data-action="Pending">Unreserve</button>`;
            }
            actions += '</div>';

            rows += `
                <tr style="background:${row_bg};">
                    <td>
                        <div style="font-weight:600; font-size:13px;">${row.item_code}</div>
                        <div class="text-muted" style="font-size:11px;">${row.item_name || ''}</div>
                    </td>
                    <td style="vertical-align:middle;">
                        <a href="/app/sales-order/${row.sales_order}" target="_blank"
                           style="color:#2490ef; font-size:12px; font-weight:bold;">${row.sales_order}</a>
                    </td>
                    <td style="text-align:center; vertical-align:middle;">${fulfill_html}</td>
                    <td style="text-align:center; vertical-align:middle;">${get_delivery_badge(row.delivery_status)}</td>
                    <td style="text-align:right; font-size:14px; vertical-align:middle;"><b>${row.required_qty}</b></td>
                    <td style="text-align:right; font-size:14px; vertical-align:middle;">${row.actual_bin}</td>
                    <td style="text-align:right; font-size:14px; color:#e67e22; vertical-align:middle;">${row.other_reserved || 0}</td>
                    <td style="text-align:right; font-size:14px; vertical-align:middle;">${row.virtual_available}</td>
                    <td style="text-align:right; vertical-align:middle;">${shortfall_cell}${competing_html}</td>
                    <td style="text-align:center; vertical-align:middle;">
                        <span class="badge ${get_item_badge(row.status)}">${row.status}</span>
                    </td>
                    <td style="vertical-align:middle;">${actions}</td>
                </tr>`;
        });
        return rows;
    }

    function bind_canvas_events() {
        
        // Fulfillment percentage sorting logic
        $('#lp-canvas').on('click', '.sort-fulfill-header', function() {
            var th = $(this);
            var currentSort = th.data('sort') || 'none';
            var tbody = $('#canvas-tbody');
            var rows = tbody.find('tr').toArray();

            rows.sort(function(a, b) {
                var valA = parseFloat($(a).find('.fulfill-cell').data('pct')) || 0;
                var valB = parseFloat($(b).find('.fulfill-cell').data('pct')) || 0;

                if (currentSort === 'desc') {
                    return valA - valB; // Switch to Ascending (Lowest to Highest)
                } else {
                    return valB - valA; // Default to Descending (Highest to Lowest)
                }
            });

            var newSort = currentSort === 'desc' ? 'asc' : 'desc';
            th.data('sort', newSort);
            th.find('.sort-icon').text(newSort === 'asc' ? '↑' : '↓').removeClass('text-muted').css('color', '#2490ef');

            tbody.empty().append(rows);
        });

        $('#lp-canvas .action-btn').on('click', function() {
            var row_name = $(this).data('row');
            var action   = $(this).data('action');
            var row      = state.items.find(r => r.item_row_name === row_name);
            if (row) set_row_state(row, action);
        });

        $('#btn-reserve-all').on('click', function() {
            state.items.forEach(function(row) {
                if (!row.so_dispatched && row.status !== 'Confirmed') apply_state(row, 'Reserved');
            });
            flush_and_save();
        });

        $('#btn-confirm-all').on('click', function() {
            state.items.forEach(function(row) {
                if (!row.so_dispatched) apply_state(row, 'Confirmed');
            });
            flush_and_save();
        });

        $('#btn-unreserve-all').on('click', function() {
            frappe.confirm(
                __('Unreserve ALL items in this Load Plan? This frees stock for other plans.'),
                function() {
                    state.items.forEach(function(row) {
                        if (!row.so_dispatched) apply_state(row, 'Pending');
                    });
                    flush_and_save();
                }
            );
        });

        $('#btn-refresh-stock').on('click', function() {
            var lp = state.load_plans.find(l => l.name === state.active_lp);
            if (lp) reload_items_only(lp);
        });

        $('#lp-canvas').on('click', '[data-plan]', function() {
            var plan_name = $(this).data('plan');
            var lp        = state.load_plans.find(l => l.name === plan_name);
            if (lp) select_load_plan(lp);
            else frappe.show_alert({ message: `Load Plan ${plan_name} not in current list.`, indicator: 'orange' });
        });
    }

    // ── State mutation ────────────────────────────────────────────────────
    function apply_state(row, new_status) {
        row.status = new_status;
        if (state.current_state[row.item_row_name]) {
            state.current_state[row.item_row_name].state = new_status;
        }
    }

    function set_row_state(row, new_status) {
        if (new_status === 'Confirmed' && row.less_amount < 0) {
            frappe.show_alert({
                message: `Shortfall for ${row.item_code} — repacking logic will apply.`,
                indicator: 'orange'
            });
        }
        apply_state(row, new_status);
        update_sidebar_status_optimistically();
        render_canvas();
        save_states_to_backend();
    }

    function flush_and_save() {
        update_sidebar_status_optimistically();
        render_canvas();
        save_states_to_backend();
    }

    function update_sidebar_status_optimistically() {
        var total     = Object.keys(state.current_state).length;
        var confirmed = Object.values(state.current_state).filter(v => v.state === 'Confirmed').length;
        var reserved  = Object.values(state.current_state).filter(v => v.state === 'Reserved').length;
        var new_status = (confirmed === total && total > 0) ? 'Reserved'
                       : (confirmed > 0 || reserved > 0)   ? 'Partially Reserved'
                       : 'Soft';
        var lp = state.load_plans.find(l => l.name === state.active_lp);
        if (lp) { lp.reservation_status = new_status; render_sidebar(); }
    }

    // ── Backend calls ─────────────────────────────────────────────────────
    function fetch_load_plans(callback) {
        frappe.call({
            method: 'nexus_supply_chain.nexus_supply_chain.page.dispatch_status.dispatch_status.get_dispatch_board_load_plans',
            callback: function(r) {
                state.load_plans = r.message || [];
                render_sidebar();
                update_unattended_banner();
                if (callback) callback();
            }
        });
    }

    function select_load_plan(lp) {
        state.active_lp = lp.name;
        render_sidebar();
        $('#lp-canvas').html(`
            <div class="text-muted" style="padding:40px; text-align:center;">
                <div class="spinner-border spinner-border-sm" role="status"></div>
                &nbsp; Calculating stock positions…
            </div>`);

        frappe.call({
            method: 'nexus_supply_chain.nexus_supply_chain.page.dispatch_status.dispatch_status.get_load_plan_dispatch_data',
            args: { load_plan_name: lp.name },
            callback: function(r) {
                if (r.message) {
                    state.items           = r.message.items || [];
                    state.current_state   = r.message.current_state || {};
                    state.competing_plans = r.message.competing_plans || {};
                    state.items.forEach(function(item) {
                        if (!item.so_dispatched && !state.current_state[item.item_row_name]) {
                            state.current_state[item.item_row_name] = {
                                item_code:    item.item_code,
                                qty:          item.required_qty,
                                sales_order:  item.sales_order,
                                state:        'Pending'
                            };
                        }
                    });
                }
                render_canvas();
            }
        });
    }

    function reload_items_only(lp) {
        frappe.show_alert({ message: 'Refreshing stock positions…', indicator: 'blue' });
        frappe.call({
            method: 'nexus_supply_chain.nexus_supply_chain.page.dispatch_status.dispatch_status.get_load_plan_dispatch_data',
            args: { load_plan_name: lp.name },
            callback: function(r) {
                if (r.message) {
                    var fresh = r.message.items || [];
                    fresh.forEach(function(fi) {
                        var saved = state.current_state[fi.item_row_name];
                        fi.status = saved ? saved.state : 'Pending';
                    });
                    state.items           = fresh;
                    state.competing_plans = r.message.competing_plans || {};
                }
                render_canvas();
                frappe.show_alert({ message: 'Stock positions updated.', indicator: 'green' });
            }
        });
    }

    function save_states_to_backend() {
        frappe.call({
            method: 'nexus_supply_chain.nexus_supply_chain.page.dispatch_status.dispatch_status.save_item_states',
            args: {
                load_plan_name: state.active_lp,
                item_states:    JSON.stringify(state.current_state)
            },
            callback: function(r) {
                if (r.message && r.message.master_status) {
                    var lp = state.load_plans.find(l => l.name === state.active_lp);
                    if (lp) { lp.reservation_status = r.message.master_status; render_sidebar(); }
                }
            }
        });
    }

    // ── Popup: All Planned Items ──────────────────────────────────────────
    $('#card-all-planned').on('click', function() {
        var d = new frappe.ui.Dialog({
            title: '📦 All Planned Items (Without D-Note)',
            size:  'extra-large',
            fields: [{ fieldtype: 'HTML', fieldname: 'content' }]
        });
        d.fields_dict.content.$wrapper.html(
            '<div style="text-align:center;padding:30px;"><div class="spinner-border spinner-border-sm"></div> Loading…</div>'
        );
        d.show();

        frappe.call({
            method: 'nexus_supply_chain.nexus_supply_chain.page.dispatch_status.dispatch_status.get_all_planned_items_without_dnote',
            callback: function(r) {
                var items = (r.message || {}).items || [];
                d.fields_dict.content.$wrapper.html(
                    _render_popup(items, true /* show_lp_col */)
                );
                _bind_popup_events(d.fields_dict.content.$wrapper, items, true);
            }
        });
    });

    // ── Popup: Unplanned Orders ───────────────────────────────────────────
    $('#card-unplanned').on('click', function() {
        var d = new frappe.ui.Dialog({
            title: '🗂 Unplanned Confirmed Orders',
            size:  'extra-large',
            fields: [{ fieldtype: 'HTML', fieldname: 'content' }]
        });
        d.fields_dict.content.$wrapper.html(
            '<div style="text-align:center;padding:30px;"><div class="spinner-border spinner-border-sm"></div> Loading…</div>'
        );
        d.show();

        frappe.call({
            method: 'nexus_supply_chain.nexus_supply_chain.page.dispatch_status.dispatch_status.get_unplanned_confirmed_orders',
            callback: function(r) {
                var items = (r.message || {}).items || [];
                d.fields_dict.content.$wrapper.html(
                    _render_popup(items, false /* show_lp_col */)
                );
                _bind_popup_events(d.fields_dict.content.$wrapper, items, false);
            }
        });
    });

    // ── Popup renderer ────────────────────────────────────────────────────
    var BALANCE_COL_IDX = 6;

    function _render_popup(items, show_lp_col) {
        if (!items || !items.length) {
            return '<div class="text-muted" style="padding:20px;text-align:center;">No items found.</div>';
        }

        var lp_th = show_lp_col ? '<th>Load Plan</th>' : '<th>Order Date</th>';

        return `
            ${make_search_bar('Search item code, name, Sales Order…', 'popup-search')}
            ${make_balance_filter('popup-table', BALANCE_COL_IDX)}
            <div style="margin-bottom:10px; display:flex; gap:8px; align-items:center;">
                <button id="btn-consolidate"   class="btn btn-sm btn-primary">⊕ Consolidate</button>
                <button id="btn-unconsolidate" class="btn btn-sm btn-default" style="display:none;">≡ Individual</button>
                <span   id="popup-summary-bar" style="display:none; font-size:12px;
                                                       color:#2490ef; margin-left:8px;"></span>
            </div>
            <div style="overflow-x:auto; max-height:55vh; overflow-y:auto;">
                <table id="popup-table" class="table table-bordered table-sm"
                       style="font-size:13px; min-width:680px;">
                    <thead class="bg-light">
                        <tr>
                            <th>Item Code</th>
                            <th>Item Name</th>
                            <th>Sales Order</th>
                            ${lp_th}
                            <th style="text-align:right;">Req. Qty</th>
                            <th style="text-align:right;">Bin Stock</th>
                            <th class="sort-bal-header" data-sort="none" style="text-align:right; cursor:pointer; user-select:none; background:#e9ecef;" title="Click to sort by Balance">
                                Balance <span class="sort-icon text-muted">↕</span>
                            </th>
                        </tr>
                    </thead>
                    <tbody id="popup-tbody">
                        ${_popup_individual_rows(items, show_lp_col)}
                    </tbody>
                </table>
            </div>`;
    }

    function _popup_individual_rows(items, show_lp_col) {
        return items.map(function(item) {
            var balance   = item.balance !== undefined ? item.balance : ((item.actual_bin || 0) - (item.required_qty || 0));
            var bal_style = balance < 0
                ? 'color:#c0392b; font-weight:700;'
                : 'color:#27ae60;';
            var extra_td  = show_lp_col
                ? `<td style="font-size:12px;">${item.load_plan || '—'}</td>`
                : `<td style="font-size:12px;">${item.transaction_date || '—'}</td>`;

            return `
                <tr>
                    <td><b style="font-size:13px;">${item.item_code}</b></td>
                    <td style="font-size:12px;">${item.item_name || ''}</td>
                    <td>
                        <a href="/app/sales-order/${item.sales_order}" target="_blank"
                           style="color:#2490ef; font-size:12px;">${item.sales_order}</a>
                    </td>
                    ${extra_td}
                    <td style="text-align:right; font-size:14px;"><b>${item.required_qty}</b></td>
                    <td style="text-align:right; font-size:14px;">${item.actual_bin || 0}</td>
                    <td class="bal-cell" style="text-align:right; font-size:14px; ${bal_style}">${balance}</td>
                </tr>`;
        }).join('');
    }

    function _popup_consolidated_rows(items) {
        var agg = {};
        items.forEach(function(item) {
            var ic = item.item_code;
            if (!agg[ic]) {
                agg[ic] = {
                    item_code:    ic,
                    item_name:    item.item_name,
                    required_qty: 0,
                    actual_bin:   item.actual_bin || 0,
                    sales_orders: []
                };
            }
            agg[ic].required_qty += (item.required_qty || 0);
            if ((item.actual_bin || 0) > agg[ic].actual_bin) {
                agg[ic].actual_bin = item.actual_bin;
            }
            if (item.sales_order && !agg[ic].sales_orders.includes(item.sales_order)) {
                agg[ic].sales_orders.push(item.sales_order);
            }
        });

        var rows_arr   = Object.values(agg).sort((a, b) => a.item_code.localeCompare(b.item_code));
        var total_req  = 0;
        var shortfalls = 0;

        var html = rows_arr.map(function(item) {
            var balance   = item.actual_bin - item.required_qty;
            var bal_style = balance < 0 ? 'color:#c0392b; font-weight:700;' : 'color:#27ae60;';
            if (balance < 0) shortfalls++;
            total_req += item.required_qty;

            return `
                <tr>
                    <td><b style="font-size:13px;">${item.item_code}</b></td>
                    <td style="font-size:12px;">${item.item_name || ''}</td>
                    <td style="color:#888; font-size:11px;">${item.sales_orders.length} order(s)</td>
                    <td style="color:#888; font-size:11px;">Multiple</td>
                    <td style="text-align:right; font-size:14px;"><b>${item.required_qty}</b></td>
                    <td style="text-align:right; font-size:14px;">${item.actual_bin}</td>
                    <td class="bal-cell" style="text-align:right; font-size:14px; ${bal_style}">${balance}</td>
                </tr>`;
        }).join('');

        var summary = `${rows_arr.length} unique items · Total Req: <b>${total_req}</b> ·
                       <span style="color:#c0392b;">${shortfalls} item(s) with shortfall</span>`;

        return { html: html, summary: summary };
    }

    function _bind_popup_events(wrapper, original_items, show_lp_col) {

        // Text search
        wrapper.on('input', '#popup-search', function() {
            var q = $(this).val().toLowerCase().trim();
            wrapper.find('#popup-table tbody tr').each(function() {
                $(this).toggle(!q || $(this).text().toLowerCase().includes(q));
            });
        });

        // Balance filter
        bind_balance_filter(wrapper, 'popup-table', BALANCE_COL_IDX);

        // Sorting Logic via Balance Header click
        wrapper.on('click', '.sort-bal-header', function() {
            var th = $(this);
            var currentSort = th.data('sort') || 'none';
            var tbody = wrapper.find('#popup-tbody');
            var rows = tbody.find('tr').toArray();

            rows.sort(function(a, b) {
                var valA = parseFloat($(a).find('.bal-cell').text().replace(/[^\d.\-]/g, '')) || 0;
                var valB = parseFloat($(b).find('.bal-cell').text().replace(/[^\d.\-]/g, '')) || 0;

                if (currentSort === 'desc') {
                    return valA - valB; // Switch to Ascending (Lowest to Highest)
                } else {
                    return valB - valA; // Default to Descending (Highest to Lowest)
                }
            });

            var newSort = currentSort === 'desc' ? 'asc' : 'desc';
            th.data('sort', newSort);
            th.find('.sort-icon').text(newSort === 'asc' ? '↑' : '↓').removeClass('text-muted').css('color', '#2490ef');

            tbody.empty().append(rows);
        });

        // Consolidate
        wrapper.on('click', '#btn-consolidate', function() {
            var result = _popup_consolidated_rows(original_items);
            wrapper.find('#popup-tbody').html(result.html);
            wrapper.find('#popup-summary-bar').html(result.summary).show();
            wrapper.find('#btn-consolidate').hide();
            wrapper.find('#btn-unconsolidate').show();
            // reset search, filter, and sort
            wrapper.find('#popup-search').val('');
            wrapper.find('.bal-filter-btn[data-filter="all"]').trigger('click');
            wrapper.find('.sort-bal-header').data('sort', 'none').find('.sort-icon').text('↕').addClass('text-muted').css('color', '');
        });

        // Unconsolidate
        wrapper.on('click', '#btn-unconsolidate', function() {
            wrapper.find('#popup-tbody').html(_popup_individual_rows(original_items, show_lp_col));
            wrapper.find('#popup-summary-bar').hide();
            wrapper.find('#btn-consolidate').show();
            wrapper.find('#btn-unconsolidate').hide();
            // reset search, filter, and sort
            wrapper.find('#popup-search').val('');
            wrapper.find('.bal-filter-btn[data-filter="all"]').trigger('click');
            wrapper.find('.sort-bal-header').data('sort', 'none').find('.sort-icon').text('↕').addClass('text-muted').css('color', '');
        });
    }

    // ── Visual helpers ────────────────────────────────────────────────────
    function get_status_color(lp) {
        if (lp.dispatch_status === 'Fully Dispatched')      return '#28a745';
        if (lp.dispatch_status === 'Partially Dispatched')  return '#e67e22';
        if (lp.reservation_status === 'Reserved')           return '#28a745';
        if (lp.reservation_status === 'Partially Reserved') return '#e67e22';
        if (lp.docstatus === 1)                             return '#2490ef';
        return '#adb5bd';
    }

    function get_reservation_badge(status) {
        var map = {
            'Reserved':           'badge-success',
            'Partially Reserved': 'badge-warning',
            'Soft':               'badge-secondary',
            'Partially Consumed': 'badge-info',
            'Consumed':           'badge-primary'
        };
        return `<span class="badge ${map[status] || 'badge-secondary'}">${status || 'Soft'}</span>`;
    }

    function get_item_badge(status) {
        if (status === 'Confirmed')  return 'badge-success';
        if (status === 'Reserved')   return 'badge-warning';
        if (status === 'Dispatched') return 'badge-success';
        return 'badge-secondary';
    }

    function get_delivery_badge(status) {
        var map = {
            'Fully Delivered':     ['badge-success', 'Delivered'],
            'Partially Delivered': ['badge-warning', 'Partial'],
            'Pending':             ['badge-secondary','Pending'],
            'On Hold':             ['badge-danger',  'On Hold'],
            'Closed':              ['badge-dark',    'Closed'],
            'Dispatched':          ['badge-success', 'Dispatched']
        };
        var e = map[status] || ['badge-secondary', status || 'Pending'];
        return `<span class="badge ${e[0]}" style="font-size:10px;">${e[1]}</span>`;
    }

    // ── Sidebar refresh button ────────────────────────────────────────────
    $('#btn-refresh-plans').on('click', function() {
        var current = state.active_lp;
        fetch_load_plans(function() {
            if (current) {
                var lp = state.load_plans.find(l => l.name === current);
                if (lp) select_load_plan(lp);
            }
        });
    });

    // ── Boot ─────────────────────────────────────────────────────────────
    fetch_load_plans();
};