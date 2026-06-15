frappe.pages['nexus_inventory_sync'].on_page_load = function(wrapper) {
	let page = frappe.ui.make_app_page({
		parent: wrapper,
		title: 'NEXUS INVENTORY SYNC',
		single_column: true
	});

	const style = document.createElement('style');
	style.innerHTML = `
		.section-header-container { margin-top: 40px; margin-bottom: 10px; border-bottom: 2px solid #f0f4f7; padding-bottom: 8px; }
		.section-title { font-size: 17px; font-weight: 700; color: #171717; text-transform: uppercase; letter-spacing: 0.5px; }
		.search-area-wrapper { background: #f8fafc; padding: 20px; border-radius: 8px; border: 1px solid #e2e8f0; margin-bottom: 20px; box-shadow: inset 0 1px 2px rgba(0,0,0,0.02); }
		.section-search-input { height: 38px !important; font-size: 14px !important; border: 1px solid #d1d8dd !important; box-shadow: none !important; transition: border-color 0.2s; }
		.section-search-input:focus { border-color: #5e64ff !important; background: #fff !important; }
		.btn-clear-search { margin-top: 10px; color: #64748b; font-size: 12px; cursor: pointer; border: none; background: none; padding: 0; display: inline-flex; align-items: center; transition: color 0.2s; }
		.btn-clear-search:hover { color: #ef4444; text-decoration: underline; }
		.indicator-dot { height: 10px; width: 10px; border-radius: 50%; display: inline-block; margin-right: 8px; }
		.dot-green { background-color: #28a745; }
		.dot-orange { background-color: #ffc107; }
		.dot-red { background-color: #dc3545; }
		.dot-gray { background-color: #6c757d; }
		.pagination { margin-bottom: 20px; }
	`;
	document.head.appendChild(style);

	let company_f = page.add_field({label: 'Company', fieldtype: 'Link', options: 'Company', default: frappe.defaults.get_user_default("Company")});
	let customer_f = page.add_field({label: 'Customer', fieldtype: 'Link', options: 'Customer'});
	let item_code_f = page.add_field({label: 'Item Code', fieldtype: 'Link', options: 'Item'});
	let status_f = page.add_field({label: 'Sales Order Status', fieldtype: 'Select', options: 'Draft\nTo Deliver\nTo Deliver and Bill'});
	let territory_f = page.add_field({label: 'Territory', fieldtype: 'Link', options: 'Territory'});
	let delivery_region_f = page.add_field({label: 'Delivery Region', fieldtype: 'Link', options: 'Delivery Region'});
	let tonnage_plan_f = page.add_field({label: 'Tonnage Plan', fieldtype: 'Link', options: 'Tonnage Plan'});
	let from_date_f = page.add_field({label: 'From Date', fieldtype: 'Date'});
	let to_date_f = page.add_field({label: 'To Date', fieldtype: 'Date'});
	let from_delivery_date_f = page.add_field({label: 'From Delivery Date', fieldtype: 'Date'});
	let to_delivery_date_f = page.add_field({label: 'To Delivery Date', fieldtype: 'Date'});

	let page_fields = {
		company: company_f, customer: customer_f, item_code: item_code_f,
		sales_order_status: status_f, territory: territory_f, delivery_region: delivery_region_f,
		tonnage_plan: tonnage_plan_f, from_date: from_date_f, to_date: to_date_f,
		from_delivery_date: from_delivery_date_f, to_delivery_date: to_delivery_date_f
	};

	let current_data = null;
	let pages = {so: 0, fg: 0, sub: 0, rm: 0};
	const page_size = 50;
	let sort_states = {fg: {}, sub: {}, rm: {}};
	let search_queries = {so: '', fg: '', sub: '', rm: ''};
	let debounce_timer = null;

	$(wrapper).find('.layout-main-section').append(frappe.render_template('nexus_inventory_sync', {}));

	$(wrapper).on('input', '.section-search-input', function() {
		let $el = $(this);
		let type = $el.data('type');
		search_queries[type] = $el.val().toLowerCase();
		
		clearTimeout(debounce_timer);
		debounce_timer = setTimeout(() => {
			pages[type] = 0; 
			render_section(wrapper, current_data, type, pages[type], page_size, sort_states);
			
			let $input = $(wrapper).find(`.section-search-input[data-type="${type}"]`);
			$input.focus();
			let len = $input.val().length;
			if($input[0]) $input[0].setSelectionRange(len, len);
		}, 350);
	});

	$(wrapper).on('click', '.btn-clear-search', function() {
		let type = $(this).data('type');
		search_queries[type] = '';
		pages[type] = 0;
		render_section(wrapper, current_data, type, pages[type], page_size, sort_states);
	});

	$(wrapper).on('click', '.btn-get', function() {
		let type = $(this).data('type');
		pages[type] = 0;
		frappe.call({
			method: 'nexus_supply_chain.nexus_supply_chain.page.nexus_inventory_sync.nexus_inventory_sync.get_planning_data',
			args: get_filters(page_fields),
			freeze: true,
			callback: (r) => {
				current_data = r.message;
				render_section(wrapper, current_data, type, pages[type], page_size, sort_states);
			}
		});
	});

	$(wrapper).on('click', '.btn-export', function() {
		let type = $(this).data('type');
		export_section(type, current_data, page_fields, wrapper);
	});

	$(wrapper).on('click', '.pagination button', function(e) {
		e.preventDefault();
		let type = $(this).attr('data-type');
		let action = $(this).attr('data-action');
		let total_pages = parseInt($(this).attr('data-total-pages') || 1);
		if (action === 'first') pages[type] = 0;
		else if (action === 'prev' && pages[type] > 0) pages[type]--;
		else if (action === 'next' && pages[type] < total_pages - 1) pages[type]++;
		else if (action === 'last') pages[type] = total_pages - 1;
		render_section(wrapper, current_data, type, pages[type], page_size, sort_states);
	});

	$(wrapper).on('change', '.select-all-checkbox', function() {
		let type = $(this).data('type');
		$(`#${type}-table-container .row-checkbox`).prop('checked', this.checked);
	});

	$(wrapper).on('change', '[id^=toggle-consolidate-]', function() {
		let type = this.id.split('-')[2];
		pages[type] = 0;
		render_section(wrapper, current_data, type, pages[type], page_size, sort_states);
	});

	$(wrapper).on('click', '#clear-filters', function() {
		Object.values(page_fields).forEach(field => field.set_value(''));
		search_queries = {so: '', fg: '', sub: '', rm: ''};
		current_data = null;
		pages = {so: 0, fg: 0, sub: 0, rm: 0};
		['so', 'fg', 'sub', 'rm'].forEach(type => $(`#${type}-table-container`).empty());
	});

	function get_filters(page_fields) {
		let f = {};
		Object.keys(page_fields).forEach(k => {
			let v = page_fields[k].get_value();
			if (v) f[k] = v;
		});
		return f;
	}

	function get_readiness_label(color) {
		let labels = {green: "Ready", orange: "Partial Shortage", red: "Full Shortage"};
		return labels[color] || "Unknown";
	}

	function truncate_list(arr, max=2) {
		if (!arr || arr.length <= max) return { display: arr?.join(', ') || '-', full: arr?.join(', ') || '-', truncated: false };
		return { display: arr.slice(0, max).join(', ') + ` +${arr.length - max} more`, full: arr.join(', '), truncated: true };
	}

	function get_paginated_items(items, consolidate, type, page, page_size) {
		let display = [...items];
		let query = search_queries[type];
		if (query) {
			display = display.filter(i => {
				return [i.sales_order, i.item_code, i.item_name, i.customer, i.customer_name, i.parent, i.parent_fg]
					.join(' ').toLowerCase().includes(query);
			});
		}
		if (consolidate && type !== 'so') {
			let grouped = {};
			display.forEach(i => {
				let key = i.item_code;
				if (!grouped[key]) {
					grouped[key] = {...i, parent:'Multiple', parent_fg:'Multiple', sales_order:'Multiple'};
					grouped[key][type === 'fg' ? 'qty' : 'req_qty'] = 0;
				}
				grouped[key][type === 'fg' ? 'qty' : 'req_qty'] += flt(i[type === 'fg' ? 'qty' : 'req_qty'] || 0);
			});
			display = Object.values(grouped);
		}
		let start = page * page_size;
		return { paginated: display.slice(start, start + page_size), total: display.length };
	}

	function get_section_header_html(type, title, placeholder) {
		let show_clear = search_queries[type] ? 'inline-flex' : 'none';
		return `
			<div class="section-header-container"><div class="section-title">${title}</div></div>
			<div class="search-area-wrapper">
				<input type="text" class="form-control section-search-input" data-type="${type}" placeholder="🔍 ${placeholder}" value="${search_queries[type] || ''}">
				<button class="btn-clear-search" data-type="${type}" style="display: ${show_clear}">✕ Clear search results</button>
			</div>`;
	}

	function render_pagination(type, page, total_pages, start, end, total) {
		if (total_pages <= 1) return '';
		return `<div class="text-center mt-3 pagination">
			<button class="btn btn-default btn-xs" data-type="${type}" data-action="first" data-total-pages="${total_pages}" ${page===0?'disabled':''}>First</button>
			<button class="btn btn-default btn-xs" data-type="${type}" data-action="prev" data-total-pages="${total_pages}" ${page===0?'disabled':''}>‹</button>
			<span style="margin: 0 10px;">${page+1} / ${total_pages} (${start+1}–${Math.min(end,total)} of ${total})</span>
			<button class="btn btn-default btn-xs" data-type="${type}" data-action="next" data-total-pages="${total_pages}" ${page===total_pages-1?'disabled':''}>›</button>
			<button class="btn btn-default btn-xs" data-type="${type}" data-action="last" data-total-pages="${total_pages}" ${page===total_pages-1?'disabled':''}>Last</button>
		</div>`;
	}

	function render_section(wrapper, data, type, page, page_size, sort_states) {
		if (!data) return;
		if (type === 'so') render_so(wrapper, data, page, page_size);
		else if (type === 'fg') render_fg(wrapper, data, page, page_size);
		else if (type === 'sub') render_sub(wrapper, data, page, page_size);
		else if (type === 'rm') render_rm(wrapper, data, page, page_size);
	}

	function render_so(wrapper, data, page, page_size) {
		let {paginated, total} = get_paginated_items(data.sos || [], false, 'so', page, page_size);
		let tp = Math.ceil(total / page_size), start = page * page_size;
		let html = get_section_header_html('so', 'Sales Order Readiness', 'Search Order ID, Customer or Status...') + 
		`<table class="table table-bordered table-hover" style="background:white; font-size:12px;">
		<thead><tr class="active">
			<th width="40"><input type="checkbox" class="select-all-checkbox" data-type="so"></th>
			<th>Readiness</th><th>Sales Order</th><th>Date</th><th>Customer</th><th>Customer Name</th>
			<th>Grand Total</th><th>Territory</th><th>Delivery Region</th><th>Status</th>
			<th>Shortaged FG Codes</th><th>Shortaged FG Names</th>
		</tr></thead><tbody>`;

		paginated.forEach(row => {
			let color = data.so_status?.[row.sales_order] || 'gray';
			let shortages = data.so_shortages?.[row.sales_order] || [];
			let codes_i = truncate_list(shortages.map(x => x.code), 2);
			let names_i = truncate_list(shortages.map(x => x.name), 2);
			html += `<tr>
				<td><input type="checkbox" class="row-checkbox" data-id="${row.sales_order}"></td>
				<td><span class="indicator-dot dot-${color}"></span>${get_readiness_label(color)}</td>
				<td>${row.sales_order}</td><td>${row.sales_order_date}</td><td>${row.customer}</td><td>${row.customer_name}</td>
				<td style="text-align:right">${flt(row.grand_total).toLocaleString(undefined,{minimumFractionDigits:2})}</td>
				<td>${row.territory}</td><td>${row.delivery_region}</td><td>${row.status}</td>
				<td title="${codes_i.full}">${codes_i.display}</td><td title="${names_i.full}">${names_i.display}</td>
			</tr>`;
		});
		$('#so-table-container').html(html + `</tbody></table>` + render_pagination('so', page, tp, start, start + paginated.length, total));
	}

	function render_fg(wrapper, data, page, page_size) {
		let {paginated, total} = get_paginated_items(data.fgs || [], $('#toggle-consolidate-fg').is(':checked'), 'fg', page, page_size);
		let tp = Math.ceil(total / page_size), start = page * page_size;
		let html = get_section_header_html('fg', 'Finished Goods Required', 'Search Item...') + 
		`<table class="table table-bordered table-hover" style="background:white; font-size:12px;">
		<thead><tr class="active">
			<th width="40"><input type="checkbox" class="select-all-checkbox" data-type="fg"></th>
			<th>SO</th><th>Item Code</th><th>Item Name</th><th>BOM</th><th>Req Qty</th><th>Stock</th>
			<th>Not Started</th><th>In Process</th><th>Balance</th><th>Planned Qty</th>
		</tr></thead><tbody>`;

		paginated.forEach(row => {
			let s = data.stock?.[row.item_code]?.actual || 0;
			let nsq = flt(data.not_started_qty?.[row.item_code] || 0);
			let ipq = flt(data.in_process_qty?.[row.item_code] || 0);
			let bal = s - flt(row.qty);
			let plan = Math.max(0, flt(row.qty) - s - nsq - ipq);
			html += `<tr>
				<td><input type="checkbox" class="row-checkbox" data-id="${row.item_code}_${row.parent}"></td>
				<td>${row.parent}</td><td>${row.item_code}</td><td>${row.item_name}</td><td>${row.default_bom || ''}</td>
				<td style="text-align:right">${flt(row.qty).toFixed(2)}</td><td style="text-align:right">${s.toFixed(2)}</td>
				<td style="text-align:right">${nsq.toFixed(2)}</td><td style="text-align:right">${ipq.toFixed(2)}</td>
				<td style="text-align:right; color:${bal<0?'red':'black'}">${bal.toFixed(2)}</td>
				<td style="text-align:right">${plan.toFixed(2)}</td>
			</tr>`;
		});
		$('#fg-table-container').html(html + `</tbody></table>` + render_pagination('fg', page, tp, start, start + paginated.length, total));
	}

	function render_sub(wrapper, data, page, page_size) {
		let {paginated, total} = get_paginated_items(data.subs || [], $('#toggle-consolidate-sub').is(':checked'), 'sub', page, page_size);
		let tp = Math.ceil(total / page_size), start = page * page_size;
		let html = get_section_header_html('sub', 'Sub-Assemblies Required', 'Search Sub...') + 
		`<table class="table table-bordered table-hover" style="background:white; font-size:12px;">
		<thead><tr class="active">
			<th width="40"><input type="checkbox" class="select-all-checkbox" data-type="sub"></th>
			<th>Parent FG</th><th>SO</th><th>Item Code</th><th>Item Name</th><th>Req Qty</th><th>Stock</th>
			<th>Not Started</th><th>In Process</th><th>Balance</th><th>Planned Qty</th>
		</tr></thead><tbody>`;

		paginated.forEach(row => {
			let s = data.stock?.[row.item_code]?.actual || 0;
			let nsq = flt(data.not_started_qty?.[row.item_code] || 0);
			let ipq = flt(data.in_process_qty?.[row.item_code] || 0);
			let bal = s - flt(row.req_qty);
			let plan = Math.max(0, flt(row.req_qty) - s - nsq - ipq);
			html += `<tr>
				<td><input type="checkbox" class="row-checkbox" data-id="${row.item_code}_${row.sales_order}"></td>
				<td>${row.parent_fg}</td><td>${row.sales_order}</td><td>${row.item_code}</td><td>${row.item_name}</td>
				<td style="text-align:right">${flt(row.req_qty).toFixed(2)}</td><td style="text-align:right">${s.toFixed(2)}</td>
				<td style="text-align:right">${nsq.toFixed(2)}</td><td style="text-align:right">${ipq.toFixed(2)}</td>
				<td style="text-align:right; color:${bal<0?'red':'black'}">${bal.toFixed(2)}</td>
				<td style="text-align:right">${plan.toFixed(2)}</td>
			</tr>`;
		});
		$('#sub-table-container').html(html + `</tbody></table>` + render_pagination('sub', page, tp, start, start + paginated.length, total));
	}

	function render_rm(wrapper, data, page, page_size) {
		let {paginated, total} = get_paginated_items(data.rms || [], $('#toggle-consolidate-rm').is(':checked'), 'rm', page, page_size);
		let tp = Math.ceil(total / page_size), start = page * page_size;
		let html = get_section_header_html('rm', 'Raw Materials Required', 'Search RM...') + 
		`<table class="table table-bordered table-hover" style="background:white; font-size:12px;">
		<thead><tr class="active">
			<th width="40"><input type="checkbox" class="select-all-checkbox" data-type="rm"></th>
			<th>Parent FG</th><th>SO</th><th>Item Code</th><th>Item Name</th><th>Req Qty</th><th>Stock</th>
			<th>Not Started</th><th>In Process</th><th>Balance</th><th>Planned Qty</th>
		</tr></thead><tbody>`;

		paginated.forEach(row => {
			let s = data.stock?.[row.item_code]?.actual || 0;
			let nsq = flt(data.not_started_qty?.[row.item_code] || 0);
			let ipq = flt(data.in_process_qty?.[row.item_code] || 0);
			let bal = s - flt(row.req_qty);
			let plan = Math.max(0, flt(row.req_qty) - s - nsq - ipq);
			html += `<tr>
				<td><input type="checkbox" class="row-checkbox" data-id="${row.item_code}_${row.sales_order}"></td>
				<td>${row.parent_fg}</td><td>${row.sales_order}</td><td>${row.item_code}</td><td>${row.item_name}</td>
				<td style="text-align:right">${flt(row.req_qty).toFixed(2)}</td><td style="text-align:right">${s.toFixed(2)}</td>
				<td style="text-align:right">${nsq.toFixed(2)}</td><td style="text-align:right">${ipq.toFixed(2)}</td>
				<td style="text-align:right; color:${bal<0?'red':'black'}">${bal.toFixed(2)}</td>
				<td style="text-align:right">${plan.toFixed(2)}</td>
			</tr>`;
		});
		$('#rm-table-container').html(html + `</tbody></table>` + render_pagination('rm', page, tp, start, start + paginated.length, total));
	}

	function export_section(type, data, page_fields, wrapper) {
		if (!data) return frappe.msgprint("No data to export.");
		
		let filters = get_filters(page_fields);
		let filter_lines = Object.keys(filters).map(k => [`${k}: ${filters[k]}`]);

		let selected_ids = [];
		$(`#${type}-table-container .row-checkbox:checked`).each(function() {
			selected_ids.push($(this).data('id'));
		});

		let all_items = (type === 'so') ? data.sos : (type === 'fg') ? data.fgs : (type === 'sub') ? data.subs : data.rms;
		let export_items = all_items;
		let query = search_queries[type];

		if (query && selected_ids.length === 0) {
			export_items = export_items.filter(i => {
				return [i.sales_order, i.item_code, i.item_name, i.customer, i.customer_name].join(' ').toLowerCase().includes(query);
			});
		}

		if (selected_ids.length > 0) {
			export_items = all_items.filter(r => {
				if (type === 'so') return selected_ids.includes(r.sales_order);
				return selected_ids.includes(r.item_code + "_" + (r.parent || r.sales_order || r.parent_fg));
			});
		}

		let csv = [
			["Nexus Inventory Sync Export - " + type.toUpperCase()],
			["--- Filters Applied ---"],
			...filter_lines,
			["Export Date: " + frappe.datetime.now_datetime()],
			[] 
		];

		if (type === 'so') {
			csv.push(["Readiness", "Sales Order", "Date", "Customer ID", "Customer Name", "Grand Total", "Territory", "Region", "Status"]);
			export_items.forEach(r => {
				csv.push([get_readiness_label(data.so_status[r.sales_order]), r.sales_order, r.sales_order_date, r.customer, `"${r.customer_name}"`, r.grand_total, r.territory, r.delivery_region, r.status]);
			});
		} else {
			csv.push(["Parent Ref", "SO Reference", "Item Code", "Item Name", "BOM", "Req Qty", "Stock", "Not Started", "In Process", "Balance", "Planned Qty"]);
			export_items.forEach(r => {
				let s = data.stock?.[r.item_code]?.actual || 0;
				let req = flt(r.qty || r.req_qty);
				let nsq = flt(data.not_started_qty?.[r.item_code] || 0);
				let ipq = flt(data.in_process_qty?.[r.item_code] || 0);
				csv.push([r.parent || r.parent_fg, r.sales_order || '', r.item_code, `"${r.item_name}"`, r.default_bom || '', req, s, nsq, ipq, (s - req), Math.max(0, req - s - nsq - ipq)]);
			});
		}

		let blob = new Blob([csv.map(r => r.join(',')).join('\n')], {type: 'text/csv;charset=utf-8;'});
		let a = document.createElement('a');
		a.href = URL.createObjectURL(blob);
		a.download = `nexus_export_${type}_${frappe.datetime.get_today()}.csv`;
		a.click();
	}
};
