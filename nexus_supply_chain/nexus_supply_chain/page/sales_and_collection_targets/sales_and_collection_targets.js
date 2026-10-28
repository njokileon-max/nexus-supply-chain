// Copyright (c) 2026, Nexus Supply Chain and contributors
// For license information, please see license.txt

frappe.pages['sales-and-collection-targets'].on_page_load = function(wrapper) {
    var page = frappe.ui.make_app_page({
        parent: wrapper,
        title: 'Executive Control Tower',
        single_column: true
    });

    // Global state for instant sorting/filtering/exporting
    page.nexus_cache = [];
    page.nexus_dates = {};
    page.current_sort = { col: 'sales_person_name', dir: 'asc' };

    page.set_primary_action('Refresh Live Data', () => trigger_fetch(page), 'refresh');
    
    // Custom Checkbox injection in page header with State Management
    let custom_btn = page.add_inner_button('Custom Audit Range', function() {
        toggle_custom_range(page, custom_btn);
    });

    // Phase 5: Client-Side Excel Export Engine
    page.add_inner_button('Export to Excel', function() {
        export_to_excel(page);
    });

    inject_styles();
    build_skeleton(page);
    trigger_fetch(page);
}

function inject_styles() {
    frappe.dom.set_style(`
        /* Skeleton & Toolbars */
        .nexus-toolbar { display: flex; justify-content: space-between; align-items: center; margin-bottom: 20px; padding: 15px; background: #fff; border-radius: 8px; box-shadow: 0 2px 6px rgba(0,0,0,0.04); }
        .nexus-search { padding: 8px 12px; border: 1px solid #d1d8dd; border-radius: 4px; width: 300px; font-size: 13px; }
        .nexus-badge { background: #e2e8f0; color: #1e293b; padding: 4px 10px; border-radius: 20px; font-weight: 700; font-size: 12px; }
        
        /* 9-Card Executive Layout */
        .nexus-kpi-container { display: grid; grid-template-columns: repeat(auto-fit, minmax(320px, 1fr)); gap: 16px; margin-bottom: 24px; }
        .nexus-kpi-card { padding: 20px; border-radius: 8px; color: #fff; box-shadow: 0 4px 10px rgba(0,0,0,0.08); display: flex; flex-direction: column; justify-content: space-between; }
        .kpi-title { font-size: 13px; text-transform: uppercase; font-weight: 700; opacity: 0.9; margin-bottom: 8px; }
        .kpi-value { font-size: 26px; font-weight: 800; }
        .kpi-subtext { font-size: 14.5px; margin-top: 15px; padding-top: 12px; border-top: 1px solid rgba(255,255,255,0.3); font-weight: 800; opacity: 1; text-shadow: 0 1px 2px rgba(0,0,0,0.1); }
        
        .bg-dark { background: #1e293b; }
        .bg-sales { background: #1e3a8a; }
        .bg-deliv { background: #0369a1; }
        .bg-coll { background: #064e3b; }
        .bg-var-pos { background: #0f766e; }
        .bg-var-neg { background: #991b1b; }
        .bg-outst { background: #b45309; }
        .bg-risk { background: #991b1b; }
        .bg-pdc { background: #0f172a; }

        /* Meta Text */
        .nexus-meta-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(250px, 1fr)); gap: 16px; margin-bottom: 16px; padding: 15px; background: #f8fafc; border-radius: 8px; border: 1px solid #e2e8f0; }
        .nexus-meta-item { font-size: 14px; color: #334155; }
        .nexus-meta-item span { font-weight: 800; color: #0f172a; }

        /* Table & DOUBLE-AXIS Scroll Lock Logic */
        .nexus-table-wrapper { width: 100%; max-height: calc(100vh - 350px); overflow-y: auto; overflow-x: auto; border-radius: 8px; border: 1px solid #cbd5e1; background: #fff; position: relative; }
        .nexus-table { width: 100%; border-collapse: separate; border-spacing: 0; font-size: 12px; min-width: 2500px; } /* Expanded for Delivery & PDC */
        .nexus-table th, .nexus-table td { border-bottom: 1px solid #e2e8f0; border-right: 1px solid #e2e8f0; padding: 12px; text-align: right; white-space: nowrap; }
        
        /* Sticky Headers - Y-Axis */
        .nexus-table thead tr:nth-child(1) th { position: sticky; top: 0; background-color: #f1f5f9; font-weight: 800; color: #334155; z-index: 50; border-top: 1px solid #cbd5e1; border-bottom: 2px solid #cbd5e1; }
        .nexus-table thead tr:nth-child(2) th { position: sticky; top: 43px; background-color: #f1f5f9; font-weight: 800; color: #334155; z-index: 49; border-bottom: 2px solid #cbd5e1; cursor: pointer; }
        .nexus-table thead tr:nth-child(2) th:hover { background-color: #e2e8f0; }

        /* Sticky Columns - X-Axis */
        .col-sp { position: sticky; left: 0; width: 220px; min-width: 220px; max-width: 220px; background: #ffffff !important; z-index: 30; text-align: left !important; font-weight: 700; border-right: 1px solid #e2e8f0;}
        .col-terr { position: sticky; left: 220px; width: 140px; min-width: 140px; max-width: 140px; background: #ffffff !important; z-index: 30; text-align: left !important; box-shadow: 4px 0 8px -2px rgba(0,0,0,0.08); border-right: 1px solid #cbd5e1;}
        
        /* The Intersection Lock (Top-Left Corner) */
        .nexus-table thead tr:nth-child(1) th.col-sp, 
        .nexus-table thead tr:nth-child(1) th.col-terr { z-index: 70; background: #f1f5f9 !important; }

        /* Light Professional Percentage Highlights */
        .light-pct-green { background-color: #d1fae5 !important; color: #065f46 !important; font-weight: 800; text-align: center !important; }
        .light-pct-yellow { background-color: #fef3c7 !important; color: #92400e !important; font-weight: 800; text-align: center !important; }
        .light-pct-red { background-color: #fee2e2 !important; color: #991b1b !important; font-weight: 800; text-align: center !important; }
        
        .val-green { color: #15803d; font-weight: 700; }
        .val-red { color: #b91c1c; font-weight: 700; }
        .val-today { color: #0369a1; font-weight: 700; font-style: italic; background: #f0f9ff !important; }
        .text-heavy { font-weight: 800; color: #0f172a; }

        /* Glossary */
        .nexus-glossary { margin-top: 30px; padding: 20px; background: #f8fafc; border-left: 4px solid #334155; border-radius: 4px; font-size: 13px; color: #475569; line-height: 1.6; }
        .nexus-glossary strong { color: #0f172a; }
    `);
}

