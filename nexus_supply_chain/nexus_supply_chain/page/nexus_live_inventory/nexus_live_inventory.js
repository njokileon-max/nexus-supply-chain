frappe.pages['nexus_live_inventory'].on_page_load = function(wrapper) {
    var page = frappe.ui.make_app_page({
        parent: wrapper,
        title: 'Nexus Control Room: Live Reservations',
        single_column: true
    });

    let masterData = [];
    let autoRefreshInterval;

    $(page.main).html(`
        <div style="padding: 15px; background: #1e293b; border-radius: 8px; margin-bottom: 20px; display: flex; justify-content: space-between; align-items: center;">
            <input type="text" id="nexus-item-search" 
                placeholder="Search active Nexus orders by Item or SO..." 
                style="width: 70%; padding: 12px; border-radius: 6px; border: 1px solid #475569; background: #0f172a; color: white; font-size: 14px;">
            <div style="color: #10b981; font-size: 12px; display: flex; align-items: center; gap: 8px;">
                <span class="indicator-pill green">Live Sync Active</span>
            </div>
        </div>
        
        <div style="max-height: 70vh; overflow-y: auto; overflow-x: auto; border: 1px solid #475569; border-radius: 8px;">
            <table style="width: 100%; min-width: 900px; border-collapse: collapse; text-align: left; background: #1e293b; color: #f8fafc;">
                <thead style="background: #0f172a; position: sticky; top: 0; z-index: 10;">
                    <tr>
                        <th style="padding: 12px; border-bottom: 2px solid #475569;">Item ID</th>
                        <th style="padding: 12px; border-bottom: 2px solid #475569;">Item Name</th>
                        <th style="padding: 12px; border-bottom: 2px solid #475569;">Sales Order</th>
                        <th style="padding: 12px; border-bottom: 2px solid #475569;">Req. Amount</th>
                        <th style="padding: 12px; border-bottom: 2px solid #475569;">Stock Balance</th>
                        <th style="padding: 12px; border-bottom: 2px solid #475569; color: #ef4444;">Reserved Amount</th>
                        <th style="padding: 12px; border-bottom: 2px solid #475569; color: #10b981;">Global Available</th>
                    </tr>
                </thead>
                <tbody id="nexus-inventory-body">
                    <tr><td colspan="7" style="padding: 20px; text-align: center;">Fetching active Nexus reservations...</td></tr>
                </tbody>
            </table>
        </div>
    `);

    function fetchLiveData() {
        frappe.call({
            method: "nexus_supply_chain.api.get_nexus_live_inventory",
            callback: function(r) {
                if(r.message) {
                    masterData = r.message;
                    let currentSearch = $('#nexus-item-search').val().toLowerCase();
                    filterAndRender(currentSearch);
                } else {
                    $('#nexus-inventory-body').html(`<tr><td colspan="7" style="padding: 20px; text-align: center; color: #94a3b8;">No active reservations found in Nexus.</td></tr>`);
                }
            }
        });
    }

    function renderTable(data) {
        if (data.length === 0) {
            $('#nexus-inventory-body').html(`<tr><td colspan="7" style="padding: 20px; text-align: center; color: #94a3b8;">No matching records found.</td></tr>`);
            return;
        }

        let html = "";
        data.forEach(row => {
            let rowStyle = row.reserved_amount === 0 ? "opacity: 0.6;" : "";
            
            html += `
                <tr style="border-bottom: 1px solid #334155; ${rowStyle}">
                    <td style="padding: 12px; white-space: nowrap;">${row.item_id}</td>
                    <td style="padding: 12px;">${row.item_name}</td>
                    <td style="padding: 12px;">
                        <a href="/app/sales-order/${row.sales_order}" target="_blank" style="color: #38bdf8; font-weight: bold; text-decoration: none;">
                            ${row.sales_order}
                        </a>
                    </td>
                    <td style="padding: 12px;">${row.required_amount}</td>
                    <td style="padding: 12px;">${row.stock_balance}</td>
                    <td style="padding: 12px; color: #ef4444; font-weight: bold;">${row.reserved_amount}</td>
                    <td style="padding: 12px; color: #10b981; font-weight: bold;">${row.available_amount}</td>
                </tr>
            `;
        });
        $('#nexus-inventory-body').html(html);
    }

    function filterAndRender(searchString) {
        let filteredData = masterData.filter(row => {
            return row.item_id.toLowerCase().includes(searchString) || 
                   row.item_name.toLowerCase().includes(searchString) ||
                   row.sales_order.toLowerCase().includes(searchString);
        });
        renderTable(filteredData);
    }

    $('#nexus-item-search').on('input', function(e) {
        filterAndRender(e.target.value.toLowerCase());
    });

    page.set_primary_action('Refresh Now', function() {
        fetchLiveData();
    });

    fetchLiveData(); 
    autoRefreshInterval = setInterval(fetchLiveData, 15000); 

    frappe.router.on('change', () => {
        clearInterval(autoRefreshInterval);
    });
}