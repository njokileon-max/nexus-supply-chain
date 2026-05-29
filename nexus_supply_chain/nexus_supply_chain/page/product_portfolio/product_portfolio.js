frappe.pages['product_portfolio'].on_page_load = function(wrapper) {
	var page = frappe.ui.make_app_page({
		parent: wrapper,
		title: 'Product Portfolio Profitability',
		single_column: true
	});

	page.wrapper = $(wrapper);
	
	page.main.html(frappe.render_template("product_portfolio", {}));

	page.set_primary_action('Calculate & Refresh', () => {
		render_portfolio_table(page);
	});

	// Initial Render
	render_portfolio_table(page);
}

function render_portfolio_table(page) {
	let $container = page.main.find('.portfolio-table-wrapper');
	
    // 1. Inject a visual loading state so you know it's trying
	$container.html('<div class="text-muted text-center" style="padding: 50px;"><h5><i class="fa fa-spinner fa-spin"></i> Calculating deep recursive BOM costs...</h5></div>');
	
	console.log("🚀 Firing Frappe Call to: nexus_supply_chain.nexus_supply_chain.page.product_portfolio.product_portfolio.get_portfolio_data");

	frappe.call({
		method: "nexus_supply_chain.nexus_supply_chain.page.product_portfolio.product_portfolio.get_portfolio_data",
		callback: function(r) {
            console.log("📦 Backend Response Payload:", r);

			if (r.message && r.message.length > 0) {
                // We have data! Clear the loading state and build the table
				$container.empty();
				build_datatable($container, r.message);
				frappe.show_alert({message: __('Calculations Complete'), indicator: 'green'});
			} else {
                // The backend returned [] (Empty List)
                $container.html(`
                    <div class="text-muted text-center" style="padding: 50px;">
                        <h4>No Data Found</h4>
                        <p>No active items under 'Finished Goods' or 'Work In Progress' have a <b>Submitted</b> and <b>Default</b> BOM.</p>
                    </div>
                `);
			}
		},
        error: function(err) {
            console.error("❌ API Call Failed:", err);
            $container.html('<div class="text-danger text-center" style="padding: 50px;"><h4>Connection Error</h4><p>Failed to reach the Python backend. Press F12 and check the browser console.</p></div>');
        }
	});
}

function build_datatable($container, data) {
	let columns = [
		{ name: "item_code", id: "item_code", editable: false, resizable: true, sortable: true, width: 150, format: (value) => `<a href="/app/item/${value}" target="_blank"><strong>${value}</strong></a>` },
		{ name: "item_name", id: "item_name", editable: false, resizable: true, sortable: true, width: 250 },
		{ name: "item_group", id: "item_group", editable: false, resizable: true, sortable: true, width: 150 },
		{ name: "bom_no", id: "bom_no", editable: false, resizable: true, sortable: true, width: 180, format: (value) => `<a href="/app/bom/${value}" target="_blank">${value}</a>` },
		{ name: "System Valuation Rate", id: "system_val", editable: false, resizable: true, sortable: true, width: 160, format: (value) => format_currency(value, "KES") },
		{ name: "Theoretical Unit Cost", id: "theoretical_cost", editable: false, resizable: true, sortable: true, width: 160, format: (value) => format_currency(value, "KES") },
		{ name: "Variance", id: "variance", editable: false, resizable: true, sortable: true, width: 140, 
			format: (value) => {
				let color = value > 0 ? "red" : (value < 0 ? "green" : "gray");
				let formatted = format_currency(value, "KES");
				return `<span style="color: ${color}; font-weight: bold;">${formatted}</span>`;
			}
		}
	];

	new frappe.DataTable($container.get(0), {
		columns: columns,
		data: data,
		layout: "fluid",
		serialNoColumn: true,
		checkboxColumn: false,
		cellHeight: 35
	});
}