function build_skeleton(page) {
    $(page.main).html(`
        <div id="custom-date-panel" style="display: none; background: #f1f5f9; padding: 20px; border-radius: 8px; margin-bottom: 20px; border: 1px solid #cbd5e1; border-left: 4px solid #0284c7;">
            <h5 style="margin-top:0; font-weight:700; color: #0f172a;">Custom Ledger Audit Range</h5>
            <div style="display: flex; gap: 15px; align-items: flex-end;">
                <div><label style="font-size:12px; font-weight:600; color:#475569;">From Date</label><br><input type="date" id="nexus-start-date" class="form-control" style="width: 200px;"></div>
                <div><label style="font-size:12px; font-weight:600; color:#475569;">To Date</label><br><input type="date" id="nexus-end-date" class="form-control" style="width: 200px;"></div>
                <button class="btn btn-primary" onclick="window.run_custom_audit(this)">Run Custom Audit</button>
            </div>
        </div>
        
        <div id="dashboard-content">
            <div class="text-center text-muted" style="padding: 60px 0;">
                <div class="spinner-border spinner-border-sm text-primary"></div>
                <span class="ml-2" style="font-weight: 600;">Compiling active ledger arrays...</span>
            </div>
        </div>
    `);

    window.run_custom_audit = function(btn) {
        let start = $('#nexus-start-date').val();
        let end = $('#nexus-end-date').val();
        if(!start || !end) { frappe.msgprint("Please select both dates to run a custom audit."); return; }
        trigger_fetch(page, start, end);
    };
}

function toggle_custom_range(page, btn) {
    let panel = $('#custom-date-panel');
    if(panel.is(':visible')) {
        panel.slideUp();
        $(btn).text('Custom Audit Range');
        $(btn).removeClass('btn-primary').addClass('btn-default');
        
        $('#nexus-start-date').val('');
        $('#nexus-end-date').val('');
        
        trigger_fetch(page); 
    } else {
        panel.slideDown();
        $(btn).text('CURRENT AI ANALYSIS');
        $(btn).removeClass('btn-default').addClass('btn-primary');
        $('#dashboard-content').html('<div class="text-muted text-center" style="padding: 60px; font-size: 15px;">Select dates above and click "Run Custom Audit" to generate historical performance.</div>');
    }
}

