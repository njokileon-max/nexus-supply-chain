// apps/nexus_supply_chain/nexus_supply_chain/page/nexus_executive_command/nexus_executive_command.js

frappe.pages['nexus_executive_command'].on_page_load = function(wrapper) {
    let page = frappe.ui.make_app_page({
        parent: wrapper,
        title: 'Executive Operations Command',
        single_column: true
    });

    page.is_custom = false;

    let custom_btn = page.add_inner_button('Custom Audit Range', function() {
        toggle_custom_range();
    });

    // --- Layout & Styles ---
    const layout_html = [
        "<style>",
        "    .exec-wrap { padding: 20px; background: #f8fafc; min-height: 85vh; font-family: 'Inter', sans-serif; }",
        "    .nexus-panel { background: #f1f5f9; padding: 20px; border-radius: 8px; margin-bottom: 20px; border: 1px solid #cbd5e1; border-left: 4px solid #0284c7; }",
        "    .exec-ribbon { display: grid; grid-template-columns: repeat(3, 1fr); gap: 15px; margin-bottom: 25px; }",
        "    .kpi-card { background: #fff; padding: 20px; border-radius: 10px; border: 1px solid #e2e8f0; box-shadow: 0 4px 10px rgba(0,0,0,0.03); }",
        "    .kpi-title { font-size: 11.5px; font-weight: 800; color: #64748b; text-transform: uppercase; margin-bottom: 8px; }",
        "    .kpi-val { font-size: 30px; font-weight: 900; color: #0f172a; line-height: 1.1; }",
        "    .kpi-sub { font-size: 13.5px; font-weight: 700; margin-top: 10px; border-top: 1px solid #f1f5f9; padding-top: 8px; }",
        "    .val-green { color: #059669; } .val-red { color: #dc2626; } .val-blue { color: #2563eb; } .val-orange { color: #d97706; }",
        "    .exec-grid { display: flex; flex-direction: column; gap: 20px; margin-bottom: 25px; }",
        "    .panel { background: #fff; border-radius: 10px; padding: 20px; border: 1px solid #e2e8f0; box-shadow: 0 4px 10px rgba(0,0,0,0.03); }",
        "    .panel-header { font-size: 16px; font-weight: 800; color: #0f172a; margin-bottom: 15px; display: flex; justify-content: space-between; align-items: center; }",
        "    .live-feed-table, .group-table { width: 100%; border-collapse: collapse; font-size: 14px; }",
        "    .live-feed-table th, .group-table th { background: #f1f5f9; padding: 12px 10px; text-align: left; font-weight: 800; color: #475569; text-transform: uppercase; font-size: 12px; border: 1px solid #e2e8f0; }",
        "    .live-feed-table td, .group-table td { padding: 12px 10px; font-weight: 600; color: #1e293b; border: 1px solid #e2e8f0; }",
        "    .live-dot { height: 10px; width: 10px; background-color: #10b981; border-radius: 50%; display: inline-block; animation: pulse 2s infinite; }",
        "    @keyframes pulse { 0% { box-shadow: 0 0 0 0 rgba(16, 185, 129, 0.7); } 70% { box-shadow: 0 0 0 6px rgba(16, 185, 129, 0); } 100% { box-shadow: 0 0 0 0 rgba(16, 185, 129, 0); } }",
        "    .clean-period-label { background-color: #ffffff; color: #000000; font-weight: 900; font-size: 14px; padding: 8px 15px; border: 1px solid #d1d5db; border-radius: 6px; box-shadow: 0 1px 2px rgba(0,0,0,0.05); }",
        "</style>",
        "<div class='exec-wrap'>",
        "    <div id='custom-date-panel' class='nexus-panel' style='display: none;'>",
        "        <h5 style='margin-top:0; font-weight:700; color: #0f172a;'>Custom Command Audit Range</h5>",
        "        <div style='display: flex; gap: 15px; align-items: flex-end;'>",
        "            <div><label style='font-size:12px; font-weight:600; color:#475569;'>From Date</label><br><input type='date' id='exec-start-date' class='form-control' style='width: 150px;'></div>",
        "            <div><label style='font-size:12px; font-weight:600; color:#475569;'>To Date</label><br><input type='date' id='exec-end-date' class='form-control' style='width: 150px;'></div>",
        "            <div><label style='font-size:12px; font-weight:600; color:#475569;'>Working Days</label><br><input type='number' id='exec-working-days' class='form-control' style='width: 100px;'></div>",
        "            <button class='btn btn-primary fw-bold' id='run-exec-audit'>Run Analysis</button>",
        "        </div>",
        "    </div>",
        "    <div class='d-flex justify-content-between align-items-center mb-3'>",
        "        <h4 class='fw-bold text-dark mb-0' id='exec-title'>Month-To-Date Snapshot</h4>",
        "        <span class='clean-period-label' id='period-label'>Loading...</span>",
        "    </div>",
        "    <div id='dashboard-content'>",
        "        <div class='text-center py-5 text-muted'><i class='fa fa-spin fa-spinner fa-2x mb-3'></i><br><b>Initializing Executive Data Graph...</b></div>",
        "    </div>",
        "</div>"
    ].join("");

    $(page.main).html(layout_html);

    // --- Dynamic Working Days Auto-Calculator ---
    function update_days_count() {
        let start = $('#exec-start-date').val();
        let end = $('#exec-end-date').val();
        if (start && end) {
            let d1 = new Date(start);
            let d2 = new Date(end);
            let days = 0;
            while (d1 <= d2) {
                if (d1.getDay() !== 0) days++; // 0 = Sunday
                d1.setDate(d1.getDate() + 1);
            }
            $('#exec-working-days').val(days > 0 ? days : 1);
        }
    }
    $('#exec-start-date, #exec-end-date').on('change', update_days_count);

    // --- State Management ---
    function toggle_custom_range() {
        let panel = $('#custom-date-panel');
        if(panel.is(':visible')) {
            panel.slideUp();
            custom_btn.text('Custom Audit Range');
            custom_btn.removeClass('btn-primary').addClass('btn-default');
            page.is_custom = false;
            $('#exec-title').text('Month-To-Date Snapshot');
            
            $('#exec-start-date').val('');
            $('#exec-end-date').val('');
            $('#exec-working-days').val('');
            trigger_fetch(null, null); 
        } else {
            panel.slideDown();
            custom_btn.text('CURRENT AI ANALYSIS');
            custom_btn.removeClass('btn-default').addClass('btn-primary');
            page.is_custom = true;
            $('#exec-title').text('Custom Executive Audit');
            $('#dashboard-content').html('<div class="text-muted text-center" style="padding: 60px; font-size: 15px; border: 2px dashed #cbd5e1; border-radius: 8px;">Select dates and working days above, then click "Run Analysis".</div>');
            $('#period-label').text("Awaiting Input...");
        }
    }

    // --- Helper Functions ---
    function fmt_curr(val) { return "KES " + parseFloat(val).toLocaleString('en-US', {minimumFractionDigits: 2, maximumFractionDigits: 2}); }
    function fmt_num(val) { return parseFloat(val).toLocaleString('en-US', {minimumFractionDigits: 1, maximumFractionDigits: 1}); }

    // --- Core Data Fetch & Render ---
    function trigger_fetch(start_date, end_date) {
        let custom_wd = page.is_custom ? $('#exec-working-days').val() : null;

        $('#dashboard-content').html('<div class="text-center py-5 text-muted"><i class="fa fa-spin fa-spinner fa-2x mb-3"></i><br><b>Compiling Executive Data Graph...</b></div>');

        frappe.call({
            method: "nexus_supply_chain.nexus_supply_chain.page.nexus_executive_command.nexus_executive_command.get_executive_dashboard_data",
            args: { start_date: start_date, end_date: end_date, custom_working_days: custom_wd },
            callback: function(r) {
                if(r.message) render_ui(r.message);
            }
        });
    }

    $('#run-exec-audit').on('click', function() {
        let start = $('#exec-start-date').val();
        let end = $('#exec-end-date').val();
        if(!start || !end) { frappe.msgprint("Please select both dates."); return; }
        trigger_fetch(start, end);
    });

    function render_ui(data) {
        $('#period-label').html("Period: " + data.period + " <span class='ms-2 px-2' style='border-left: 2px solid #cbd5e1;'>" + data.working_days_computed + " Working Days</span>");

        // Zone 1: Executive Ribbon (6 Cards)
        let ribbon_html = "" +
            "<div class='exec-ribbon'>" +
            "    <div class='kpi-card' style='border-top: 4px solid #3b82f6;'>" +
            "        <div class='kpi-title'>Net Sales Revenue</div>" +
            "        <div class='kpi-val'>" + fmt_curr(data.total_sales_revenue) + "</div>" +
            "        <div class='kpi-sub val-blue'>GL COGS: " + fmt_curr(data.total_cogs) + "</div>" +
            "    </div>" +
            "    <div class='kpi-card' style='border-top: 4px solid #10b981;'>" +
            "        <div class='kpi-title'>Gross Margin (GL)</div>" +
            "        <div class='kpi-val " + (data.gross_margin_pct >= 25 ? "val-green" : "val-red") + "'>" + data.gross_margin_pct.toFixed(1) + "%</div>" +
            "        <div class='kpi-sub text-muted'>Operating Overheads: <span style='color:#0f172a;'>" + fmt_curr(data.total_overhead) + "</span></div>" +
            "    </div>" +
            "    <div class='kpi-card' style='border-top: 4px solid #8b5cf6;'>" +
            "        <div class='kpi-title'>Sell-Through Rate</div>" +
            "        <div class='kpi-val'>" + data.sell_through_pct.toFixed(1) + "%</div>" +
            "        <div class='kpi-sub text-muted'>Value Manufactured: <span style='color:#0f172a;'>" + fmt_curr(data.net_value_manufactured) + "</span></div>" +
            "    </div>" +
            "    <div class='kpi-card' style='border-top: 4px solid #0f172a;'>" +
            "        <div class='kpi-title'>Total FG Cash Value</div>" +
            "        <div class='kpi-val'>" + fmt_curr(data.total_fg_store_value) + "</div>" +
            "        <div class='kpi-sub text-muted'>Live Global Warehouse Snapshot</div>" +
            "    </div>" +
            "    <div class='kpi-card' style='border-top: 4px solid #f59e0b;'>" +
            "        <div class='kpi-title'>Raw Materials Purchased</div>" +
            "        <div class='kpi-val'>" + fmt_curr(data.rm_purchased) + "</div>" +
            "        <div class='kpi-sub text-muted'>Total Invoiced RM Intake</div>" +
            "    </div>" +
            "    <div class='kpi-card' style='border-top: 4px solid #ef4444;'>" +
            "        <div class='kpi-title'>Raw Materials Consumed</div>" +
            "        <div class='kpi-val'>" + fmt_curr(data.rm_consumed) + "</div>" +
            "        <div class='kpi-sub text-muted'>Consumption Ratio: <span style='color:#0f172a;'>" + data.rm_consumption_ratio.toFixed(1) + "%</span></div>" +
            "    </div>" +
            "</div>";

        // Zone 2: Capacity Distribution (Pure FG Layout + Auto-Fetched Landed Rates)
        let group_rows = "";
        data.item_group_distribution.forEach(row => {
            let rate_display = row.rate_per_kg > 0 ? fmt_curr(row.rate_per_kg) : "<span class='text-muted'>0.00</span>";
            group_rows += "<tr>" +
                "<td>" + row.group + "</td>" +
                "<td class='text-end'>" + fmt_num(row.kg) + " Kg</td>" +
                "<td class='text-end val-blue'>" + row.pct.toFixed(1) + "%</td>" +
                "<td class='text-end fw-bold'>" + rate_display + "</td>" +
                "</tr>";
        });

        if (group_rows === "") {
            group_rows = "<tr><td colspan='4' class='text-center py-4 text-muted'>No capacity data available for this period.</td></tr>";
        } else {
            group_rows += "<tr style='background: #f0fdf4; border-top: 2px solid #10b981; border-bottom: 2px solid #10b981; font-weight: 900; color: #064e3b; font-size: 15px;'>" +
                "<td class='text-uppercase'>TOTAL FINISHED GOODS YIELD</td>" +
                "<td class='text-end'>" + fmt_num(data.net_fg_yield_kg) + " Kg</td>" +
                "<td class='text-end'>100.0%</td>" +
                "<td class='text-end'>-</td>" +
                "</tr>";
        }

        // Zone 3: Live Feed Table
        let feed_rows = "";
        data.recent_stock_entries.forEach(row => {
            let entry_link = "<a href='/app/stock-entry/" + row.name + "' target='_blank' style='text-decoration:none; font-weight:800; color:#0284c7;'>" + row.name + "</a>";
            feed_rows += "<tr>" +
                "<td>" + entry_link + "</td>" +
                "<td>" + row.posting_date + " <span class='text-muted ms-1'>" + row.posting_time.substring(0,5) + "</span></td>" +
                "<td>" + row.item_code + "</td>" +
                "<td>" + row.item_name + "</td>" +
                "<td class='text-end'>" + fmt_num(row.yield_kg) + " Kg</td>" +
                "<td class='text-end val-green fw-bold'>" + fmt_curr(row.total_value_kes) + "</td>" +
                "</tr>";
        });

        if (feed_rows === "") {
            feed_rows = "<tr><td colspan='6' class='text-center py-4 text-muted'>No production activity in this period.</td></tr>";
        }

        let grid_html = "" +
            "<div class='exec-grid'>" +
            "    <div class='panel'>" +
            "        <div class='panel-header'>Capacity Yield Distribution (FG Only)</div>" +
            "        <div style='overflow-y:auto; max-height:400px;'>" +
            "            <table class='group-table'>" +
            "                <thead><tr><th>Item Group</th><th class='text-end'>Volume</th><th class='text-end'>% of Yield</th><th class='text-end'>Rate (KES/Kg)</th></tr></thead>" +
            "                <tbody>" + group_rows + "</tbody>" +
            "            </table>" +
            "        </div>" +
            "    </div>" +
            "    <div class='panel'>" +
            "        <div class='panel-header'><span><span class='live-dot me-2'></span>Live Manufacturing Log</span> <span class='badge bg-light text-dark border'>" + fmt_num(data.net_fg_yield_kg) + " Kg FG Yield</span></div>" +
            "        <div style='overflow-y:auto; max-height:500px;'>" +
            "            <table class='live-feed-table'>" +
            "                <thead><tr><th>Entry ID</th><th>Date/Time</th><th>Item Code</th><th>Item Name</th><th class='text-end'>Yield (Kg)</th><th class='text-end'>Computed Value (KES)</th></tr></thead>" +
            "                <tbody>" + feed_rows + "</tbody>" +
            "            </table>" +
            "        </div>" +
            "    </div>" +
            "</div>";

        $('#dashboard-content').html(ribbon_html + grid_html);
    }

    // --- WebSockets: The 0-Lag Auto Update Trigger ---
    frappe.realtime.on('nexus_production_sync', function(event_data) {
        // Only trigger background refresh if we are in the default "Live MTD" view
        if(!page.is_custom) {
            trigger_fetch(null, null);
            frappe.show_alert({message: "Production Ledger Updated. Dashboard synced.", indicator: 'green'});
        }
    });

    // Initial Load
    setTimeout(() => { trigger_fetch(null, null); }, 300);
};