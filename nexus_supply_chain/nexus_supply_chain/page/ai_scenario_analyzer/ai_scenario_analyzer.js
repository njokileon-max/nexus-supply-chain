// apps/nexus_supply_chain/nexus_supply_chain/page/ai_scenario_analyzer/ai_scenario_analyzer.js

frappe.pages['ai_scenario_analyzer'].on_page_load = function(wrapper) {
    let page = frappe.ui.make_app_page({
        parent: wrapper,
        title: 'AI Profitability & Scenario Analyzer',
        single_column: true
    });

    page.scenario_data = [];
    page.filtered_data = [];
    page.global_overheads = 0.0;
    page.global_gl_sales = 0.0;
    page.current_sort = { col: 'total_billed_gross', dir: 'desc' };

    const layout_html = [
        "<style>",
        "    .analyzer-wrapper { padding: 20px; background-color: #f8fafc; min-height: 85vh; font-family: 'Inter', sans-serif; }",
        
        "    /* Toolbar */",
        "    .nexus-toolbar { display: flex; flex-direction: column; gap: 15px; margin-bottom: 20px; padding: 15px; background: #fff; border-radius: 8px; border: 1px solid #e2e8f0; box-shadow: 0 2px 6px rgba(0,0,0,0.04); }",
        "    .nexus-input { padding: 8px 12px; border: 1px solid #cbd5e1; border-radius: 4px; font-size: 13px; font-weight: 600; outline: none; background: #f8fafc; color: #0f172a; }",
        "    .nexus-input:focus { border-color: #3b82f6; }",
        
        "    /* KPI Ribbon - Updated to 4 columns across 2 rows */",
        "    .kpi-grid { display: grid; grid-template-columns: repeat(4, 1fr); gap: 15px; margin-bottom: 20px; }",
        "    .kpi-card { padding: 15px; border-radius: 8px; background: #fff; border: 1px solid #e2e8f0; box-shadow: 0 4px 6px rgba(0,0,0,0.02); text-align: center; display: flex; flex-direction: column; justify-content: center; transition: all 0.2s ease-in-out; }",
        "    .kpi-title { font-size: 10px; font-weight: 800; text-transform: uppercase; color: #64748b; letter-spacing: 0.5px; margin-bottom: 5px; }",
        "    .kpi-value { font-size: 18px; font-weight: 900; line-height: 1.2; }",
        "    .kpi-sub { font-size: 11px; font-weight: 700; margin-top: 5px; }",
        
        "    .bg-dark-kpi { background: #0f172a; border-color: #0f172a; }",
        "    .bg-dark-kpi .kpi-title { color: #94a3b8; } .bg-dark-kpi .kpi-value { color: #fff; }",
        
        "    .bg-red-kpi { background: #fef2f2; border-color: #fca5a5; }",
        "    .bg-red-kpi .kpi-title { color: #991b1b; } .bg-red-kpi .kpi-value { color: #b91c1c; }",
        
        "    .bg-green-kpi { background: #f0fdf4; border-color: #86efac; }",
        "    .bg-green-kpi .kpi-title { color: #166534; } .bg-green-kpi .kpi-value { color: #15803d; }",

        "    /* Multi-Axis Frozen Table */",
        "    .nexus-table-wrapper { width: 100%; max-height: calc(100vh - 350px); overflow-y: auto; overflow-x: auto; border-radius: 8px; border: 1px solid #cbd5e1; background: #fff; position: relative; }",
        "    .nexus-table { width: 100%; border-collapse: separate; border-spacing: 0; font-size: 13px; min-width: 2400px; }",
        "    .nexus-table th, .nexus-table td { border-bottom: 1px solid #e2e8f0; border-right: 1px solid #e2e8f0; padding: 12px; text-align: right; white-space: nowrap; }",
        
        "    .nexus-table thead tr:nth-child(1) th { position: sticky; top: 0; background-color: #f1f5f9; font-weight: 800; color: #334155; z-index: 50; border-top: 1px solid #cbd5e1; border-bottom: 2px solid #cbd5e1; }",
        "    .nexus-table thead tr:nth-child(2) th { position: sticky; top: 43px; background-color: #f8fafc; font-weight: 800; color: #475569; z-index: 49; border-bottom: 2px solid #cbd5e1; cursor: pointer; }",
        "    .nexus-table thead tr:nth-child(2) th:hover { background-color: #e2e8f0; }",
        
        "    .col-code { position: sticky; left: 0; width: 130px; min-width: 130px; max-width: 130px; background: #ffffff !important; z-index: 30; text-align: left !important; font-weight: 700; border-right: 1px solid #e2e8f0;}",
        "    .col-name { position: sticky; left: 130px; width: 250px; min-width: 250px; max-width: 250px; background: #ffffff !important; z-index: 30; text-align: left !important; border-right: 1px solid #e2e8f0; white-space: normal !important; word-wrap: break-word; line-height: 1.3; }",
        "    .col-grp  { position: sticky; left: 380px; width: 130px; min-width: 130px; max-width: 130px; background: #ffffff !important; z-index: 30; text-align: left !important; border-right: 1px solid #e2e8f0; }",
        "    .col-base { position: sticky; left: 510px; width: 140px; min-width: 140px; max-width: 140px; background: #ffffff !important; z-index: 30; text-align: left !important; box-shadow: 4px 0 8px -2px rgba(0,0,0,0.08); border-right: 1px solid #cbd5e1; white-space: normal !important; word-wrap: break-word; line-height: 1.3; }",
        
        "    .nexus-table thead tr:nth-child(1) th.col-code, .nexus-table thead tr:nth-child(1) th.col-name, .nexus-table thead tr:nth-child(1) th.col-grp, .nexus-table thead tr:nth-child(1) th.col-base { z-index: 70; background: #f1f5f9 !important; }",
        "    .nexus-table thead tr:nth-child(2) th.col-code, .nexus-table thead tr:nth-child(2) th.col-name, .nexus-table thead tr:nth-child(2) th.col-grp, .nexus-table thead tr:nth-child(2) th.col-base { z-index: 69; background: #f8fafc !important; }",
        "</style>",
        
        "<div class='analyzer-wrapper'>",
        "    <div class='nexus-toolbar'>",
        "        <div class='d-flex justify-content-between align-items-center w-100'>",
        "            <div class='d-flex gap-3 align-items-end'>",
        "                <div class='d-flex flex-column'>",
        "                    <label style='font-size:11px; font-weight:700; color:#64748b; margin-bottom:4px;'>From Date</label>",
        "                    <input type='date' id='filter-start-date' class='nexus-input'>",
        "                </div>",
        "                <div class='d-flex flex-column'>",
        "                    <label style='font-size:11px; font-weight:700; color:#64748b; margin-bottom:4px;'>To Date</label>",
        "                    <input type='date' id='filter-end-date' class='nexus-input'>",
        "                </div>",
        "                <div class='d-flex flex-column border-start ps-3 ms-1'>",
        "                    <label style='font-size:11px; font-weight:700; color:#64748b; margin-bottom:4px;'>Target Price List Audit</label>",
        "                    <select id='filter-price-list' class='nexus-input' style='width: 200px;'></select>",
        "                </div>",
        "                <button class='btn btn-primary fw-bold px-4 mb-1 ms-2' id='btn-run-analysis'>Run Analysis</button>",
        "            </div>",
        "            <div class='d-flex align-items-end mb-1'>",
        "                <button class='btn btn-light border fw-bold' id='btn-export-excel'><i class='fa fa-file-excel me-2 text-success'></i>Export Ledger</button>",
        "            </div>",
        "        </div>",
        "        <div class='d-flex justify-content-start align-items-center w-100 gap-3 border-top pt-3 mt-1'>",
        "            <div class='d-flex align-items-center'>",
        "                <i class='fa fa-search text-muted me-2'></i>",
        "                <input type='text' id='dt-search' class='nexus-input' style='width: 300px;' placeholder='Quick Search Item Code, Name or Base...'>",
        "            </div>",
        "            <select id='dt-group-filter' class='nexus-input' style='width: 250px;'><option value=''>All Item Groups</option></select>",
        "        </div>",
        "    </div>",
        
        "    <div class='kpi-grid' id='kpi-container'>",
        "        ",
        "    </div>",
        
        "    <div class='nexus-table-wrapper'>",
        "        <table class='nexus-table'>",
        "            <thead>",
        "                <tr>",
        "                    <th rowspan='2' class='col-code'>Item Code ↕</th>",
        "                    <th rowspan='2' class='col-name'>Item Name ↕</th>",
        "                    <th rowspan='2' class='col-grp' data-sort='item_group' style='text-align: left;'>Group ↕</th>",
        "                    <th rowspan='2' class='col-base' data-sort='custom_crystal_base' style='text-align: left;'>Crystal Base ↕</th>",
        "                    <th rowspan='2' data-sort='total_qty' style='text-align: center; min-width: 100px;'>Qty Billed ↕</th>",
        "                    <th colspan='2' style='text-align:center; background:#f8fafc; color:#334155;'>RATE AUDIT (GROSS)</th>",
        "                    <th colspan='3' style='text-align:center; background:#eff6ff; color:#1e3a8a;'>INVOICE REVENUE</th>",
        "                    <th colspan='2' style='text-align:center; background:#fff7ed; color:#854d0e;'>TRUE LANDED COST ENGINE</th>",
        "                    <th colspan='2' style='text-align:center; background:#fef08a; color:#713f12;'>NET PROFITABILITY</th>",
        "                </tr>",
        "                <tr>",
        "                    <th data-sort='exact_billed_rate' style='min-width: 140px; color:#0f172a;'>Exact Billed Rate ↕</th>",
        "                    <th data-sort='target_price' style='min-width: 140px; color:#64748b;'>Target Price List ↕</th>",
        
        "                    <th data-sort='total_billed_gross' style='min-width: 150px; background:#eff6ff; font-weight:900;'>Gross Billed ↕</th>",
        "                    <th data-sort='total_vat_deduction' style='min-width: 140px; background:#dbeafe; color:#b91c1c;'>- 16% VAT Liab. ↕</th>",
        "                    <th data-sort='net_realized_revenue' style='min-width: 150px; background:#bfdbfe; color:#1e3a8a; font-weight:900;'>Net Revenue ↕</th>",
        
        "                    <th data-sort='unit_landed_cost' style='min-width: 140px; background:#fff7ed;'>Unit Landed Cost ↕</th>",
        "                    <th data-sort='total_landed_cost' style='min-width: 150px; background:#ffedd5; color:#854d0e; font-weight:900;'>Total Landed Cost ↕</th>",
        
        "                    <th data-sort='net_profit_value' style='min-width: 150px; background:#fef08a; color:#713f12; font-weight:900;'>Net Profit Value ↕</th>",
        "                    <th data-sort='net_margin_pct' style='min-width: 130px; background:#fef08a; color:#713f12; font-weight:900;'>Net Margin % ↕</th>",
        "                </tr>",
        "            </thead>",
        "            <tbody id='analyzer-table-body'>",
        "                <tr><td colspan='14' class='text-center py-5 text-muted fw-bold'>Select a date range and click Run Analysis.</td></tr>",
        "            </tbody>",
        "        </table>",
        "    </div>",
        "</div>"
    ].join("");

    $(wrapper).find('.layout-main-section').html(layout_html);

    let today = frappe.datetime.get_today();
    let first_day = frappe.datetime.month_start();
    $('#filter-start-date').val(first_day);
    $('#filter-end-date').val(today);

    frappe.call({
        method: "nexus_supply_chain.nexus_supply_chain.page.ai_scenario_analyzer.ai_scenario_analyzer.get_filters_data",
        callback: function(r) {
            if (r.message && r.message.price_lists) {
                let opts = "";
                r.message.price_lists.forEach(pl => { opts += `<option value='${pl}'>${pl}</option>`; });
                $('#filter-price-list').html(opts);
            }
        }
    });

    function format_curr(value) { return "KES " + parseFloat(value || 0).toLocaleString('en-US', {minimumFractionDigits: 2, maximumFractionDigits: 2}); }

    $('#btn-run-analysis').on('click', function() {
        let start = $('#filter-start-date').val();
        let end = $('#filter-end-date').val();
        let pl = $('#filter-price-list').val();

        if(!start || !end) {
            frappe.msgprint("Please select a valid date range.");
            return;
        }

        $('#analyzer-table-body').html(`<tr><td colspan='14' class='text-center py-5'><i class='fa fa-spinner fa-spin fa-2x text-primary mb-3'></i><p class='text-muted fw-bold'>Mining GL & Invoice Arrays...</p></td></tr>`);
        $('#kpi-container').empty();

        frappe.call({
            method: "nexus_supply_chain.nexus_supply_chain.page.ai_scenario_analyzer.ai_scenario_analyzer.get_scenario_data",
            args: { start_date: start, end_date: end, price_list: pl },
            callback: function(r) {
                if (r.message) {
                    page.global_overheads = r.message.total_overheads || 0.0;
                    page.global_gl_sales = r.message.total_gl_sales || 0.0;
                    
                    page.scenario_data = r.message.sales_data.map(row => {
                        let qty = parseFloat(row.total_qty) || 0.0;
                        let gross_billed = parseFloat(row.total_billed_gross) || 0.0;
                        
                        let net_revenue = gross_billed / 1.16;
                        let vat_deduction = gross_billed - net_revenue;
                        
                        let unit_landed = parseFloat(row.unit_landed_cost) || 0.0;
                        let total_landed = qty * unit_landed;
                        
                        let net_profit = net_revenue - total_landed;
                        let margin_pct = net_revenue > 0 ? (net_profit / net_revenue) * 100 : 0.0;

                        return {
                            ...row,
                            total_vat_deduction: vat_deduction,
                            net_realized_revenue: net_revenue,
                            total_landed_cost: total_landed,
                            net_profit_value: net_profit,
                            net_margin_pct: margin_pct
                        };
                    });

                    let groups = new Set();
                    page.scenario_data.forEach(row => { if(row.item_group) groups.add(row.item_group); });
                    let group_opts = "<option value=''>All Item Groups</option>";
                    Array.from(groups).sort().forEach(g => { group_opts += `<option value='${g}'>${g}</option>`; });
                    $('#dt-group-filter').html(group_opts);

                    apply_filters();
                }
            }
        });
    });

    function apply_filters() {
        let term = $('#dt-search').val().toLowerCase();
        let grp = $('#dt-group-filter').val();

        page.filtered_data = page.scenario_data.filter(row => {
            let match_term = !term || (
                (row.item_code && row.item_code.toLowerCase().includes(term)) ||
                (row.item_name && row.item_name.toLowerCase().includes(term)) ||
                (row.item_group && row.item_group.toLowerCase().includes(term)) ||
                (row.custom_crystal_base && row.custom_crystal_base.toLowerCase().includes(term))
            );
            let match_grp = !grp || row.item_group === grp;
            return match_term && match_grp;
        });

        render_dashboard();
    }

    $('#dt-search').on('keyup', apply_filters);
    $('#dt-group-filter').on('change', apply_filters);

    function render_dashboard() {
        let t_gross_billed = 0, t_vat = 0, t_net_rev = 0, t_landed_cost = 0, t_net_profit = 0, t_target_gross = 0;
        
        page.filtered_data.forEach(r => {
            t_gross_billed += r.total_billed_gross;
            t_vat += r.total_vat_deduction;
            t_net_rev += r.net_realized_revenue;
            t_landed_cost += r.total_landed_cost;
            t_net_profit += r.net_profit_value;
            t_target_gross += (r.total_qty * r.target_price); 
        });

        let revenue_leakage = t_target_gross - t_gross_billed;
        let leakage_class = revenue_leakage > 0 ? 'bg-red-kpi' : 'bg-green-kpi';
        let leakage_text = revenue_leakage > 0 ? 'Value Lost (Discounts)' : 'Value Gained (Markups)';

        let is_filtered = $('#dt-search').val() !== '' || $('#dt-group-filter').val() !== '';
        
        let display_profit = t_net_profit;
        
        let corp_margin = t_net_rev > 0 ? (display_profit / t_net_rev) * 100 : 0.0;

        let kpi_html = `
            <div class='kpi-card bg-dark-kpi'>
                <div class='kpi-title'>1. Total Invoice Billed (Gross)</div>
                <div class='kpi-value'>${format_curr(t_gross_billed)}</div>
            </div>
            <div class='kpi-card bg-red-kpi'>
                <div class='kpi-title'>2. VAT Liability Excluded</div>
                <div class='kpi-value'>- ${format_curr(t_vat)}</div>
            </div>
            <div class='kpi-card' style='background:#eff6ff; border-color:#bfdbfe;'>
                <div class='kpi-title' style='color:#1e3a8a;'>3. Net Realized Revenue</div>
                <div class='kpi-value' style='color:#1e40af;'>${format_curr(t_net_rev)}</div>
            </div>
            <div class='kpi-card' style='background:#fff7ed; border-color:#fed7aa;'>
                <div class='kpi-title' style='color:#9a3412;'>4. Total Landed Cost (BOM+OH)</div>
                <div class='kpi-value' style='color:#7c2d12;'>- ${format_curr(t_landed_cost)}</div>
            </div>
            <div class='kpi-card bg-green-kpi' style='border-width: 2px;'>
                <div class='kpi-title'>5. Net Corporate Profit</div>
                <div class='kpi-value'>${format_curr(display_profit)}</div>
            </div>
            <div class='kpi-card bg-green-kpi' style='border-width: 2px;'>
                <div class='kpi-title'>6. Corporate Net Margin</div>
                <div class='kpi-value'>${corp_margin.toFixed(2)}%</div>
            </div>
            <div class='kpi-card ${leakage_class}' style='border-width: 2px;'>
                <div class='kpi-title'>7. Target Price Adherence</div>
                <div class='kpi-value'>${format_curr(revenue_leakage)}</div>
                <div class='kpi-sub'>${leakage_text}</div>
            </div>
            <div class='kpi-card' style='background:#f1f5f9; border-color:#cbd5e1;' title='For Audit: Raw Cash spent in GL. Do not subtract from Profit as it is already allocated per item.'>
                <div class='kpi-title'>8. Actual GL Overheads (Audit)</div>
                <div class='kpi-value' style='color:#475569;'>${format_curr(page.global_overheads)}</div>
            </div>
        `;
        $('#kpi-container').html(kpi_html);

        if(!is_filtered) {
            let variance = Math.abs(page.global_gl_sales - t_net_rev);
            if(variance > 1000) {
                frappe.show_alert({message: `Audit Warning: GL Income Accounts (${format_curr(page.global_gl_sales)}) differ from Invoice Net Revenue by ${format_curr(variance)}.`, indicator: 'orange'});
            }
        }

        render_table();
    }

    function render_table() {
        let data = [...page.filtered_data];
        let col = page.current_sort.col;
        let dir = page.current_sort.dir === 'asc' ? 1 : -1;
        
        data.sort((a, b) => {
            let valA = a[col] || 0;
            let valB = b[col] || 0;
            if (typeof valA === 'string' && typeof valB === 'string') return valA.localeCompare(valB) * dir;
            return (valA - valB) * dir;
        });

        let tbody_html = '';
        data.forEach(row => {
            let p_color = row.net_profit_value >= 0 ? '#166534' : '#991b1b'; 
            let warning_icon = row.exact_billed_rate < row.target_price ? `<i class="fa fa-exclamation-triangle text-danger me-1" title="Sold Below Target"></i>` : '';

            tbody_html += `
                <tr>
                    <td class="col-code">${row.item_code}</td>
                    <td class="col-name">${row.item_name}</td>
                    <td class="col-grp" style="text-align: left; color:#475569;">${row.item_group || ''}</td>
                    <td class="col-base" style="text-align: left; font-weight:700; color:#0f172a;">${row.custom_crystal_base || '<span class="text-muted fw-normal">None</span>'}</td>
                    <td style="text-align: center; font-weight:800; color:#475569;">${row.total_qty}</td>
                    
                    <td style="font-weight:900; color:#0f172a;">${warning_icon}${format_curr(row.exact_billed_rate)}</td>
                    <td class="text-muted">${format_curr(row.target_price)}</td>
                    
                    <td style="background:#eff6ff; font-weight:800; color:#1e3a8a;">${format_curr(row.total_billed_gross)}</td>
                    <td style="background:#dbeafe; color:#b91c1c;">${format_curr(row.total_vat_deduction)}</td>
                    <td style="background:#bfdbfe; font-weight:900; color:#1e3a8a;">${format_curr(row.net_realized_revenue)}</td>
                    
                    <td style="background:#fff7ed; color:#9a3412;">${format_curr(row.unit_landed_cost)}</td>
                    <td style="background:#ffedd5; font-weight:900; color:#7c2d12;">${format_curr(row.total_landed_cost)}</td>
                    
                    <td style="background:#fef08a; font-weight:900; color:${p_color};">${format_curr(row.net_profit_value)}</td>
                    <td style="background:#fef08a; font-weight:900; color:${p_color};">${row.net_margin_pct.toFixed(1)}%</td>
                </tr>
            `;
        });

        if(data.length === 0) {
            tbody_html = `<tr><td colspan='14' class='text-center py-5 text-muted fw-bold'>No sales found in this filter range.</td></tr>`;
        }

        $('#analyzer-table-body').empty().html(tbody_html);
    }

    $('.nexus-table th[data-sort]').on('click', function() {
        let col = $(this).attr('data-sort');
        if(col) {
            if(page.current_sort.col === col) {
                page.current_sort.dir = page.current_sort.dir === 'asc' ? 'desc' : 'asc';
            } else {
                page.current_sort.col = col;
                page.current_sort.dir = 'desc'; 
            }
            render_table();
        }
    });

    $('#btn-export-excel').on('click', function() {
        if (!page.filtered_data || page.filtered_data.length === 0) return;
        
        let csvContent = "Item Code,Item Name,Item Group,Crystal Base,Qty Billed,Exact Billed Rate (Gross),Target Price List Rate,Total Billed (Gross),VAT Deduction (16%),Net Realized Revenue,Unit Landed Cost,Total Landed Cost,Net Profit Value,Net Margin %\n";
        
        page.filtered_data.forEach(row => {
            let cleanName = (row.item_name || "").replace(/,/g, " "); 
            let cleanBase = (row.custom_crystal_base || "").replace(/,/g, " "); 
            csvContent += `${row.item_code},${cleanName},${row.item_group || ""},${cleanBase},${row.total_qty},${row.exact_billed_rate},${row.target_price},${row.total_billed_gross},${row.total_vat_deduction},${row.net_realized_revenue},${row.unit_landed_cost},${row.total_landed_cost},${row.net_profit_value},${row.net_margin_pct}\n`;
        });
        
        const blob = new Blob([csvContent], { type: 'text/csv;charset=utf-8;' });
        const url = URL.createObjectURL(blob);
        const link = document.createElement("a");
        link.setAttribute("href", url);
        
        let start = $('#filter-start-date').val();
        let end = $('#filter-end-date').val();
        link.setAttribute("download", `Scenario_Analysis_${start}_to_${end}.csv`);
        document.body.appendChild(link);
        link.click();
        document.body.removeChild(link);
    });
};