function trigger_fetch(page, start_date=null, end_date=null) {
    $('#dashboard-content').html('<div class="text-center" style="padding: 60px;"><div class="spinner-border text-primary"></div><div class="mt-2 text-muted font-weight-bold">Extracting Strict Base-Currency Data...</div></div>');
    
    frappe.call({
        method: "nexus_supply_chain.nexus_supply_chain.page.sales_and_collection_targets.sales_and_collection_targets.get_dashboard_data",
        args: { start_date: start_date, end_date: end_date },
        callback: function(r) {
            if(r.message) {
                page.nexus_cache = r.message.metrics;
                page.nexus_dates = r.message.dates;
                page.active_reps = r.message.active_reps;
                page.is_custom = r.message.is_custom;
                
                render_core_ui(page);
                render_table_body(page);
            }
        }
    });
}

function render_core_ui(page) {
    let data = page.nexus_cache;
    let dates = page.nexus_dates;
    
    // Global Accumulators aligned with exact backend ledger math
    let t_sales_target = data.reduce((sum, row) => sum + row.sales_target, 0);
    let t_sales = data.reduce((sum, row) => sum + row.actual_sales, 0);
    let t_delivered = data.reduce((sum, row) => sum + (row.actual_delivered || 0), 0);
    let global_sales_pct = t_sales_target ? ((t_sales / t_sales_target) * 100).toFixed(1) : 0.0;

    let t_coll_target = data.reduce((sum, row) => sum + row.collection_target, 0);
    let t_coll = data.reduce((sum, row) => sum + row.actual_collections, 0);
    let global_coll_pct = t_coll_target ? ((t_coll / t_coll_target) * 100).toFixed(1) : 0.0;

    let t_var = t_coll - t_sales; // Projected Net Cash Position
    let t_outstanding = data.reduce((sum, row) => sum + row.total_outstanding, 0);
    let t_overdue = data.reduce((sum, row) => sum + row.total_overdue, 0);
    
    // 4-Month PDC Splice
    let t_pdc = data.reduce((sum, row) => sum + row.pdc_amount, 0);
    let t_pdc_m1 = data.reduce((sum, row) => sum + row.pdc_m1, 0);
    let t_pdc_m2 = data.reduce((sum, row) => sum + row.pdc_m2, 0);
    let t_pdc_m3 = data.reduce((sum, row) => sum + row.pdc_m3, 0);
    let t_pdc_m4 = data.reduce((sum, row) => sum + row.pdc_m4, 0);

    let html = `
        <div class="nexus-toolbar">
            <div>
                <span class="nexus-badge">Strict Active Portfolio Managers: ${page.active_reps}</span>
            </div>
            <div>
                <input type="text" id="nexus-search-input" class="nexus-search" placeholder="🔍 Quick Filter by Rep or Territory...">
            </div>
        </div>

        <div class="nexus-kpi-container">
            <!-- Row 1: Sales Operations -->
            <div class="nexus-kpi-card bg-dark">
                <div>
                    <div class="kpi-title">Company Sales Target</div>
                    <div class="kpi-value">${format_curr(t_sales_target)}</div>
                </div>
            </div>
            <div class="nexus-kpi-card bg-sales">
                <div>
                    <div class="kpi-title">${page.is_custom ? 'Total Invoiced / Billed (Period)' : 'Total Invoiced / Billed (MTD)'}</div>
                    <div class="kpi-value">${format_curr(t_sales)}</div>
                </div>
                <div class="kpi-subtext">🎯 ${global_sales_pct}% of Company Target</div>
            </div>
            <div class="nexus-kpi-card bg-deliv">
                <div>
                    <div class="kpi-title">Total Value Delivered</div>
                    <div class="kpi-value">${format_curr(t_delivered)}</div>
                </div>
            </div>

            <!-- Row 2: Collection & Liquidity -->
            <div class="nexus-kpi-card bg-dark">
                <div>
                    <div class="kpi-title">Company Collection Target</div>
                    <div class="kpi-value">${format_curr(t_coll_target)}</div>
                </div>
            </div>
            <div class="nexus-kpi-card bg-coll">
                <div>
                    <div class="kpi-title">${page.is_custom ? 'Total Collected (Period)' : 'Total Collected (MTD)'}</div>
                    <div class="kpi-value">${format_curr(t_coll)}</div>
                </div>
                <div class="kpi-subtext">🎯 ${global_coll_pct}% of Company Target</div>
            </div>
            <div class="nexus-kpi-card ${t_var >= 0 ? 'bg-var-pos' : 'bg-var-neg'}">
                <div>
                    <div class="kpi-title">Projected Net Cash Position</div>
                    <div class="kpi-value">${format_curr(t_var)}</div>
                </div>
            </div>
            
            <!-- Row 3: Risk Matrix & Pipeline -->
            <div class="nexus-kpi-card bg-outst">
                <div>
                    <div class="kpi-title">Total Ledger Outstanding</div>
                    <div class="kpi-value">${format_curr(t_outstanding)}</div>
                </div>
            </div>
            <div class="nexus-kpi-card bg-risk">
                <div>
                    <div class="kpi-title">Critical Overdue Exposure</div>
                    <div class="kpi-value" style="color: #fca5a5;">${format_curr(t_overdue)}</div>
                </div>
            </div>
            <div class="nexus-kpi-card bg-pdc">
                <div>
                    <div class="kpi-title">PDC Vault (120-Day Horizon)</div>
                    <div class="kpi-value" style="color: #38bdf8;">${format_curr(t_pdc)}</div>
                </div>
                <div class="kpi-subtext" style="display: grid; grid-template-columns: 1fr 1fr; gap: 4px; font-size: 13px; color: #cbd5e1; border-top: 1px solid rgba(255,255,255,0.2);">
                    <div>M1: ${format_curr(t_pdc_m1)}</div>
                    <div>M2: ${format_curr(t_pdc_m2)}</div>
                    <div>M3: ${format_curr(t_pdc_m3)}</div>
                    <div>M4: ${format_curr(t_pdc_m4)}</div>
                </div>
            </div>
        </div>

        <div class="nexus-meta-grid">
            <div class="nexus-meta-item">Invoices Monitored:<br><span>${dates.start}</span> to <span>${dates.end}</span></div>
            <div class="nexus-meta-item">Receipts Monitored:<br><span>${dates.start}</span> to <span>${dates.end}</span></div>
            <div class="nexus-meta-item">Receivables Aging Portfolio:<br>Up to <span>${dates.end}</span></div>
            <div class="nexus-meta-item">PDC Vault Matrix:<br><span>${dates.end}</span> to <span>${dates.pdc_end}</span></div>
        </div>
        
        <div class="nexus-table-wrapper">
            <table class="nexus-table">
                <thead>
                    <tr>
                        <th rowspan="2" class="col-sp">Executive ↕</th>
                        <th rowspan="2" class="col-terr">Territory ↕</th>
                        <th colspan="6" style="text-align:center; background:#ebf8ff; color:#0369a1;">SALES & INVOICING POSITION</th>
                        <th colspan="5" style="text-align:center; background:#f0fdf4; color:#15803d;">COLLECTION & TREASURY POSITION</th>
                        <th rowspan="2" style="background:#f8fafc;">Projected Net Cash ↕</th>
                        <th rowspan="2">Ledger Outstanding ↕</th>
                        <th rowspan="2" style="color:#b91c1c;">Overdue Exposure ↕</th>
                        <th colspan="5" style="text-align:center; background:#f8fafc;">PDC VAULT (MATURITY HORIZON)</th>
                    </tr>
                    <tr>
                        <!-- Sales & Delivery -->
                        <th data-sort="sales_target">Custom Target ↕</th>
                        <th data-sort="actual_sales">Invoiced ↕</th>
                        <th data-sort="actual_delivered">Delivered ↕</th>
                        <th data-sort="sales_pct">% Achieved ↕</th>
                        <th data-sort="sales_deficit">Deficit/Surplus ↕</th>
                        <th data-sort="today_sales">Today's Invoiced ↕</th>
                        
                        <!-- Collections -->
                        <th data-sort="collection_target">Custom Target ↕</th>
                        <th data-sort="actual_collections">Collected ↕</th>
                        <th data-sort="collection_pct">% Achieved ↕</th>
                        <th data-sort="collection_deficit">Deficit/Surplus ↕</th>
                        <th data-sort="today_collections">Today's Collections ↕</th>

                        <!-- PDC Breakdown -->
                        <th data-sort="pdc_amount">Total PDC ↕</th>
                        <th data-sort="pdc_m1">Month 1 ↕</th>
                        <th data-sort="pdc_m2">Month 2 ↕</th>
                        <th data-sort="pdc_m3">Month 3 ↕</th>
                        <th data-sort="pdc_m4">Month 4 ↕</th>
                    </tr>
                </thead>
                <tbody id="nexus-table-body">
                    </tbody>
            </table>
        </div>

        <div class="nexus-glossary">
            <h6 style="margin-top:0; font-weight:800; color:#0f172a;">Executive Financial Glossary</h6>
            <strong>Transaction-Led Integrity:</strong> Only executives with an active billing invoice in the selected period are displayed. Targets are absolute, non-prorated.<br>
            <strong>Accounts Receivable Accuracy:</strong> Ledger Outstanding and Overdue Exposure reflect exact historical FIFO logic and payment schedules executing natively from Frappe's AR engine.<br>
            <strong>Projected Net Cash Position:</strong> The variance between cash collected and revenue invoiced. A <em>Positive</em> number means the team is successfully liquidating historical debt.<br>
            <strong>PDC Vault:</strong> Strict mapping of submitted Post-Dated Checks categorized dynamically into 4 sequential 30-day brackets starting from the moment of refresh.
        </div>
    `;

    $('#dashboard-content').html(html);

    // Attach Event Listeners for Client-Side Interactivity
    $('#nexus-search-input').on('keyup', function() {
        render_table_body(page, $(this).val());
    });

    $('.nexus-table th[data-sort], .nexus-table th:contains("↕")').on('click', function() {
        let col = $(this).attr('data-sort');
        
        // Map top row header clicks to their respective column keys
        if(!col) {
            let text = $(this).text().trim();
            if(text.includes("Executive")) col = "sales_person_name";
            if(text.includes("Territory")) col = "territory";
            if(text.includes("Projected Net Cash")) col = "coll_sales_diff";
            if(text.includes("Ledger Outstanding")) col = "total_outstanding";
            if(text.includes("Overdue Exposure")) col = "total_overdue";
        }

        if(col) {
            if(page.current_sort.col === col) {
                page.current_sort.dir = page.current_sort.dir === 'asc' ? 'desc' : 'asc';
            } else {
                page.current_sort.col = col;
                page.current_sort.dir = 'desc'; 
            }
            render_table_body(page, $('#nexus-search-input').val());
        }
    });
}

