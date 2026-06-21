// gnleon29@gmail.com

frappe.pages['nexus_treasury_command'].on_page_load = function(wrapper) {
    let page = frappe.ui.make_app_page({
        parent: wrapper,
        title: 'Treasury & Liquidity Command',
        single_column: true
    });

    page.is_custom = false;

    let custom_btn = page.add_inner_button('Custom Audit Range', function() {
        toggle_custom_range();
    });

    const layout_html = [
        "<style>",
        "    .treasury-wrap { padding: 20px; background: #f8fafc; min-height: 85vh; font-family: 'Inter', sans-serif; }",
        "    .nexus-panel { background: #f1f5f9; padding: 20px; border-radius: 8px; margin-bottom: 20px; border: 1px solid #cbd5e1; border-left: 4px solid #0284c7; }",
        "    .t-ribbon { display: grid; grid-template-columns: repeat(4, 1fr); gap: 15px; margin-bottom: 25px; }",
        "    .t-card { background: #fff; padding: 20px; border-radius: 10px; border: 1px solid #e2e8f0; box-shadow: 0 4px 10px rgba(0,0,0,0.03); }",
        "    .t-title { font-size: 11.5px; font-weight: 800; color: #64748b; text-transform: uppercase; margin-bottom: 8px; }",
        "    .t-val { font-size: 30px; font-weight: 900; color: #0f172a; line-height: 1.1; }",
        "    .t-sub { font-size: 13.5px; font-weight: 700; margin-top: 10px; border-top: 1px solid #f1f5f9; padding-top: 8px; }",
        "    .val-green { color: #059669; } .val-red { color: #dc2626; } .val-blue { color: #2563eb; } .val-orange { color: #d97706; }",
        "    .t-grid { display: flex; flex-direction: column; gap: 20px; margin-bottom: 25px; }",
        "    .t-split { display: grid; grid-template-columns: 1fr 1fr; gap: 20px; }",
        "    .panel { background: #fff; border-radius: 10px; padding: 20px; border: 1px solid #e2e8f0; box-shadow: 0 4px 10px rgba(0,0,0,0.03); }",
        "    .panel-header { font-size: 16px; font-weight: 800; color: #0f172a; margin-bottom: 15px; display: flex; justify-content: space-between; align-items: center; border-bottom: 2px solid #f1f5f9; padding-bottom: 10px; }",
        "    .t-table { width: 100%; border-collapse: collapse; font-size: 14px; }",
        "    .t-table th { background: #f8fafc; padding: 12px 10px; text-align: left; font-weight: 800; color: #475569; text-transform: uppercase; font-size: 11px; }",
        "    .t-table td { padding: 12px 10px; border-bottom: 1px solid #f1f5f9; font-weight: 600; color: #1e293b; }",
        "    .row-red { background-color: #fef2f2; color: #991b1b !important; }",
        "    .row-orange { background-color: #fffbeb; color: #92400e !important; }",
        "    .clean-badge { background: #ffffff; color: #0f172a; border: 1px solid #cbd5e1; padding: 6px 12px; border-radius: 6px; font-weight: 800; font-size: 13.5px; box-shadow: 0 1px 3px rgba(0,0,0,0.03); }",
        "</style>",
        "<div class='treasury-wrap'>",
        "    <div id='custom-date-panel' class='nexus-panel' style='display: none;'>",
        "        <h5 style='margin-top:0; font-weight:700; color: #0f172a;'>Custom Treasury Audit Range</h5>",
        "        <div style='display: flex; gap: 15px; align-items: flex-end;'>",
        "            <div><label style='font-size:12px; font-weight:600; color:#475569;'>From Date</label><br><input type='date' id='t-start-date' class='form-control' style='width: 150px;'></div>",
        "            <div><label style='font-size:12px; font-weight:600; color:#475569;'>To Date</label><br><input type='date' id='t-end-date' class='form-control' style='width: 150px;'></div>",
        "            <div><label style='font-size:12px; font-weight:600; color:#475569;'>Working Days</label><br><input type='number' id='t-working-days' class='form-control' style='width: 100px;'></div>",
        "            <button class='btn btn-primary fw-bold' id='run-t-audit'>Run Liquidity Audit</button>",
        "        </div>",
        "    </div>",
        "    <div class='d-flex justify-content-between align-items-center mb-3'>",
        "        <h4 class='fw-bold text-dark mb-0' id='t-title'>Liquidity & Debt Snapshot</h4>",
        "        <span class='clean-badge' id='period-label'>Loading...</span>",
        "    </div>",
        "    <div id='dashboard-content'>",
        "        <div class='text-center py-5 text-muted'><i class='fa fa-spin fa-spinner fa-2x mb-3'></i><br><b>Initializing Treasury Ledger...</b></div>",
        "    </div>",
        "</div>"
    ].join("");

    $(page.main).html(layout_html);

    function update_days_count() {
        let start = $('#t-start-date').val();
        let end = $('#t-end-date').val();
        if (start && end) {
            let d1 = new Date(start);
            let d2 = new Date(end);
            let days = 0;
            while (d1 <= d2) {
                if (d1.getDay() !== 0) days++; 
                d1.setDate(d1.getDate() + 1);
            }
            $('#t-working-days').val(days > 0 ? days : 1);
        }
    }
    $('#t-start-date, #t-end-date').on('change', update_days_count);

    function toggle_custom_range() {
        let panel = $('#custom-date-panel');
        if(panel.is(':visible')) {
            panel.slideUp();
            custom_btn.text('Custom Audit Range');
            custom_btn.removeClass('btn-primary').addClass('btn-default');
            page.is_custom = false;
            $('#t-title').text('Liquidity & Debt Snapshot');
            $('#t-start-date').val('');
            $('#t-end-date').val('');
            $('#t-working-days').val('');
            trigger_fetch(null, null); 
        } else {
            panel.slideDown();
            custom_btn.text('CURRENT AI ANALYSIS');
            custom_btn.removeClass('btn-default').addClass('btn-primary');
            page.is_custom = true;
            $('#t-title').text('Custom Treasury Audit');
            $('#dashboard-content').html('<div class="text-muted text-center" style="padding: 60px; font-size: 15px; border: 2px dashed #cbd5e1; border-radius: 8px;">Select dates and working days above, then click "Run Liquidity Audit".</div>');
            $('#period-label').text("Awaiting Input...");
        }
    }

    function fmt_curr(val) { return "KES " + parseFloat(val).toLocaleString('en-US', {minimumFractionDigits: 2, maximumFractionDigits: 2}); }
    
    function format_days_status(days_past_due) {
        let d = parseInt(days_past_due);
        if (d > 0) return "<span class='val-red fw-bold'><i class='fa fa-exclamation-circle'></i> " + d + " Days Overdue</span>";
        if (d === 0) return "<span class='val-orange fw-bold'><i class='fa fa-clock-o'></i> Due Today</span>";
        let to_due = Math.abs(d);
        if (to_due <= 7) return "<span class='val-orange fw-bold'>Due in " + to_due + " Days</span>";
        return "<span class='val-green fw-bold'>Due in " + to_due + " Days</span>";
    }

    function get_row_class(days_past_due) {
        let d = parseInt(days_past_due);
        if (d > 0) return "row-red";
        if (d > -7) return "row-orange"; 
        return "";
    }

    function trigger_fetch(start_date, end_date) {
        let custom_wd = page.is_custom ? $('#t-working-days').val() : null;
        $('#dashboard-content').html('<div class="text-center py-5 text-muted"><i class="fa fa-spin fa-spinner fa-2x mb-3"></i><br><b>Compiling Treasury Ledger...</b></div>');

        frappe.call({
            method: "nexus_supply_chain.nexus_supply_chain.page.nexus_treasury_command.nexus_treasury_command.get_treasury_dashboard_data",
            args: { start_date: start_date, end_date: end_date, custom_working_days: custom_wd },
            callback: function(r) {
                if(r.message) render_ui(r.message);
            }
        });
    }

    $('#run-t-audit').on('click', function() {
        let start = $('#t-start-date').val();
        let end = $('#t-end-date').val();
        if(!start || !end) { frappe.msgprint("Please select both dates."); return; }
        trigger_fetch(start, end);
    });

    function render_ui(data) {
        $('#period-label').html("Analysis End Date: " + data.period.split(" to ")[1] + " <span class='ms-2 px-2' style='border-left: 2px solid #cbd5e1;'>Burn Based On: " + data.working_days + " Days</span>");

        let runway_color = data.cash_runway_days < 30 ? "val-red" : "val-green";
        let ratio_color = data.quick_ratio < 1.0 ? "val-red" : "val-green";

        let ribbon_html = "" +
            "<div class='t-ribbon'>" +
            "    <div class='t-card' style='border-top: 4px solid #10b981;'>" +
            "        <div class='t-title'>Liquid War Chest (Cash & Bank)</div>" +
            "        <div class='t-val val-green'>" + fmt_curr(data.total_liquid_cash) + "</div>" +
            "        <div class='t-sub text-muted'>Total Available Funds</div>" +
            "    </div>" +
            "    <div class='t-card' style='border-top: 4px solid #dc2626;'>" +
            "        <div class='t-title'>Accounts Payable (We Owe)</div>" +
            "        <div class='t-val val-red'>" + fmt_curr(data.total_ap) + "</div>" +
            "        <div class='t-sub text-muted'>Supplier Debt | Acid Ratio: <span class='" + ratio_color + " fw-bold'>" + data.quick_ratio.toFixed(2) + "</span></div>" +
            "    </div>" +
            "    <div class='t-card' style='border-top: 4px solid #3b82f6;'>" +
            "        <div class='t-title'>Accounts Receivable (Owed To Us)</div>" +
            "        <div class='t-val val-blue'>" + fmt_curr(data.total_ar) + "</div>" +
            "        <div class='t-sub text-muted'>Customer Debt Outstanding</div>" +
            "    </div>" +
            "    <div class='t-card' style='border-top: 4px solid #0f172a;'>" +
            "        <div class='t-title'>Cash Runway (Survival Days)</div>" +
            "        <div class='t-val " + runway_color + "'>" + data.cash_runway_days.toFixed(1) + " Days</div>" +
            "        <div class='t-sub text-muted'>Avg Burn: <span style='color:#0f172a;'>" + fmt_curr(data.daily_cash_burn) + " / Day</span><br>Total Period Overheads: <span style='color:#0f172a;'>" + fmt_curr(data.mtd_overhead) + "</span></div>" +
            "    </div>" +
            "</div>";

        let bank_rows = "";
        data.bank_accounts.forEach(row => {
            bank_rows += "<tr><td><i class='fa fa-university text-muted me-2'></i>" + row.account_name + "</td><td class='text-end fw-bold val-green'>" + fmt_curr(row.balance) + "</td></tr>";
        });
        
        let ar_danger_rows = "";
        data.top_debtors.forEach(row => {
            ar_danger_rows += "<tr><td>" + row.entity_name + "</td><td class='text-end fw-bold val-red'>" + fmt_curr(row.amount) + "</td></tr>";
        });
        if(ar_danger_rows === "") ar_danger_rows = "<tr><td colspan='2' class='text-muted text-center'>No overdue customer debt.</td></tr>";

        let ap_danger_rows = "";
        data.top_creditors.forEach(row => {
            ap_danger_rows += "<tr><td>" + row.entity_name + "</td><td class='text-end fw-bold val-red'>" + fmt_curr(row.amount) + "</td></tr>";
        });
        if(ap_danger_rows === "") ap_danger_rows = "<tr><td colspan='2' class='text-muted text-center'>No overdue supplier debt.</td></tr>";

        let matrix_html = "" +
            "<div class='t-split mb-4'>" +
            "    <div class='panel'>" +
            "        <div class='panel-header'>Active Bank & Cash Vaults</div>" +
            "        <table class='t-table'><tbody>" + bank_rows + "</tbody></table>" +
            "    </div>" +
            "    <div class='panel' style='border: 1px solid #fecaca; background: #fff5f5;'>" +
            "        <div class='panel-header text-danger'><i class='fa fa-warning me-2'></i> The Overdue Danger Matrix</div>" +
            "        <div class='d-flex gap-3'>" +
            "            <div style='flex:1;'><h6 class='fw-bold text-muted mb-2' style='font-size:12px;'>TOP 5 DELINQUENT CUSTOMERS</h6><table class='t-table' style='background:transparent;'><tbody>" + ar_danger_rows + "</tbody></table></div>" +
            "            <div style='flex:1;'><h6 class='fw-bold text-muted mb-2' style='font-size:12px;'>TOP 5 ANGRY SUPPLIERS</h6><table class='t-table' style='background:transparent;'><tbody>" + ap_danger_rows + "</tbody></table></div>" +
            "        </div>" +
            "    </div>" +
            "</div>";

        let ar_rows = "";
        data.sales_invoices.forEach(row => {
            let row_cls = get_row_class(row.days_past_due);
            let link = "<a href='/app/sales-invoice/" + row.id + "' target='_blank' style='text-decoration:none; font-weight:800; color:#0284c7;'>" + row.id + "</a>";
            ar_rows += "<tr class='" + row_cls + "'>" +
                "<td>" + link + "</td><td>" + row.entity_id + "</td><td>" + (row.entity_name || row.entity_id) + "</td><td>" + row.posting_date + "</td><td>" + row.due_date + "</td>" +
                "<td>" + format_days_status(row.days_past_due) + "</td><td class='text-end fw-bold'>" + fmt_curr(row.outstanding_amount) + "</td></tr>";
        });
        if(ar_rows === "") ar_rows = "<tr><td colspan='7' class='text-center py-4 text-muted'>No open Sales Invoices.</td></tr>";

        let ap_rows = "";
        data.purchase_invoices.forEach(row => {
            let row_cls = get_row_class(row.days_past_due);
            let link = "<a href='/app/purchase-invoice/" + row.id + "' target='_blank' style='text-decoration:none; font-weight:800; color:#0284c7;'>" + row.id + "</a>";
            ap_rows += "<tr class='" + row_cls + "'>" +
                "<td>" + link + "</td><td>" + row.entity_id + "</td><td>" + (row.entity_name || row.entity_id) + "</td><td>" + row.posting_date + "</td><td>" + row.due_date + "</td>" +
                "<td>" + format_days_status(row.days_past_due) + "</td><td class='text-end fw-bold'>" + fmt_curr(row.outstanding_amount) + "</td></tr>";
        });
        if(ap_rows === "") ap_rows = "<tr><td colspan='7' class='text-center py-4 text-muted'>No open Purchase Invoices.</td></tr>";

        let ledgers_html = "" +
            "<div class='t-grid'>" +
            "    <div class='panel'>" +
            "        <div class='panel-header'>Accounts Receivable Ledger (Sales Due)</div>" +
            "        <div style='overflow-y:auto; max-height:400px;'>" +
            "            <table class='t-table'>" +
            "                <thead><tr><th>Invoice ID</th><th>Customer ID</th><th>Customer Name</th><th>Invoice Date</th><th>Due Date</th><th>Status</th><th class='text-end'>Outstanding (KES)</th></tr></thead>" +
            "                <tbody>" + ar_rows + "</tbody>" +
            "            </table>" +
            "        </div>" +
            "    </div>" +
            "    <div class='panel'>" +
            "        <div class='panel-header'>Accounts Payable Ledger (Purchases Due)</div>" +
            "        <div style='overflow-y:auto; max-height:400px;'>" +
            "            <table class='t-table'>" +
            "                <thead><tr><th>Invoice ID</th><th>Supplier ID</th><th>Supplier Name</th><th>Invoice Date</th><th>Due Date</th><th>Status</th><th class='text-end'>Outstanding (KES)</th></tr></thead>" +
            "                <tbody>" + ap_rows + "</tbody>" +
            "            </table>" +
            "        </div>" +
            "    </div>" +
            "</div>";

        $('#dashboard-content').html(ribbon_html + matrix_html + ledgers_html);
    }

    frappe.realtime.on('nexus_treasury_sync', function(event_data) {
        if(!page.is_custom) {
            trigger_fetch(null, null);
            frappe.show_alert({message: "Ledger transaction detected. Treasury updated.", indicator: 'green'});
        }
    });

    setTimeout(() => { trigger_fetch(null, null); }, 300);
};
