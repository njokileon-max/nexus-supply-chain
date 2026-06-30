// apps/nexus_supply_chain/nexus_supply_chain/page/nexus_daily_overhead/nexus_daily_overhead.js

frappe.pages['nexus_daily_overhead'].on_page_load = function(wrapper) {
    let page = frappe.ui.make_app_page({
        parent: wrapper,
        title: 'Daily Overhead Command',
        single_column: true
    });

    page.nexus_cache = [];
    page.is_custom = false;

    let custom_btn = page.add_inner_button('Custom Audit Range', function() {
        toggle_custom_range();
    });

    // 100% String Concatenation - Immune to formatting issues
    const layout_html = [
        "<style>",
        "    .nexus-dashboard-wrapper { padding: 20px; background-color: #f8fafc; min-height: 85vh; font-family: 'Inter', -apple-system, sans-serif; }",
        "    .nexus-panel { background: #f1f5f9; padding: 20px; border-radius: 8px; margin-bottom: 20px; border: 1px solid #cbd5e1; border-left: 4px solid #0284c7; }",
        "    .kpi-grid { display: grid; grid-template-columns: repeat(4, 1fr); gap: 15px; margin-bottom: 20px; }",
        "    .kpi-card { background: #ffffff; padding: 20px; border-radius: 10px; box-shadow: 0 4px 15px rgba(0,0,0,0.04); border: 1px solid #e2e8f0; display: flex; flex-direction: column; justify-content: space-between; transition: transform 0.2s; }",
        "    .kpi-card:hover { transform: translateY(-3px); box-shadow: 0 6px 20px rgba(0,0,0,0.08); }",
        "    .kpi-title { font-size: 12px; font-weight: 800; color: #64748b; text-transform: uppercase; letter-spacing: 0.05em; margin-bottom: 10px; }",
        "    .kpi-val { font-size: 24px; font-weight: 900; color: #0f172a; line-height: 1.2; }",
        "    .kpi-sub { font-size: 11px; font-weight: 600; color: #94a3b8; margin-top: 8px; }",
        "    .kpi-highlight-blue { border-bottom: 4px solid #3b82f6; }",
        "    .kpi-highlight-orange { border-bottom: 4px solid #f59e0b; }",
        "    .kpi-highlight-green { border-bottom: 4px solid #10b981; }",
        "    .kpi-highlight-dark { border-bottom: 4px solid #0f172a; background: #0f172a; color: white !important; }",
        "    .kpi-highlight-dark .kpi-title, .kpi-highlight-dark .kpi-sub { color: #94a3b8 !important; }",
        "    .kpi-highlight-dark .kpi-val { color: #ffffff !important; }",
        "    .rm-grid { display: grid; grid-template-columns: repeat(3, 1fr); gap: 15px; margin-bottom: 25px; }",
        "    .rm-card { background: #fff; padding: 15px 20px; border-radius: 8px; border: 1px solid #e2e8f0; box-shadow: 0 2px 8px rgba(0,0,0,0.02); }",
        "    .rm-title { font-size: 11px; font-weight: 800; color: #64748b; text-transform: uppercase; margin-bottom: 5px; }",
        "    .rm-val { font-size: 20px; font-weight: 900; color: #0f172a; }",
        "    .health-grid { display: grid; grid-template-columns: repeat(3, 1fr); gap: 15px; margin-bottom: 15px; }",
        "    .health-card { background: #f1f5f9; padding: 15px 20px; border-radius: 8px; border-left: 4px solid #cbd5e1; display: flex; align-items: center; justify-content: space-between; }",
        "    .health-text { display: flex; flex-direction: column; }",
        "    .health-label { font-size: 12px; font-weight: 700; color: #475569; text-transform: uppercase; }",
        "    .health-val { font-size: 20px; font-weight: 900; color: #1e293b; }",
        "    .health-icon { font-size: 24px; color: #94a3b8; opacity: 0.5; }",
        "    .ledger-container { background: #ffffff; border-radius: 10px; padding: 20px; box-shadow: 0 4px 15px rgba(0,0,0,0.04); border: 1px solid #e2e8f0; }",
        "    .ledger-header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 15px; }",
        "    .search-input { width: 300px; border-radius: 20px; padding: 6px 15px; border: 1px solid #cbd5e1; font-size: 13px; font-weight: 600; outline: none; }",
        "    .search-input:focus { border-color: #3b82f6; box-shadow: 0 0 0 3px rgba(59, 130, 246, 0.1); }",
        "    .nexus-table { width: 100%; border-collapse: collapse; font-size: 13px; }",
        "    .nexus-table th { background: #f8fafc; padding: 12px 15px; text-align: left; font-weight: 800; color: #475569; border-bottom: 2px solid #cbd5e1; text-transform: uppercase; font-size: 11px; letter-spacing: 0.05em; }",
        "    .nexus-table td { padding: 12px 15px; border-bottom: 1px solid #e2e8f0; color: #1e293b; font-weight: 600; }",
        "    .nexus-table tr:hover { background: #f1f5f9; }",
        "    .val-currency { text-align: right !important; font-weight: 800 !important; color: #0f172a !important; font-family: monospace; font-size: 14px; }",
        "    .val-pct { text-align: center !important; font-weight: 800 !important; color: #3b82f6 !important; }",
        "    .pl-card-positive { background-color: #d1fae5; border: 1px solid #6ee7b7; color: #064e3b; }",
        "    .pl-card-negative { background-color: #fee2e2; border: 1px solid #fca5a5; color: #7f1d1d; }",
        "    .pl-label { font-size: 13px; font-weight: 800; text-transform: uppercase; margin-bottom: 8px; opacity: 0.85; }",
        "    .pl-value { font-size: 32px; font-weight: 900; line-height: 1.1; }",
        "    .pl-subtext { font-size: 13px; font-weight: 700; margin-top: 8px; opacity: 0.9; }",
        "</style>",
        
        "<div class='nexus-dashboard-wrapper'>",
        "    <div id='custom-date-panel' class='nexus-panel' style='display: none;'>",
        "        <h5 style='margin-top:0; font-weight:700; color: #0f172a;'>Custom Ledger Audit Range</h5>",
        "        <div style='display: flex; gap: 15px; align-items: flex-end;'>",
        "            <div><label style='font-size:12px; font-weight:600; color:#475569;'>From Date</label><br><input type='date' id='audit-start-date' class='form-control' style='width: 150px;'></div>",
        "            <div><label style='font-size:12px; font-weight:600; color:#475569;'>To Date</label><br><input type='date' id='audit-end-date' class='form-control' style='width: 150px;'></div>",
        "            <div><label style='font-size:12px; font-weight:600; color:#475569;'>Working Days</label><br><input type='number' id='audit-working-days' class='form-control' style='width: 100px;'></div>",
        "            <button class='btn btn-primary fw-bold' id='run-custom-audit'>Run Custom Audit</button>",
        "        </div>",
        "    </div>",
        "    <div id='dashboard-content'></div>",
        "</div>"
    ].join("");

    $(page.main).html(layout_html);

    function update_days_count() {
        let start = $('#audit-start-date').val();
        let end = $('#audit-end-date').val();
        if (start && end) {
            let d1 = new Date(start);
            let d2 = new Date(end);
            let days = 0;
            while (d1 <= d2) {
                if (d1.getDay() !== 0) days++; 
                d1.setDate(d1.getDate() + 1);
            }
            $('#audit-working-days').val(days > 0 ? days : 1);
        }
    }
    $('#audit-start-date, #audit-end-date').on('change', update_days_count);

    function toggle_custom_range() {
        let panel = $('#custom-date-panel');
        if(panel.is(':visible')) {
            panel.slideUp();
            custom_btn.text('Custom Audit Range');
            custom_btn.removeClass('btn-primary').addClass('btn-default');
            page.is_custom = false;
            
            $('#audit-start-date').val('');
            $('#audit-end-date').val('');
            $('#audit-working-days').val('');
            
            trigger_fetch(frappe.datetime.month_start(), frappe.datetime.get_today()); 
        } else {
            panel.slideDown();
            custom_btn.text('CURRENT AI ANALYSIS');
            custom_btn.removeClass('btn-default').addClass('btn-primary');
            page.is_custom = true;
            $('#dashboard-content').html('<div class="text-muted text-center" style="padding: 60px; font-size: 15px; border: 2px dashed #cbd5e1; border-radius: 8px;">Select dates and working days above, then click "Run Custom Audit".</div>');
        }
    }

    function trigger_fetch(start_date, end_date) {
        let custom_wd = page.is_custom ? $('#audit-working-days').val() : null;

        $('#dashboard-content').html('<div class="text-center" style="padding: 60px;"><div class="spinner-border text-primary" style="width: 3rem; height: 3rem;"></div><div class="mt-3 text-muted font-weight-bold fs-5">Compiling Active Ledger Arrays...</div></div>');
        
        frappe.call({
            method: "nexus_supply_chain.nexus_supply_chain.page.nexus_daily_overhead.nexus_daily_overhead.get_daily_overhead_data",
            args: { start_date: start_date, end_date: end_date, custom_working_days: custom_wd },
            callback: function(r) {
                if(r.message) {
                    page.nexus_cache = r.message.active_accounts;
                    render_dashboard_ui(r.message, start_date, end_date);
                }
            }
        });
    }

    $('#run-custom-audit').on('click', function() {
        let start = $('#audit-start-date').val();
        let end = $('#audit-end-date').val();
        if(!start || !end) { frappe.msgprint("Please select both dates."); return; }
        trigger_fetch(start, end);
    });

    function format_money(val) {
        return "KES " + parseFloat(val).toLocaleString('en-US', {minimumFractionDigits: 2, maximumFractionDigits: 2});
    }

    function render_dashboard_ui(data, start_date, end_date) {
        let o_pct = data.overhead_pct.toFixed(1);
        let m_pct = data.material_pct.toFixed(1);
        let audit_period_label = page.is_custom ? "Custom Audit: " + start_date + " to " + end_date : "Automatic System Audit: Month-To-Date";
        
        let pl_class = data.daily_profit_loss >= 0 ? 'pl-card-positive' : 'pl-card-negative';
        let pl_icon = data.daily_profit_loss >= 0 ? 'fa-arrow-trend-up' : 'fa-arrow-trend-down';
        let pl_status = data.daily_profit_loss >= 0 ? 'PROFIT' : 'LOSS';

        let cash_class = data.net_daily_cash_position >= 0 ? 'pl-card-positive' : 'pl-card-negative';
        let cash_icon = data.net_daily_cash_position >= 0 ? 'fa-money' : 'fa-exclamation-triangle';
        let cash_status = data.net_daily_cash_position >= 0 ? 'SURPLUS' : 'DEFICIT';
        
        let rm_status = data.rm_consumption_ratio < 100 ? "<span class='text-warning'><i class='fa fa-archive'></i> Stockpiling</span>" : "<span class='text-danger'><i class='fa fa-fire'></i> Depleting Reserves</span>";

        let ui_html = "" +
            "<div class='d-flex justify-content-between align-items-end mb-3'>" +
            "    <h5 class='fw-bold text-secondary mb-0'><i class='fa fa-calendar-check me-2'></i> " + audit_period_label + " <span class='ms-2 fw-bolder text-dark'>" + data.working_days_computed + " Working Days Evaluated</span></h5>" +
            "    <button class='btn btn-success btn-sm fw-bold px-3 shadow-sm' id='btn-export-excel'><i class='fa fa-file-excel me-1'></i> Export Live Ledger</button>" +
            "</div>" +
            
            // 🚨 NEW MARKET REVENUE BREAKDOWN ROW
            "<div class='rm-grid' style='margin-bottom: 20px;'>" +
            "    <div class='rm-card' style='border-top: 3px solid #3b82f6;'><div class='rm-title'>Total Sales Invoiced (Gross)</div><div class='rm-val text-primary'>" + format_money(data.total_invoiced_gross) + "</div><div class='text-muted small mt-1'>Raw Cash Billed to Customers</div></div>" +
            "    <div class='rm-card' style='border-top: 3px solid #ef4444;'><div class='rm-title'>VAT Liability (Output Tax)</div><div class='rm-val text-danger'>" + format_money(data.vat_liability) + "</div><div class='text-muted small mt-1'>Collected on behalf of KRA</div></div>" +
            "    <div class='rm-card' style='border-top: 3px solid #10b981;'><div class='rm-title'>Net Sales Revenue</div><div class='rm-val text-success'>" + format_money(data.total_invoiced_net) + "</div><div class='text-muted small mt-1'>Actual Company Wealth Generated</div></div>" +
            "</div>" +
            
            "<div class='kpi-grid'>" +
            "    <div class='kpi-card kpi-highlight-blue'><div class='kpi-title'>Factory Yield (FG Only)</div><div class='kpi-val'>" + parseFloat(data.mtd_yield_kg).toLocaleString() + " Kg</div><div class='kpi-sub'>Total Normalized Tonnage</div></div>" +
            "    <div class='kpi-card kpi-highlight-orange'><div class='kpi-title'>OpEx Overhead</div><div class='kpi-val'>" + format_money(data.mtd_total_overhead) + "</div><div class='kpi-sub'>Total Active General Ledger</div></div>" +
            "    <div class='kpi-card kpi-highlight-green'><div class='kpi-title'>Actual Material Value (GL)</div><div class='kpi-val'>" + format_money(data.mtd_actual_material_value) + "</div><div class='kpi-sub'>Booked Value at Production</div></div>" +
            "    <div class='kpi-card kpi-highlight-dark'><div class='kpi-title'>Total Value Manufactured</div><div class='kpi-val'>" + format_money(data.total_value_manufactured) + "</div><div class='kpi-sub'>Material Value + Operating Overheads</div></div>" +
            "</div>" +
            
            // 🚨 REFINED RM CASH PIPELINE (Gross Outflow vs Net Consumed)
            "<div class='rm-grid'>" +
            "    <div class='rm-card' style='border-top: 3px solid #f59e0b;'><div class='rm-title'>Raw Materials Purchased</div><div class='rm-val text-warning'>" + format_money(data.rm_purchased_gross) + "</div><div class='text-muted small mt-1'>Net Inventory Added: <span class='text-dark fw-bold'>" + format_money(data.rm_purchased_net) + "</span></div></div>" +
            "    <div class='rm-card' style='border-top: 3px solid #ef4444;'><div class='rm-title'>Raw Materials Consumed</div><div class='rm-val text-danger'>" + format_money(data.rm_consumed) + "</div><div class='text-muted small mt-1'>Net Inventory Dumped to Mixers</div></div>" +
            "    <div class='rm-card' style='border-top: 3px solid #64748b;'><div class='rm-title'>Consumption Ratio</div><div class='rm-val'>" + data.rm_consumption_ratio.toFixed(1) + "% <span style='font-size: 12px; margin-left: 5px; vertical-align: middle;'>" + rm_status + "</span></div><div class='text-muted small mt-1'>(Net Consumed &divide; Net Purchased)</div></div>" +
            "</div>" +
            
            "<div class='health-grid'>" +
            "    <div class='health-card' style='border-left-color: #ef4444;'><div class='health-text'><div class='health-label'>Daily Cash Burn Rate</div><div class='health-val'>" + format_money(data.daily_burn_rate) + " / Day</div></div><i class='fa fa-fire health-icon text-danger opacity-75'></i></div>" +
            "    <div class='health-card' style='border-left-color: #3b82f6;'><div class='health-text'><div class='health-label'>Overhead to Material Ratio</div><div class='health-val'>Overhead: " + o_pct + "% | Material: " + m_pct + "%</div></div><i class='fa fa-balance-scale health-icon text-primary opacity-75'></i></div>" +
            "    <div class='health-card' style='border-left-color: #8b5cf6;'><div class='health-text'><div class='health-label'>Prorated End-of-Month OpEx</div><div class='health-val'>" + format_money(data.prorated_projection) + "</div></div><i class='fa fa-chart-area health-icon text-purple opacity-75'></i></div>" +
            "</div>" +
            "<div class='health-grid' style='grid-template-columns: repeat(2, 1fr); margin-bottom: 30px;'>" +
            "    <div class='" + pl_class + "' style='padding: 20px; border-radius: 10px; display: flex; justify-content: space-between; align-items: center; box-shadow: 0 4px 10px rgba(0,0,0,0.03);'>" +
            "        <div><div class='pl-label'>True P&L Break-Even Velocity (Daily " + pl_status + ")</div><div class='pl-value'>" + format_money(data.daily_profit_loss) + "</div><div class='pl-subtext' style='font-size:12px;'>Avg Daily Margin (" + format_money(data.daily_margin) + ") - Cash Burn (" + format_money(data.daily_burn_rate) + ")</div></div>" +
            "        <i class='fa " + pl_icon + "' style='font-size: 45px; opacity: 0.2;'></i>" +
            "    </div>" +
            "    <div class='" + cash_class + "' style='position:relative; padding: 20px; border-radius: 10px; display: flex; flex-direction:column; justify-content: center; box-shadow: 0 4px 10px rgba(0,0,0,0.03);'>" +
            "        <div style='z-index: 2; width:100%;'>" +
            "            <div class='pl-label'>Net Cash Flow Velocity (Daily " + cash_status + ")</div>" +
            "            <div class='pl-value mb-2'>" + format_money(data.net_daily_cash_position) + "</div>" +
            "            <div class='p-3 rounded' style='background: rgba(255,255,255,0.6); border: 1px solid rgba(0,0,0,0.05); font-size:12.5px; font-weight:700; width:100%;'>" +
            "                <div class='d-flex justify-content-between mb-1'><span class='text-muted'>Daily Sales (Gross):</span><span class='text-dark'>" + format_money(data.daily_invoiced_gross) + "</span></div>" +
            "                <div class='d-flex justify-content-between mb-1'><span class='text-muted'>Daily RM Purchases (Gross):</span><span class='text-danger'>- " + format_money(data.daily_rm_purchased_gross) + "</span></div>" +
            "                <div class='d-flex justify-content-between'><span class='text-muted'>Daily Overheads:</span><span class='text-danger'>- " + format_money(data.daily_burn_rate) + "</span></div>" +
            "            </div>" +
            "        </div>" +
            "        <i class='fa " + cash_icon + "' style='font-size: 60px; opacity: 0.1; position:absolute; right:20px; top:20px; z-index:1;'></i>" +
            "    </div>" +
            "</div>" +
            "<div style='background: #f8fafc; border: 1px solid #cbd5e1; padding: 20px; border-radius: 10px; display: flex; justify-content: space-between; align-items: center; box-shadow: 0 4px 10px rgba(0,0,0,0.02); margin-bottom: 25px;'>" +
            "    <div><div class='pl-label' style='color: #475569;'>Period Gross Margin Spread</div><div class='pl-value' style='color: #0f172a;'>" + data.margin_pct.toFixed(1) + "%</div><div class='pl-subtext' style='color: #64748b;'>Net Sales (" + format_money(data.total_invoiced_net) + ") - GL COGS (" + format_money(data.total_cogs) + ")</div></div>" +
            "    <i class='fa fa-percent' style='font-size: 40px; color: #94a3b8; opacity: 0.3;'></i>" +
            "</div>" +
            "<div class='ledger-container'>" +
            "    <div class='ledger-header'>" +
            "        <h5 class='fw-bold mb-0 text-dark'><i class='fa fa-list-alt me-2 text-secondary'></i> Active General Ledger Trajectory</h5>" +
            "        <input type='text' id='ledger-search' class='search-input' placeholder='&#128269; Quick Filter Account...'>" +
            "    </div>" +
            "    <div style='overflow-x: auto; max-height: 500px; overflow-y: auto;'>" +
            "        <table class='nexus-table' id='expense-table'>" +
            "            <thead><tr><th style='width: 50px;'>Rank</th><th style='width: 150px;'>Account No.</th><th>Account Name</th><th class='val-currency'>Net Expense (KES)</th><th class='val-pct'>% of Total</th></tr></thead>" +
            "            <tbody id='expense-tbody'></tbody>" +
            "        </table>" +
            "    </div>" +
            "</div>";

        $('#dashboard-content').html(ui_html);
        render_table(page.nexus_cache, start_date, end_date);

        $('#ledger-search').on('keyup', function() {
            let term = $(this).val().toLowerCase();
            let filtered = page.nexus_cache.filter(row => 
                row.account_name.toLowerCase().includes(term) || 
                (row.account_number && row.account_number.toLowerCase().includes(term))
            );
            render_table(filtered, start_date, end_date);
        });

        $('#btn-export-excel').on('click', function() {
            if (!page.nexus_cache || page.nexus_cache.length === 0) return;
            let csvContent = "Rank,Account Number,Account Name,Net Expense (KES),% of Total\n";
            page.nexus_cache.forEach((row, i) => {
                let acctNum = row.account_number ? '="' + row.account_number + '"' : ""; 
                let cleanName = row.account_name.replace(/,/g, ""); 
                csvContent += (i+1) + "," + acctNum + "," + cleanName + "," + row.net_expense + "," + row.pct_of_total + "\n";
            });
            const blob = new Blob([csvContent], { type: 'text/csv;charset=utf-8;' });
            const url = URL.createObjectURL(blob);
            const link = document.createElement("a");
            link.setAttribute("href", url);
            let suffix = page.is_custom ? `${start_date}_to_${end_date}` : `MTD_${frappe.datetime.get_today()}`;
            link.setAttribute("download", `Overhead_Ledger_${suffix}.csv`);
            document.body.appendChild(link);
            link.click();
            document.body.removeChild(link);
        });
    }

    function render_table(expenses, start_date, end_date) {
        let html = '';
        if (expenses.length === 0) {
            html = "<tr><td colspan='5' class='text-center py-4 text-muted fw-bold'>No expense transactions found for this date range.</td></tr>";
        } else {
            expenses.forEach((row, index) => {
                let acct_link = "<a href='/app/query-report/General%20Ledger?account=" + encodeURIComponent(row.account_id) + "&from_date=" + start_date + "&to_date=" + end_date + "' target='_blank' style='text-decoration: none; font-weight: 800; color: #0284c7;'>" + (row.account_number || 'View') + "</a>";
                html += "<tr><td class='text-muted fw-bold'>#" + (index + 1) + "</td><td>" + acct_link + "</td><td>" + row.account_name + "</td><td class='val-currency'>" + format_money(row.net_expense) + "</td><td class='val-pct'><span style='background: #e0f2fe; padding: 3px 8px; border-radius: 12px; font-size: 11px;'>" + row.pct_of_total.toFixed(2) + "%</span></td></tr>";
            });
        }
        $('#expense-tbody').html(html);
    }

    setTimeout(() => {
        trigger_fetch(frappe.datetime.month_start(), frappe.datetime.get_today());
    }, 300);
};