function render_table_body(page, filter_term = "") {
    let data = [...page.nexus_cache];
    
    if(filter_term) {
        filter_term = filter_term.toLowerCase();
        data = data.filter(r => 
            r.sales_person_name.toLowerCase().includes(filter_term) || 
            r.territory.toLowerCase().includes(filter_term)
        );
    }

    let col = page.current_sort.col;
    let dir = page.current_sort.dir === 'asc' ? 1 : -1;
    data.sort((a, b) => {
        let valA = a[col] || 0;
        let valB = b[col] || 0;
        if (typeof valA === 'string') return valA.localeCompare(valB) * dir;
        return (valA - valB) * dir;
    });

    let tbody_html = '';
    data.forEach(row => {
        let s_css = get_pct_css(row.sales_pct);
        let c_css = get_pct_css(row.collection_pct);

        // Phase 3: Conditionally render Today metrics (leave blank for custom audit)
        let display_today_sales = page.is_custom ? "" : format_curr(row.today_sales || 0);
        let display_today_coll = page.is_custom ? "" : format_curr(row.today_collections || 0);

        tbody_html += `
            <tr>
                <td class="col-sp">${row.sales_person_name}</td>
                <td class="col-terr" style="font-size: 11px; color: #475569;">${row.territory}</td>
                
                <!-- Sales & Delivery Array -->
                <td style="color:#64748b;">${format_curr(row.sales_target)}</td>
                <td class="text-heavy">${format_curr(row.actual_sales)}</td>
                <td style="color:#0369a1; font-weight: 700;">${format_curr(row.actual_delivered)}</td>
                <td class="${s_css}">${row.sales_pct.toFixed(1)}%</td>
                <td class="${row.sales_deficit >= 0 ? 'val-green' : 'val-red'}">${format_curr(row.sales_deficit)}</td>
                <td class="val-today">${display_today_sales}</td>
                
                <!-- Collection Array -->
                <td style="color:#64748b;">${format_curr(row.collection_target)}</td>
                <td class="text-heavy">${format_curr(row.actual_collections)}</td>
                <td class="${c_css}">${row.collection_pct.toFixed(1)}%</td>
                <td class="${row.collection_deficit >= 0 ? 'val-green' : 'val-red'}">${format_curr(row.collection_deficit)}</td>
                <td class="val-today">${display_today_coll}</td>
                
                <!-- Cash & Risk Array (Historical & FIFO synced) -->
                <td class="${row.coll_sales_diff >= 0 ? 'val-green' : 'val-red'}" style="background: #f8fafc; font-weight: 800;">${format_curr(row.coll_sales_diff)}</td>
                <td class="text-heavy">${format_curr(row.total_outstanding)}</td>
                <td class="${row.total_overdue > 0 ? 'val-red' : ''}">${format_curr(row.total_overdue)}</td>
                
                <!-- 4-Month PDC Matrix Array -->
                <td style="color: #0369a1; font-weight: 800; background: #f8fafc;">${format_curr(row.pdc_amount)}</td>
                <td style="font-size: 11px; color:#475569;">${format_curr(row.pdc_m1)}</td>
                <td style="font-size: 11px; color:#475569;">${format_curr(row.pdc_m2)}</td>
                <td style="font-size: 11px; color:#475569;">${format_curr(row.pdc_m3)}</td>
                <td style="font-size: 11px; color:#475569;">${format_curr(row.pdc_m4)}</td>
            </tr>
        `;
    });

    if(data.length === 0) {
        tbody_html = `<tr><td colspan="21" style="text-align:center; padding: 40px; font-weight: bold; color: #64748b;">No matching transaction records found.</td></tr>`;
    }

    $('#nexus-table-body').empty().html(tbody_html);
}

