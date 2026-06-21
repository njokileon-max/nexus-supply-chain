// gnleon29@gmail.com

frappe.pages['product_portfolio'].on_page_load = function(wrapper) {
    let page = frappe.ui.make_app_page({
        parent: wrapper,
        title: 'Predictive Mass Pricing Generator',
        single_column: true
    });

    page.portfolio_data = [];
    page.filtered_data = [];
    page.current_sort = { col: 'item_name', dir: 'asc' };

    const layout_html = [
        "<style>",
        "    .portfolio-wrapper { padding: 20px; background-color: #f8fafc; min-height: 85vh; font-family: 'Inter', sans-serif; }",
        "    .rate-badge { display: inline-block; padding: 5px 10px; background: #fff; border: 1px solid #cbd5e1; border-radius: 6px; font-size: 12px; font-weight: 700; color: #334155; margin-right: 8px; box-shadow: 0 1px 2px rgba(0,0,0,0.02); }",
        
        "    .nexus-toolbar { display: flex; justify-content: space-between; align-items: center; margin-bottom: 20px; padding: 15px; background: #fff; border-radius: 8px; border: 1px solid #e2e8f0; box-shadow: 0 2px 6px rgba(0,0,0,0.04); }",
        "    .nexus-search, .nexus-filter { padding: 8px 12px; border: 1px solid #cbd5e1; border-radius: 4px; font-size: 13px; font-weight: 600; outline: none; background: #f8fafc; }",
        "    .nexus-search:focus, .nexus-filter:focus { border-color: #3b82f6; }",
        "    .select-margin { font-size: 15px; font-weight: 900; border: 2px solid #3b82f6; color: #1e3a8a; border-radius: 6px; padding: 6px 12px; outline: none; background: #eff6ff; }",
        
        "    .nexus-table-wrapper { width: 100%; max-height: calc(100vh - 210px); overflow-y: auto; overflow-x: auto; border-radius: 8px; border: 1px solid #cbd5e1; background: #fff; position: relative; }",
        "    .nexus-table { width: 100%; border-collapse: separate; border-spacing: 0; font-size: 13px; min-width: 2100px; }",
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
        "    .variance-positive { color: #16a34a; font-weight: bold; }",
        "    .variance-negative { color: #ef4444; font-weight: bold; }",
        "</style>",
        
        "<div class='portfolio-wrapper'>",
        "    <div class='d-flex justify-content-between align-items-center mb-3'>",
        "        <div>",
        "            <h4 class='fw-bold text-dark mb-1'><i class='fa fa-cubes me-2 text-primary'></i> Predictive Mass Pricing Generator</h4>",
        "            <div id='rates-container' class='mt-2'><span class='text-muted small'>Loading Overheads...</span></div>",
        "        </div>",
        "        <button class='btn btn-outline-primary fw-bold' id='refresh-portfolio-btn'><i class='fa fa-refresh me-2'></i> Sync Data Graph</button>",
        "    </div>",
        
        "    <div class='nexus-toolbar'>",
        "        <div class='d-flex gap-3 align-items-center'>",
        "            <div class='d-flex align-items-center'>",
        "                <i class='fa fa-search text-muted me-2'></i>",
        "                <input type='text' id='dt-search' class='nexus-search' placeholder='Quick Search Catalog...'>",
        "            </div>",
        "            <select id='dt-group-filter' class='nexus-filter' style='width: 200px;'><option value=''>All Item Groups</option></select>",
        "            <div class='d-flex align-items-center gap-2 border-start ps-3 ms-1'>",
        "                <span class='fw-bold text-muted' style='font-size:12px;'>TARGET MARGIN:</span>",
        "                <select id='margin-simulator' class='select-margin'></select>",
        "            </div>",
        "        </div>",
        "        <div class='d-flex gap-2'>",
        "            <button class='btn btn-light border fw-bold' id='btn-export-excel'><i class='fa fa-file-excel me-2 text-success'></i>Export Displayed</button>",
        "            <button class='btn btn-warning fw-bold shadow-sm' id='btn-commit-costs'><i class='fa fa-database me-2 text-dark'></i>Sync Landed Costs to DB</button>",
        "            <button class='btn btn-dark fw-bold shadow-sm' id='btn-generate-averages'><i class='fa fa-cogs me-2'></i>Generate Averages & Save Pricing</button>",
        "        </div>",
        "    </div>",
        
        "    <div class='nexus-table-wrapper' id='portfolio-datatable'>",
        "        <div class='text-center py-5 text-muted'><i class='fa fa-spinner fa-spin fa-2x mb-3 text-primary'></i><p>Compiling Memory Graph Elements...</p></div>",
        "    </div>",
        "</div>"
    ].join("");

    $(wrapper).find('.layout-main-section').html(layout_html);

    let margin_opts = "";
    for(let i=5; i<=100; i+=5) {
        let selected = (i===25) ? "selected" : "";
        margin_opts += `<option value='${i}' ${selected}>${i}%</option>`;
    }
    $('#margin-simulator').html(margin_opts);

    function format_curr(value) { return "KES " + parseFloat(value || 0).toLocaleString('en-US', {minimumFractionDigits: 2, maximumFractionDigits: 2}); }

    function load_portfolio_data() {
        let container = document.getElementById('portfolio-datatable');
        container.innerHTML = `<div class="text-center py-5 text-muted"><i class="fa fa-spinner fa-spin fa-2x mb-3 text-primary"></i><p>Re-compiling Memory Graph...</p></div>`;

        frappe.call({
            method: "nexus_supply_chain.nexus_supply_chain.page.product_portfolio.product_portfolio.get_portfolio_matrix",
            callback: function(r) {
                if (r.message) {
                    page.portfolio_data = r.message.portfolio;
                    
                    let rates_html = "<span class='fw-bold text-muted me-2' style='font-size:11px;'>ACTIVE OVERHEADS:</span>";
                    for (const [grp, rate] of Object.entries(r.message.rates)) {
                        rates_html += `<span class='rate-badge'>${grp}: <span class='text-primary'>${format_curr(rate)}/Kg</span></span>`;
                    }
                    if (rates_html.includes("span") === false) rates_html = "<span class='text-danger fw-bold'>No Active Cost Matrix Found. Overheads will be 0.00.</span>";
                    $('#rates-container').html(rates_html);

                    let groups = new Set();
                    page.portfolio_data.forEach(row => { if(row.item_group) groups.add(row.item_group); });
                    let group_opts = "<option value=''>All Item Groups</option>";
                    Array.from(groups).sort().forEach(g => { group_opts += `<option value='${g}'>${g}</option>`; });
                    $('#dt-group-filter').html(group_opts);

                    build_table_skeleton();
                    run_live_simulation(); 
                }
            }
        });
    }

    function build_table_skeleton() {
        let table_html = `
            <table class='nexus-table'>
                <thead>
                    <tr>
                        <th rowspan="2" class="col-code">Item Code ↕</th>
                        <th rowspan="2" class="col-name">Item Name ↕</th>
                        <th rowspan="2" class="col-grp" data-sort="item_group" style="text-align: left;">Group ↕</th>
                        <th rowspan="2" class="col-base" data-sort="custom_crystal_base" style="text-align: left;">Crystal Base ↕</th>
                        <th rowspan="2" data-sort="custom_nexus_uom_pool" style="text-align: center;">UOM Pool ↕</th>
                        <th colspan="3" style="text-align:center; background:#f8fafc; color:#334155;">BASE COSTS (KES)</th>
                        <th colspan="3" style="text-align:center; background:#eff6ff; color:#1e3a8a;">FACTORY SELLING PRICE</th>
                        <th colspan="2" style="text-align:center; background:#f0fdf4; color:#15803d;">REGIONAL SELLING PRICE</th>
                        <th colspan="2" style="text-align:center; background:#fef2f2; color:#991b1b;">ERP SYSTEM</th>
                    </tr>
                    <tr>
                        <th data-sort="theoretical_cost" style="min-width: 140px;">BOM Cost ↕</th>
                        <th data-sort="overhead_rate" style="min-width: 140px;">OH Rate/Kg ↕</th>
                        <th data-sort="true_landed_cost" style="min-width: 140px;">Landed Cost ↕</th>
                        
                        <th data-sort="system_suggested_price" style="background:#eff6ff; min-width: 150px;">Net Price ↕</th>
                        <th data-sort="system_suggested_price_vat" style="background:#dbeafe; color:#1e3a8a; min-width: 150px;">Inc 16% VAT ↕</th>
                        <th data-sort="profit_value" style="background:#fef08a; color:#713f12; min-width: 150px;">Profit Value ↕</th>
                        
                        <th data-sort="regional_price" style="background:#f0fdf4; min-width: 150px;">Net Price ↕</th>
                        <th data-sort="regional_price_vat" style="background:#dcfce7; color:#15803d; min-width: 150px;">Inc 16% VAT ↕</th>
                        
                        <th data-sort="system_valuation_rate" style="min-width: 140px;">Sys Valuation ↕</th>
                        <th data-sort="variance" style="min-width: 140px;">Variance ↕</th>
                    </tr>
                </thead>
                <tbody id="nexus-table-body">
                </tbody>
            </table>
        `;
        $('#portfolio-datatable').html(table_html);

        $('.nexus-table th[data-sort], .nexus-table th:contains("↕")').on('click', function() {
            let col = $(this).attr('data-sort');
            if(!col) {
                let text = $(this).text().trim();
                if(text.includes("Item Code")) col = "item_code";
                if(text.includes("Item Name")) col = "item_name";
            }
            if(col) {
                if(page.current_sort.col === col) {
                    page.current_sort.dir = page.current_sort.dir === 'asc' ? 'desc' : 'asc';
                } else {
                    page.current_sort.col = col;
                    page.current_sort.dir = 'desc'; 
                }
                render_table_body();
            }
        });
    }

    function run_live_simulation() {
        let margin_pct = parseFloat($('#margin-simulator').val()) / 100.0;

        page.portfolio_data.forEach(row => {
            let cost = parseFloat(row.true_landed_cost) || 0.0;
            
            let suggested_price_net = margin_pct < 1.0 ? (cost / (1.0 - margin_pct)) : cost; 
            let suggested_price_vat = suggested_price_net * 1.16;
            
            let profit_value = suggested_price_net - cost;
            
            let regional_price_net = suggested_price_net * 1.05;
            let regional_price_vat = regional_price_net * 1.16;

            row.system_suggested_price = suggested_price_net;
            row.system_suggested_price_vat = suggested_price_vat;
            row.profit_value = profit_value;
            row.regional_price = regional_price_net;
            row.regional_price_vat = regional_price_vat;
        });

        apply_filters(); 
    }

    $('#margin-simulator').on('change', function() {
        run_live_simulation();
    });

    function apply_filters() {
        let term = $('#dt-search').val().toLowerCase();
        let grp = $('#dt-group-filter').val();

        page.filtered_data = page.portfolio_data.filter(row => {
            let match_term = !term || (
                (row.item_code && row.item_code.toLowerCase().includes(term)) ||
                (row.item_name && row.item_name.toLowerCase().includes(term)) ||
                (row.item_group && row.item_group.toLowerCase().includes(term)) ||
                (row.custom_crystal_base && row.custom_crystal_base.toLowerCase().includes(term))
            );
            let match_grp = !grp || row.item_group === grp;
            return match_term && match_grp;
        });

        render_table_body();
    }

    $('#dt-search').on('keyup', apply_filters);
    $('#dt-group-filter').on('change', apply_filters);

    function render_table_body() {
        let data = [...page.filtered_data];
        
        let col = page.current_sort.col;
        let dir = page.current_sort.dir === 'asc' ? 1 : -1;
        data.sort((a, b) => {
            let valA = a[col] || '';
            let valB = b[col] || '';
            if (typeof valA === 'string' && typeof valB === 'string') return valA.localeCompare(valB) * dir;
            return (valA - valB) * dir;
        });

        let tbody_html = '';
        data.forEach(row => {
            let v_class = row.variance >= 0 ? 'variance-positive' : 'variance-negative';
            let v_icon = row.variance >= 0 ? 'fa-arrow-up' : 'fa-arrow-down';
            let v_display = row.variance === 0 ? '<span class="text-muted">-</span>' : `<span class="${v_class}"><i class="fa ${v_icon} me-1"></i>${format_curr(Math.abs(row.variance))}</span>`;
            
            let p_color = row.profit_value >= 0 ? '#166534' : '#991b1b';

            tbody_html += `
                <tr>
                    <td class="col-code">${row.item_code}</td>
                    <td class="col-name">${row.item_name}</td>
                    <td class="col-grp" style="text-align: left; color:#475569;">${row.item_group || ''}</td>
                    <td class="col-base" style="text-align: left; font-weight:700; color:#0f172a;">${row.custom_crystal_base || '<span class="text-muted fw-normal">None</span>'}</td>
                    <td style="text-align: center; font-weight:800; color:#0f172a;">${row.custom_nexus_uom_pool || '-'}</td>
                    
                    <td class="text-muted">${format_curr(row.theoretical_cost)}</td>
                    <td class="text-muted">${row.overhead_rate > 0 ? format_curr(row.overhead_rate) : '-'}</td>
                    <td class="text-heavy">${format_curr(row.true_landed_cost)}</td>
                    
                    <td style="background:#eff6ff; font-weight:700; color:#1e3a8a;">${format_curr(row.system_suggested_price)}</td>
                    <td style="background:#dbeafe; font-weight:900; color:#1e3a8a;">${format_curr(row.system_suggested_price_vat)}</td>
                    <td style="background:#fef08a; font-weight:900; color:${p_color};">${format_curr(row.profit_value)}</td>
                    
                    <td style="background:#f0fdf4; font-weight:700; color:#15803d;">${format_curr(row.regional_price)}</td>
                    <td style="background:#dcfce7; font-weight:900; color:#15803d;">${format_curr(row.regional_price_vat)}</td>
                    
                    <td class="text-muted">${format_curr(row.system_valuation_rate)}</td>
                    <td>${v_display}</td>
                </tr>
            `;
        });

        if(data.length === 0) {
            tbody_html = `<tr><td colspan="14" style="text-align:center; padding: 40px; font-weight: bold; color: #64748b;">No matching products found in catalog.</td></tr>`;
        }

        $('#nexus-table-body').empty().html(tbody_html);
    }

    $('#btn-commit-costs').on('click', function() {
        if (!page.filtered_data || page.filtered_data.length === 0) {
            frappe.msgprint("No data available in the current view to sync.");
            return;
        }

        let strategy_dialog = new frappe.ui.Dialog({
            title: 'Sync True Landed Costs to Database',
            fields: [
                {
                    fieldtype: 'HTML',
                    fieldname: 'help_text',
                    options: `
                        <div style="margin-bottom: 15px; padding: 15px; background-color: #fef08a; border-left: 4px solid #ca8a04; border-radius: 4px; font-size: 13px; color: #713f12;">
                            <strong>Insert New Records:</strong> Safe Mode. Creates cost records for items that do NOT currently exist in the Nexus Item Costing table.<br><br>
                            <strong>Update Existing Records:</strong> Audit Mode. Updates the Theoretical BOM and True Landed Costs for items that ALREADY exist in the Costing table.
                        </div>
                    `
                },
                {
                    fieldtype: 'Select',
                    fieldname: 'strategy',
                    label: 'Action Type',
                    options: [
                        {label: 'Insert New Records', value: 'insert'},
                        {label: 'Update Existing Records', value: 'update'}
                    ],
                    reqd: 1,
                    default: 'update'
                }
            ],
            primary_action_label: 'Generate Data Import',
            primary_action: function(strat_values) {
                strategy_dialog.get_primary_btn().prop('disabled', true).text('Compiling CSV...');

                function compile_costing_dispatch(costing_name_map) {
                    let is_update = strat_values.strategy === 'update';
                    let csv_content = is_update 
                        ? "ID,Item Code,Theoretical BOM Cost,Overhead Rate,True Landed Cost\n" 
                        : "Item Code,Theoretical BOM Cost,Overhead Rate,True Landed Cost\n";
                    let row_count = 0;

                    page.filtered_data.forEach(row => {
                        if (is_update) {
                            let doc_name = costing_name_map[row.item_code];
                            if (!doc_name) return; 
                            csv_content += `"${doc_name}","${row.item_code}",${row.theoretical_cost},${row.overhead_rate},${row.true_landed_cost}\n`;
                        } else {
                            csv_content += `"${row.item_code}",${row.theoretical_cost},${row.overhead_rate},${row.true_landed_cost}\n`;
                        }
                        row_count++;
                    });

                    if (row_count === 0) {
                        let msg = is_update 
                            ? "No matching items found with existing Costing records. Use Insert mode to create new records."
                            : "No valid items found to process.";
                        frappe.msgprint(msg);
                        strategy_dialog.get_primary_btn().prop('disabled', false).text('Generate Data Import');
                        return;
                    }

                    strategy_dialog.get_primary_btn().text('Spawning Data Import Document...');

                    frappe.call({
                        method: "nexus_supply_chain.nexus_supply_chain.page.product_portfolio.product_portfolio.generate_costing_data_import_handoff",
                        args: { 
                            csv_content: csv_content,
                            strategy: strat_values.strategy
                        },
                        callback: function(r) {
                            if(r.message) {
                                strategy_dialog.hide();
                                frappe.show_alert({message: "Costing Data Import template created. Redirecting...", indicator: 'green'});
                                setTimeout(() => {
                                    frappe.set_route('Form', 'Data Import', r.message);
                                }, 800);
                            } else {
                                strategy_dialog.get_primary_btn().prop('disabled', false).text('Generate Data Import');
                            }
                        }
                    });
                }

                if (strat_values.strategy === 'update') {
                    strategy_dialog.get_primary_btn().text('Fetching Costing Index...');
                    frappe.call({
                        method: "nexus_supply_chain.nexus_supply_chain.page.product_portfolio.product_portfolio.get_existing_costing_map",
                        callback: function(r) {
                            let costing_name_map = r.message || {};
                            compile_costing_dispatch(costing_name_map);
                        }
                    });
                } else {
                    compile_costing_dispatch({});
                }
            }
        });

        strategy_dialog.show();
    });

    $('#btn-generate-averages').on('click', function() {
        frappe.call({
            method: "nexus_supply_chain.nexus_supply_chain.page.product_portfolio.product_portfolio.get_active_price_lists",
            callback: function(r) {
                if (r.message) {
                    show_averages_modal(r.message);
                }
            }
        });
    });

    function show_averages_modal(price_lists) {
        let active_groups = [];
        page.filtered_data.forEach(r => { 
            if(r.item_group && !active_groups.includes(r.item_group)) active_groups.push(r.item_group); 
        });
        active_groups.sort();

        let formatted_options = active_groups.map(g => ({
            label: g,
            value: g,
            checked: 1 
        }));

        let d = new frappe.ui.Dialog({
            title: 'Generate Tiered Averages & Commit Pricing',
            fields: [
                { fieldtype: 'Select', fieldname: 'price_list', label: 'Target Price List', options: price_lists, reqd: 1 },
                { fieldtype: 'Column Break' },
                { 
                  fieldtype: 'MultiCheck', fieldname: 'item_groups', label: 'Item Groups Found in Current Filter', 
                  options: formatted_options
                },
                { fieldtype: 'Section Break' },
                { fieldtype: 'HTML', fieldname: 'grid_html' }
            ],
            size: 'extra-large',
            primary_action_label: 'Commit to ERP',
            primary_action: function(values) {
                let overrides = [];
                d.$wrapper.find('.override-input').each(function() {
                    let input_gross_price = parseFloat($(this).val());
                    overrides.push({
                        group: $(this).attr('data-group'),
                        base: $(this).attr('data-base'),
                        uom: $(this).attr('data-uom'),
                        new_price: input_gross_price
                    });
                });

                if(overrides.length === 0) {
                    frappe.msgprint("No valid averages found to update.");
                    return;
                }

                let strategy_dialog = new frappe.ui.Dialog({
                    title: 'Select Execution Strategy',
                    fields: [
                        {
                            fieldtype: 'HTML',
                            fieldname: 'help_text',
                            options: `
                                <div style="margin-bottom: 15px; padding: 15px; background-color: #f8fafc; border-left: 4px solid #3b82f6; border-radius: 4px; font-size: 13px; color: #334155;">
                                    <strong>Insert New Records:</strong> Safe Mode. Will only create prices for items that do NOT currently exist in the selected Price List.<br><br>
                                    <strong>Update Existing Records:</strong> Audit Mode. Will only alter prices for items that ALREADY exist in the selected Price List.
                                </div>
                            `
                        },
                        {
                            fieldtype: 'Select',
                            fieldname: 'strategy',
                            label: 'Action Type',
                            options: [
                                {label: 'Insert New Records', value: 'insert'},
                                {label: 'Update Existing Records', value: 'update'}
                            ],
                            reqd: 1,
                            default: 'update'
                        }
                    ],
                    primary_action_label: 'Generate Data Import',
                    primary_action: function(strat_values) {
                        strategy_dialog.get_primary_btn().prop('disabled', true).text('Compiling CSV...');

                        function compile_and_dispatch(price_name_map) {
                            let is_update = strat_values.strategy === 'update';
                            let csv_content = is_update ? "ID,Item Code,Price List,Rate\n" : "Item Code,Price List,Rate\n";
                            let row_count = 0;

                            page.filtered_data.forEach(row => {
                                if (!row.custom_crystal_base || row.custom_crystal_base === '' || row.custom_crystal_base.toLowerCase() === 'none') return;
                                if (!row.custom_nexus_uom_pool || row.custom_nexus_uom_pool === '') return;

                                let target_import_price = null;
                                for (let i = 0; i < overrides.length; i++) {
                                    if (overrides[i].group === row.item_group && 
                                        overrides[i].base === row.custom_crystal_base && 
                                        overrides[i].uom === row.custom_nexus_uom_pool) {
                                        target_import_price = overrides[i].new_price;
                                        break;
                                    }
                                }

                                if (target_import_price === null) return;

                                if (is_update) {
                                    let doc_name = price_name_map[row.item_code];
                                    if (!doc_name) return; 
                                    csv_content += `"${doc_name}","${row.item_code}","${values.price_list}",${target_import_price}\n`;
                                } else {
                                    csv_content += `"${row.item_code}","${values.price_list}",${target_import_price}\n`;
                                }
                                row_count++;
                            });

                            if (row_count === 0) {
                                let msg = is_update 
                                    ? "No matching items found with existing prices in this Price List. Use Insert mode to create new records."
                                    : "No valid items found matching the selected groups. Ensure items have both a defined Base and UOM.";
                                frappe.msgprint(msg);
                                strategy_dialog.get_primary_btn().prop('disabled', false).text('Generate Data Import');
                                return;
                            }

                            strategy_dialog.get_primary_btn().text('Spawning Data Import Document...');

                            frappe.call({
                                method: "nexus_supply_chain.nexus_supply_chain.page.product_portfolio.product_portfolio.generate_data_import_handoff",
                                args: { 
                                    csv_content: csv_content,
                                    strategy: strat_values.strategy,
                                    price_list: values.price_list
                                },
                                callback: function(r) {
                                    if(r.message) {
                                        strategy_dialog.hide();
                                        d.hide(); 
                                        frappe.show_alert({message: "Data Import template created. Redirecting...", indicator: 'green'});
                                        setTimeout(() => {
                                            frappe.set_route('Form', 'Data Import', r.message);
                                        }, 800);
                                    } else {
                                        strategy_dialog.get_primary_btn().prop('disabled', false).text('Generate Data Import');
                                    }
                                }
                            });
                        }

                        if (strat_values.strategy === 'update') {
                            strategy_dialog.get_primary_btn().text('Fetching Price Index...');
                            frappe.call({
                                method: "nexus_supply_chain.nexus_supply_chain.page.product_portfolio.product_portfolio.get_existing_price_map",
                                args: { price_list: values.price_list },
                                callback: function(r) {
                                    let price_name_map = r.message || {};
                                    compile_and_dispatch(price_name_map);
                                }
                            });
                        } else {
                            compile_and_dispatch({});
                        }
                    }
                });

                strategy_dialog.show();
            }
        });

        d.show();
        d.set_value('item_groups', active_groups);
        
        d.$wrapper.on('change', 'input[type="checkbox"]', function() { 
            setTimeout(() => { render_modal_grid(d); }, 50);
        });
        
        render_modal_grid(d); 
    }

    function render_modal_grid(d) {
        let selected_groups = d.get_value('item_groups') || [];
        if(selected_groups.length === 0) {
            d.fields_dict.grid_html.$wrapper.html('<div class="text-muted text-center p-4 border rounded" style="background:#f8fafc;">Select item groups above to generate nested averages.</div>');
            return;
        }

        let agg = {};
        page.filtered_data.forEach(row => {
            if(selected_groups.includes(row.item_group) && row.custom_nexus_uom_pool) {
                let g = row.item_group;
                let b = row.custom_crystal_base || 'Unassigned Base'; 
                let u = row.custom_nexus_uom_pool;
                
                if (b === 'Unassigned Base' || b.toLowerCase() === 'none') return;
                
                if(!agg[g]) agg[g] = {};
                if(!agg[g][b]) agg[g][b] = {};
                if(!agg[g][b][u]) agg[g][b][u] = { count: 0, sum_gross: 0, sum_net: 0 };
                
                agg[g][b][u].count += 1;
                agg[g][b][u].sum_gross += row.system_suggested_price_vat; 
                agg[g][b][u].sum_net += row.system_suggested_price; 
            }
        });

        let html = '<div style="max-height: 450px; overflow-y: auto; border: 1px solid #e2e8f0; border-radius: 6px;">' + 
                   '<table class="table table-bordered mb-0" style="font-size:13px;">' +
                   '<thead style="background:#f1f5f9; position: sticky; top: 0; z-index: 10;"><tr><th>Item Group</th><th>Crystal Base</th><th>UOM Pool</th><th>Calculated Shelf Avg (Inc. 16% VAT)</th><th>Override Target Shelf Price (Inc. 16% VAT)</th></tr></thead><tbody>';
        
        let has_data = false;
        for(let g in agg) {
            for(let b in agg[g]) {
                for(let u in agg[g][b]) {
                    has_data = true;
                    let avg_gross = agg[g][b][u].sum_gross / agg[g][b][u].count;
                    let avg_net = agg[g][b][u].sum_net / agg[g][b][u].count;
                    let display_base = b === 'Unassigned Base' ? '<span class="text-muted font-italic">Unassigned / General</span>' : b;
                    
                    html += `<tr>
                        <td class="align-middle fw-bold" style="color:#334155;">${g}</td>
                        <td class="align-middle fw-bold" style="color:#0f172a;">${display_base}</td>
                        <td class="align-middle fw-bold text-primary text-center">${u}</td>
                        <td class="align-middle text-muted">${format_curr(avg_gross)} <br><small class="text-primary">(Net ERP Rate ~ ${format_curr(avg_net)})</small></td>
                        <td class="align-middle" style="background-color: #eff6ff;"><input type="number" class="form-control override-input fw-bold text-primary" style="border: 2px solid #3b82f6; background-color: #dbeafe; max-width: 150px; margin: auto;" data-group="${g}" data-base="${b}" data-uom="${u}" value="${Math.round(avg_gross)}" step="1"></td>
                    </tr>`;
                }
            }
        }
        html += '</tbody></table></div>';
        
        if(!has_data) {
            html = '<div class="text-danger text-center p-4 border rounded bg-light">No qualified items found. Ensure items have both a defined Base and UOM Pool.</div>';
        }
        
        d.fields_dict.grid_html.$wrapper.html(html);
    }

    $('#btn-export-excel').on('click', function() {
        if (!page.filtered_data || page.filtered_data.length === 0) return;
        
        let csvContent = "Item Code,Item Name,Item Group,Crystal Base,UOM Pool,Theoretical BOM Cost (KES),Overhead Rate (KES/Kg),True Landed Cost (KES),Factory Price - NET (KES),Factory Price - INC 16% VAT (KES),Profit Value (KES),Regional Price - NET (KES),Regional Price - INC 16% VAT (KES),System Valuation Rate (KES),Valuation Variance (KES)\n";
        
        page.filtered_data.forEach(row => {
            let cleanName = (row.item_name || "").replace(/,/g, " "); 
            let cleanBase = (row.custom_crystal_base || "").replace(/,/g, " "); 
            csvContent += `${row.item_code},${cleanName},${row.item_group || ""},${cleanBase},${row.custom_nexus_uom_pool || ""},${row.theoretical_cost || 0},${row.overhead_rate || 0},${row.true_landed_cost || 0},${row.system_suggested_price || 0},${row.system_suggested_price_vat || 0},${row.profit_value || 0},${row.regional_price || 0},${row.regional_price_vat || 0},${row.system_valuation_rate || 0},${row.variance || 0}\n`;
        });
        
        const blob = new Blob([csvContent], { type: 'text/csv;charset=utf-8;' });
        const url = URL.createObjectURL(blob);
        const link = document.createElement("a");
        link.setAttribute("href", url);
        
        let margin = $('#margin-simulator').val();
        link.setAttribute("download", `Predictive_Pricing_${margin}PctMargin.csv`);
        document.body.appendChild(link);
        link.click();
        document.body.removeChild(link);
    });

    $('#refresh-portfolio-btn').on('click', function() { load_portfolio_data(); });

    frappe.realtime.on('nexus_catalog_sync', function(event_data) {
        frappe.show_alert({message: "Catalog Update Detected. Re-compiling pricing graph.", indicator: 'green'});
        load_portfolio_data();
    });

    setTimeout(() => { load_portfolio_data(); }, 300);
};
