// Copyright (c) 2026, Nexus Supply Chain
// For license information, please see license.txt

frappe.pages['nexus_delivery_margin'].on_page_load = function(wrapper) {
    let page = frappe.ui.make_app_page({
        parent: wrapper,
        title: 'Delivery Margin Report',
        single_column: true
    });

    let all_report_data = []; 
    let current_filtered_data = []; 
    let sort_state = { column: null, order: 'asc' };

    // Define a palette of deep, bold colors for the regional headers
    const regionColorPalette = [
        '#1e3a8a', // Deep Blue
        '#065f46', // Deep Green
        '#7f1d1d', // Deep Red
        '#581c87', // Deep Purple
        '#9a3412', // Deep Orange
        '#0f766e', // Deep Teal
        '#86198f', // Indigo
        '#3f3f46'  // Slate Gray
    ];

    // Helper to generate a very light tint of a hex color for the card body
    function getLightShade(hexCode) {
        // Convert hex to RGB
        let r = parseInt(hexCode.slice(1, 3), 16);
        let g = parseInt(hexCode.slice(3, 5), 16);
        let b = parseInt(hexCode.slice(5, 7), 16);
        
        // Mix heavily with white (approx 93% white)
        r = Math.round(r * 0.07 + 255 * 0.93);
        g = Math.round(g * 0.07 + 255 * 0.93);
        b = Math.round(b * 0.07 + 255 * 0.93);
        
        return `rgb(${r}, ${g}, ${b})`;
    }

    // Custom Styles for Dashboard Cards, Table, and Sticky Mechanics
    $('head').append(`
        <style>
            .controls-wrapper {
                background: #ffffff;
                padding: 15px 20px;
                border-radius: 10px;
                box-shadow: 0 2px 8px rgba(0,0,0,0.05);
                border: 1px solid #e2e8f0;
                margin-bottom: 20px;
                display: flex;
                justify-content: space-between;
                align-items: center;
            }
            .sticky-kpi-banner {
                position: sticky;
                top: 0;
                z-index: 10;
                background-color: #f3f6f9; 
                padding-top: 5px;
                padding-bottom: 15px;
            }
            .margin-card {
                background: #ffffff;
                border-radius: 10px;
                padding: 20px;
                box-shadow: 0 4px 12px rgba(0,0,0,0.08);
                border: 2px solid #cbd5e1; /* Heavier border */
            }
            .margin-card-title { font-size: 12px; font-weight: 700; color: #64748b; text-transform: uppercase; letter-spacing: 0.5px; margin-bottom: 8px; }
            .margin-card-value { font-size: 24px; font-weight: 800; color: #0f172a; }
            .margin-card-percentage { font-size: 14px; font-weight: bold; margin-left: 10px; padding: 2px 6px; border-radius: 4px; }
            
            /* Enhanced Regional Cards Styles */
            .region-card {
                border-radius: 8px;
                box-shadow: 0 4px 8px rgba(0,0,0,0.08);
                border: 2px solid #cbd5e1; /* Heavier outer border */
                height: 100%;
                transition: transform 0.2s, box-shadow 0.2s;
                overflow: hidden; /* Ensures header color stays inside rounded corners */
                display: flex;
                flex-direction: column;
            }
            .region-card:hover { transform: translateY(-3px); box-shadow: 0 6px 16px rgba(0,0,0,0.12); }
            
            .region-header {
                padding: 12px 16px;
                color: #ffffff;
                display: flex;
                justify-content: space-between;
                align-items: center;
                border-bottom: 2px solid rgba(0,0,0,0.1);
            }
            .region-title {
                font-size: 14px;
                font-weight: 900; /* Made slightly thicker to match the badge */
                color: #ffffff !important; /* Forces pure white text */
                margin: 0;
                text-transform: uppercase;
                letter-spacing: 0.5px;
            }
            .region-header-pct {
                font-size: 14px;
                font-weight: 900;
                background: rgba(255,255,255,0.2);
                padding: 2px 8px;
                border-radius: 6px;
                box-shadow: inset 0 1px 3px rgba(0,0,0,0.1);
            }

            .region-body {
                padding: 16px;
                flex-grow: 1;
                display: flex;
                flex-direction: column;
                justify-content: space-around;
            }
            .region-stat { display: flex; flex-direction: column; margin-bottom: 12px; }
            .region-stat-label { font-size: 10px; color: #475569; text-transform: uppercase; font-weight: 800; opacity: 0.85; }
            .region-stat-val { font-size: 16px; font-weight: 800; color: #0f172a; margin-top: 2px; }
            
            /* Responsive scrolling table */
            .table-viewport {
                background: #fff;
                border-radius: 10px;
                box-shadow: 0 4px 12px rgba(0,0,0,0.05);
                border: 2px solid #cbd5e1;
                overflow-x: auto;
                margin-bottom: 40px;
            }
            .nexus-table {
                width: 100%;
                min-width: 1400px; 
                border-collapse: collapse;
            }
            .nexus-table th { 
                background-color: #f8fafc !important; 
                color: #475569; 
                font-size: 11px; 
                text-transform: uppercase; 
                font-weight: 700; 
                border-bottom: 2px solid #cbd5e1;
                padding: 12px 15px;
                white-space: nowrap;
            }
            .nexus-table td { 
                vertical-align: middle; 
                font-size: 13px; 
                padding: 10px 15px;
                border-bottom: 1px solid #e2e8f0;
            }
            .nexus-table tbody tr:hover { background-color: #f8fafc; }
            
            /* Sorting Icons */
            .sort-btn { cursor: pointer; user-select: none; transition: color 0.2s; }
            .sort-btn:hover { color: #0f172a; }
            .sort-icon { margin-left: 4px; font-size: 10px; opacity: 0.4; }
            .sort-icon.active { opacity: 1; color: #3b82f6; }

            /* Specific Cell Styling */
            .text-success-dark { color: #16a34a; font-weight: bold; }
            .text-danger-dark { color: #dc2626; font-weight: bold; }
            .zero-qty-row { opacity: 0.55; background-color: #f8fafc; }
            .return-row { background-color: #fef2f2 !important; }
            .source-badge { font-size: 10px; padding: 3px 6px; border-radius: 4px; background: #e2e8f0; color: #475569; font-weight: 600; display: inline-block; margin-bottom: 2px;}
            
            /* Action Button */
            .btn-bom-action {
                background-color: #1e293b;
                color: #ffffff !important;
                font-weight: bold;
                font-size: 11px;
                padding: 5px 12px;
                border-radius: 5px;
                text-decoration: none;
                display: inline-block;
                transition: background-color 0.2s;
            }
            .btn-bom-action:hover { background-color: #334155; }
            
            /* Empty State Container */
            .empty-state-wrapper {
                text-align: center;
                padding: 60px 20px;
                background-color: #ffffff;
                border-radius: 10px;
                border: 2px dashed #cbd5e1;
            }
        </style>
    `);

    // Add Date Filters
    let from_date_field = page.add_field({
        label: 'From Date',
        fieldtype: 'Date',
        fieldname: 'from_date',
        default: frappe.datetime.add_days(frappe.datetime.get_today(), -7), 
    });

    let to_date_field = page.add_field({
        label: 'To Date',
        fieldtype: 'Date',
        fieldname: 'to_date',
        default: frappe.datetime.get_today(),
    });

    // Setup Main HTML Layout
    $(wrapper).find('.layout-main-section').append(`
        <div id="report-dashboard">
            
            <div class="controls-wrapper">
                <div class="d-flex align-items-center gap-3">
                    <button class="btn btn-primary fw-bold px-4 shadow-sm" id="run-report-btn">
                        <i class="fa fa-play me-2"></i> Run Report
                    </button>
                    </div>
                <button class="btn btn-sm btn-outline-success fw-bold" id="export-excel-btn">
                    <i class="fa fa-file-excel-o me-2"></i> Export to Excel
                </button>
            </div>

            <div class="sticky-kpi-banner">
                <div class="row g-4" id="summary-cards-container">
                    <div class="col-12">
                        <div class="empty-state-wrapper">
                            <i class="fa fa-calculator text-muted mb-3" style="font-size: 48px; opacity: 0.3;"></i>
                            <h4 class="text-secondary fw-bold">Ready to Analyze Margins</h4>
                            <p class="text-muted">Select your date range and click <strong>Run Report</strong> to explode formulations and fetch data.</p>
                        </div>
                    </div>
                </div>
            </div>

            <div class="mb-4" id="regional-cards-container" style="display: none;"></div>

            <div class="table-viewport" style="display: none;" id="table-wrapper">
                <table class="nexus-table" id="margin-table">
                    <thead>
                        <tr>
                            <th>Action</th>
                            <th>Item Details</th>
                            <th class="text-end sort-btn" data-col="qty">Qty <i class="fa fa-sort sort-icon"></i></th>
                            <th class="text-end sort-btn" data-col="rate">Rate <i class="fa fa-sort sort-icon"></i></th>
                            <th class="text-end sort-btn" data-col="total_amount">Total Amount <i class="fa fa-sort sort-icon"></i></th>
                            <th class="text-end sort-btn" data-col="unit_cogs">Unit COGS <i class="fa fa-sort sort-icon"></i></th>
                            <th class="text-end border-end sort-btn" data-col="total_cogs">Total COGS <i class="fa fa-sort sort-icon"></i></th>
                            <th class="text-end sort-btn" data-col="gross_margin">Gross Margin (Inc VAT) <i class="fa fa-sort sort-icon"></i></th>
                            <th class="text-end sort-btn" data-col="gp_percent" style="background-color: #f0fdf4 !important;">Margin % (Excl VAT) <i class="fa fa-sort sort-icon"></i></th>
                            <th>Delivery Note</th>
                            <th>Date</th>
                            <th>Customer & Region</th>
                            <th>COGS Source</th>
                        </tr>
                    </thead>
                    <tbody id="margin-table-body">
                    </tbody>
                </table>
            </div>
        </div>
    `);

    // --- EVENT LISTENERS ---
    
    $(wrapper).on('click', '#run-report-btn', function() {
        fetch_report_data();
    });

    $(wrapper).on('click', '.sort-btn', function() {
        if(current_filtered_data.length === 0) return;
        
        const col = $(this).data('col');
        
        if (sort_state.column === col) {
            sort_state.order = sort_state.order === 'asc' ? 'desc' : 'asc';
        } else {
            sort_state.column = col;
            sort_state.order = 'desc'; 
        }

        $('.sort-icon').removeClass('active fa-sort-up fa-sort-down').addClass('fa-sort');
        let icon_class = sort_state.order === 'asc' ? 'fa-sort-up' : 'fa-sort-down';
        $(this).find('.sort-icon').removeClass('fa-sort').addClass(`active ${icon_class}`);

        current_filtered_data.sort((a, b) => {
            let valA = a[col] || 0;
            let valB = b[col] || 0;
            if (valA < valB) return sort_state.order === 'asc' ? -1 : 1;
            if (valA > valB) return sort_state.order === 'asc' ? 1 : -1;
            return 0;
        });

        render_table(current_filtered_data);
    });

    $(wrapper).on('click', '#export-excel-btn', function() {
        export_to_csv(current_filtered_data);
    });


    // --- CORE FUNCTIONS ---

    function formatCurrency(value) {
        return 'KES ' + parseFloat(value || 0).toLocaleString(undefined, {minimumFractionDigits: 2, maximumFractionDigits: 2});
    }

    function process_and_render_data() {
        current_filtered_data = [...all_report_data];

        if (sort_state.column) {
            current_filtered_data.sort((a, b) => {
                let valA = a[sort_state.column] || 0;
                let valB = b[sort_state.column] || 0;
                if (valA < valB) return sort_state.order === 'asc' ? -1 : 1;
                if (valA > valB) return sort_state.order === 'asc' ? 1 : -1;
                return 0;
            });
        }

        let overall = { val: 0, cogs: 0, gp: 0, gp_pct: 0 };
        let regions_data = {};

        current_filtered_data.forEach(r => {
            if (r.qty !== 0) {
                // Aggregate Grand Totals
                overall.val += r.total_amount;
                overall.cogs += r.total_cogs;
                overall.gp += r.gp_ex_vat;

                // Aggregate Regional Data
                let reg = r.region || "Unassigned";
                if (!regions_data[reg]) {
                    regions_data[reg] = { val: 0, cogs: 0, gp: 0 };
                }
                regions_data[reg].val += r.total_amount;
                regions_data[reg].cogs += r.total_cogs;
                regions_data[reg].gp += r.gp_ex_vat;
            }
        });

        let total_rev_ex = overall.val / 1.16;
        overall.gp_pct = total_rev_ex !== 0 ? (overall.gp / total_rev_ex) * 100 : 0;

        render_summary_cards(overall, regions_data);
        render_table(current_filtered_data);
        $('#table-wrapper').show();
    }

    function render_summary_cards(overall, regions) {
        let percent_class = overall.gp_pct >= 0 ? 'bg-success text-white' : 'bg-danger text-white';
        let val_class = overall.gp >= 0 ? 'text-success-dark' : 'text-danger-dark';

        // 1. Render Sticky Overall Banner
        let overall_html = `
            <div class="col-md-4">
                <div class="margin-card" style="border-top: 4px solid #3b82f6;">
                    <div class="margin-card-title">Total Delivered Value (Inc VAT)</div>
                    <div class="margin-card-value">${formatCurrency(overall.val)}</div>
                </div>
            </div>
            <div class="col-md-4">
                <div class="margin-card" style="border-top: 4px solid #f59e0b;">
                    <div class="margin-card-title">Total Theoretical COGS (Current Market)</div>
                    <div class="margin-card-value">${formatCurrency(overall.cogs)}</div>
                </div>
            </div>
            <div class="col-md-4">
                <div class="margin-card" style="border-top: 4px solid #10b981;">
                    <div class="margin-card-title">Gross Profit (Exclusive 16% VAT)</div>
                    <div class="d-flex align-items-center">
                        <div class="margin-card-value ${val_class}">${formatCurrency(overall.gp)}</div>
                        <div class="margin-card-percentage ${percent_class}">${overall.gp_pct.toFixed(2)}%</div>
                    </div>
                </div>
            </div>
        `;
        $('#summary-cards-container').html(overall_html);

        // 2. Render Granular Region Cards Matrix
        let region_html = `
            <h6 class="fw-bold text-muted text-uppercase mb-3 mt-2"><i class="fa fa-map text-primary me-2"></i>Regional Breakdown Matrix</h6>
            <div class="row g-3">
        `;

        let colorIndex = 0;

        Object.keys(regions).sort().forEach(reg => {
            let rd = regions[reg];
            let rev = rd.val / 1.16;
            let pct = rev !== 0 ? (rd.gp / rev) * 100 : 0;
            
            // Assign deep header color and calculate matching light body tint
            let headerColor = regionColorPalette[colorIndex % regionColorPalette.length];
            let bodyColor = getLightShade(headerColor);
            colorIndex++;

            region_html += `
                <div class="col-md-4 col-lg-3">
                    <div class="region-card" style="border-color: ${headerColor}; padding: 0;">
                        <div class="region-header" style="background-color: ${headerColor};">
                            <h5 class="region-title"><i class="fa fa-map-marker-alt me-2" style="opacity:0.8;"></i> ${reg}</h5>
                            <span class="region-header-pct">${pct.toFixed(2)}%</span>
                        </div>
                        
                        <div class="region-body" style="background-color: ${bodyColor};">
                            <div class="region-stat">
                                <span class="region-stat-label">Delivered (Inc VAT)</span>
                                <span class="region-stat-val">${formatCurrency(rd.val)}</span>
                            </div>
                            <div class="region-stat">
                                <span class="region-stat-label">Theoretical COGS</span>
                                <span class="region-stat-val text-muted" style="opacity: 0.9;">${formatCurrency(rd.cogs)}</span>
                            </div>
                            <div class="region-stat mt-2 pt-2" style="border-top: 2px dotted rgba(0,0,0,0.15);">
                                <span class="region-stat-label">GP (Excl VAT)</span>
                                <span class="region-stat-val" style="font-size: 18px;">${formatCurrency(rd.gp)}</span>
                            </div>
                        </div>
                    </div>
                </div>
            `;
        });
        
        region_html += `</div>`;
        $('#regional-cards-container').html(region_html).show();
    }

    function render_table(rows) {
        let tbody = $('#margin-table-body');
        tbody.empty();

        if (!rows || rows.length === 0) {
            tbody.html('<tr><td colspan="12" class="text-center text-muted py-5">No deliveries found for the selected criteria.</td></tr>');
            return;
        }

        rows.forEach(row => {
            let gpClassInc = row.gross_margin >= 0 ? 'text-success-dark' : 'text-danger-dark';
            let gpClassEx = row.gp_ex_vat >= 0 ? 'text-success-dark' : 'text-danger-dark';
            
            let isZeroQty = row.qty === 0;
            let isReturn = row.qty < 0; 
            
            let rowClass = isZeroQty ? 'zero-qty-row' : (isReturn ? 'return-row' : '');
            
            let sourceHtml = row.cogs_source.split(', ').map(s => {
                let color = s === 'Price List' ? '#dbeafe' : s === 'System Valuation' ? '#fef3c7' : '#f1f5f9';
                let textCol = s === 'Price List' ? '#1e40af' : s === 'System Valuation' ? '#b45309' : '#475569';
                return `<span class="source-badge" style="background:${color}; color:${textCol};">${s}</span>`;
            }).join(' ');

            let returnBadge = isReturn ? `<span class="badge bg-danger ms-2">Return</span>` : '';

            let tr = `
                <tr class="${rowClass}">
                    <td>
                        <a href="${window.location.origin}/app/bom_cards#bom_cards?item_code=${encodeURIComponent(row.item_code)}" target="_blank" title="View BOM Formulation" class="btn-bom-action">BOM</a>
                    </td>
                    <td>
                        <div class="fw-bold text-dark">${row.item_code}</div>
                        <div class="text-muted small text-truncate" style="max-width: 180px;" title="${row.item_name}">${row.item_name}</div>
                    </td>
                    <td class="text-end fw-bold fs-6">${isZeroQty ? '<span class="text-muted">0</span>' : parseFloat(row.qty).toLocaleString()}</td>
                    <td class="text-end text-muted">${parseFloat(row.rate).toLocaleString()}</td>
                    <td class="text-end fw-bold text-dark">${parseFloat(row.total_amount).toLocaleString()}</td>
                    <td class="text-end text-muted">${parseFloat(row.unit_cogs).toLocaleString()}</td>
                    <td class="text-end border-end fw-bold">${isZeroQty ? '-' : parseFloat(row.total_cogs).toLocaleString()}</td>
                    <td class="text-end ${isZeroQty ? 'text-muted' : gpClassInc}">
                        ${isZeroQty ? '-' : parseFloat(row.gross_margin).toLocaleString()}
                    </td>
                    <td class="text-end fw-bold ${isZeroQty ? 'text-muted' : gpClassEx}" style="background-color: ${isReturn ? '#fee2e2' : '#f0fdf4'} !important; border-left: 2px solid ${isReturn ? '#fca5a5' : '#bbf7d0'};">
                        ${isZeroQty ? '-' : row.gp_percent.toFixed(2) + '%'}
                    </td>
                    <td>
                        <a href="/app/delivery-note/${row.delivery_note}" target="_blank" class="fw-bold">${row.delivery_note}</a> ${returnBadge}
                    </td>
                    <td class="text-muted">${frappe.datetime.str_to_user(row.posting_date)}</td>
                    <td>
                        <div class="fw-bold text-truncate" style="max-width: 150px;" title="${row.customer}">${row.customer}</div>
                        <span class="badge bg-light text-dark border mt-1">${row.region}</span>
                    </td>
                    <td>${sourceHtml}</td>
                </tr>
            `;
            tbody.append(tr);
        });
    }

    function export_to_csv(data_array) {
        if (!data_array || data_array.length === 0) {
            frappe.msgprint("No data available to export.");
            return;
        }

        let csv_content = "Delivery Note,Date,Customer,Region,Item Code,Item Name,Qty,Rate,Total Amount,Unit COGS,Total COGS,Gross Margin (Inc VAT),GP (Excl VAT),Margin % (Excl VAT),COGS Source\n";

        const escapeCSV = (str) => {
            if (str === null || str === undefined) return '""';
            let formatted = String(str).replace(/"/g, '""');
            return `"${formatted}"`;
        };

        data_array.forEach(row => {
            let rowData = [
                escapeCSV(row.delivery_note),
                escapeCSV(row.posting_date),
                escapeCSV(row.customer),
                escapeCSV(row.region),
                escapeCSV(row.item_code),
                escapeCSV(row.item_name),
                row.qty,
                row.rate,
                row.total_amount,
                row.unit_cogs,
                row.total_cogs,
                row.gross_margin,
                row.gp_ex_vat,
                row.gp_percent.toFixed(2) + '%',
                escapeCSV(row.cogs_source)
            ];
            csv_content += rowData.join(",") + "\n";
        });

        let blob = new Blob([csv_content], { type: 'text/csv;charset=utf-8;' });
        let url = URL.createObjectURL(blob);
        let link = document.createElement("a");
        let date_str = frappe.datetime.get_today();
        link.setAttribute("href", url);
        link.setAttribute("download", `Delivery_Margin_Report_${date_str}.csv`);
        document.body.appendChild(link);
        link.click();
        document.body.removeChild(link);
    }

    function fetch_report_data() {
        let from_date = from_date_field.get_value();
        let to_date = to_date_field.get_value();

        if (!from_date || !to_date) {
            frappe.msgprint("Please select both From and To dates.");
            return;
        }

        frappe.call({
            method: 'nexus_supply_chain.nexus_supply_chain.page.nexus_delivery_margin.nexus_delivery_margin.get_delivery_margin_data',
            args: {
                from_date: from_date,
                to_date: to_date
            },
            freeze: true,
            freeze_message: "Exploding formulations and calculating theoretical margins...",
            callback: function(r) {
                if (r.message && r.message.length > 0) {
                    all_report_data = r.message;
                    sort_state = { column: null, order: 'asc' };
                    $('.sort-icon').removeClass('active fa-sort-up fa-sort-down').addClass('fa-sort');
                    
                    process_and_render_data();
                } else {
                    $('#summary-cards-container').html(`
                        <div class="col-12">
                            <div class="empty-state-wrapper">
                                <i class="fa fa-exclamation-circle text-warning mb-3" style="font-size: 48px;"></i>
                                <h4 class="text-secondary fw-bold">No Data Found</h4>
                                <p class="text-muted">No committed ledger entries found for the selected period.</p>
                            </div>
                        </div>
                    `);
                    $('#regional-cards-container').hide();
                    $('#table-wrapper').hide();
                }
            }
        });
    }

};