// Phase 5: Fast In-Memory Excel/CSV Export
function export_to_excel(page) {
    if (!page.nexus_cache || page.nexus_cache.length === 0) {
        frappe.msgprint("No active data available to export.");
        return;
    }

    let data = [...page.nexus_cache];
    let filter_term = $('#nexus-search-input').val();
    
    if(filter_term) {
        filter_term = filter_term.toLowerCase();
        data = data.filter(r => 
            r.sales_person_name.toLowerCase().includes(filter_term) || 
            r.territory.toLowerCase().includes(filter_term)
        );
    }

    let col = page.current_sort.col;
    let dir = page.current_sort.dir === 'asc' ? 1 : -1;
    data.sort((a, b) => {
        let valA = a[col] || 0;
        let valB = b[col] || 0;
        if (typeof valA === 'string') return valA.localeCompare(valB) * dir;
        return (valA - valB) * dir;
    });

    let csv = [];
    let headers = [
        "Executive", "Territory", 
        "Custom Sales Target", "Invoiced", "Value Delivered", "Today's Invoiced", "Sales % Achieved", "Sales Deficit/Surplus",
        "Custom Collection Target", "Collected", "Today's Collections", "Collection % Achieved", "Collection Deficit/Surplus",
        "Projected Net Cash Position", "Ledger Outstanding", "Overdue Exposure",
        "Total PDC Vault", "PDC Month 1", "PDC Month 2", "PDC Month 3", "PDC Month 4"
    ];
    csv.push(headers.join(","));

    data.forEach(row => {
        let r = [
            `"${row.sales_person_name || 'Unassigned'}"`, 
            `"${row.territory || 'Unassigned'}"`,
            row.sales_target || 0, 
            row.actual_sales || 0, 
            row.actual_delivered || 0,
            page.is_custom ? 0 : (row.today_sales || 0), 
            (row.sales_pct || 0).toFixed(2), 
            row.sales_deficit || 0,
            row.collection_target || 0, 
            row.actual_collections || 0, 
            page.is_custom ? 0 : (row.today_collections || 0), 
            (row.collection_pct || 0).toFixed(2), 
            row.collection_deficit || 0,
            row.coll_sales_diff || 0, 
            row.total_outstanding || 0, 
            row.total_overdue || 0,
            row.pdc_amount || 0, 
            row.pdc_m1 || 0, 
            row.pdc_m2 || 0, 
            row.pdc_m3 || 0, 
            row.pdc_m4 || 0
        ];
        csv.push(r.join(","));
    });

    let csvContent = "data:text/csv;charset=utf-8," + csv.join("\n");
    let encodedUri = encodeURI(csvContent);
    let link = document.createElement("a");
    link.setAttribute("href", encodedUri);
    link.setAttribute("download", `Executive_Control_Tower_${frappe.datetime.nowdate()}.csv`);
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
}

function get_pct_css(pct) {
    if (pct >= 100) return 'light-pct-green';
    if (pct >= 80) return 'light-pct-yellow';
    return 'light-pct-red';
}

function format_curr(value) {
    return frappe.format(value || 0, {fieldtype: 'Currency'});
}