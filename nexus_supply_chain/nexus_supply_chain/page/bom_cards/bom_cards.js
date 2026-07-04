// Ensure exact object property assignment to avoid Frappe Router Blank Page error
frappe.pages['bom_cards'].on_page_load = function(wrapper) {
    var page = frappe.ui.make_app_page({
        parent: wrapper,
        title: 'BOM Costing Engine',
        single_column: true
    });

    const current_app = window.location.pathname.split('/')[1] || "nexus_supply_chain"; 

    const styles = `
    <style>
        .bom-card {
            background: #f8fafc;
            border: 1px solid #cbd5e1;
            border-radius: 10px;
            padding: 18px 20px;
            margin-bottom: 14px;
            transition: box-shadow 0.2s ease-in-out, border-color 0.2s;
            box-shadow: 0 1px 4px rgba(0,0,0,0.06);
        }
        .bom-card:hover {
            box-shadow: 0 4px 14px rgba(0,0,0,0.10);
            border-color: #94a3b8;
        }
        .bom-card-header {
            display: flex;
            justify-content: space-between;
            align-items: center;
            cursor: default;
        }
        .bom-card-header-left h4 {
            margin: 0 0 3px 0;
            font-size: 15px;
            font-weight: 700;
            color: #1e293b;
        }
        .bom-card-header-left .item-code-tag {
            display: inline-block;
            background: #e2e8f0;
            color: #475569;
            border-radius: 5px;
            padding: 1px 8px;
            font-size: 11.5px;
            font-family: monospace;
            font-weight: 600;
            letter-spacing: 0.3px;
        }
        .bom-card-header-right {
            display: flex;
            flex-direction: column;
            align-items: flex-end;
            gap: 8px;
        }
        .bom-card-header-right .cogs-label {
            font-size: 11px;
            color: #94a3b8;
            text-transform: uppercase;
            letter-spacing: 0.5px;
        }
        .bom-card-header-right .cogs-value {
            font-size: 1.2em;
            font-weight: 700;
            color: #1e293b;
        }
        .btn-expand-bom {
            margin-top: 10px;
            background: #1F4E78;
            color: #fff;
            border: none;
            border-radius: 6px;
            padding: 6px 16px;
            font-size: 12px;
            font-weight: 600;
            cursor: pointer;
            transition: background 0.15s;
            display: flex;
            align-items: center;
            gap: 5px;
        }
        .btn-expand-bom:hover { background: #16375a; }
        .btn-expand-bom.active { background: #475569; }

        .bom-card-body {
            display: none;
            margin-top: 16px;
            padding-top: 14px;
            border-top: 1px dashed #cbd5e1;
        }
        .bom-header-banner {
            background: #1F4E78;
            color: #fff;
            border-radius: 6px;
            padding: 8px 14px;
            margin-bottom: 14px;
            display: flex;
            align-items: center;
            justify-content: space-between;
        }
        .bom-header-banner .bom-ref-label {
            font-size: 11px;
            opacity: 0.75;
            text-transform: uppercase;
            letter-spacing: 0.5px;
        }
        .bom-header-banner .bom-ref-value {
            font-size: 13px;
            font-weight: 700;
            font-family: monospace;
            letter-spacing: 0.3px;
        }
        .bom-header-banner .bom-total-right {
            text-align: right;
        }
        .bom-search-container { margin-bottom: 20px; }

        .tree-indent { color: #94a3b8; margin-right: 4px; font-family: monospace; }
        .table-bom-explosion { font-size: 13px; }
        .table-bom-explosion thead th {
            background: #f1f5f9;
            color: #334155;
            font-size: 11.5px;
            font-weight: 700;
            text-transform: uppercase;
            letter-spacing: 0.4px;
            border-bottom: 2px solid #cbd5e1 !important;
            padding: 8px 10px;
        }
        .table-bom-explosion tbody td {
            vertical-align: middle;
            padding: 7px 10px;
            border-color: #e2e8f0 !important;
        }
        .table-bom-explosion tbody tr:hover { background: #f8fafc; }
        .row-subassembly td { background: #eff6ff !important; }
        .row-subassembly td:first-child { font-weight: 700; color: #1d4ed8; }

        .lbl-price-list  { background:#dcfce7; color:#166534; padding:2px 9px; border-radius:12px; font-size:10.5px; font-weight:700; white-space:nowrap; }
        .lbl-system-val  { background:#fef9c3; color:#854d0e; padding:2px 9px; border-radius:12px; font-size:10.5px; font-weight:700; white-space:nowrap; }
        .lbl-zero-cost   { background:#fee2e2; color:#991b1b; padding:2px 9px; border-radius:12px; font-size:10.5px; font-weight:700; white-space:nowrap; }
        .lbl-subassembly { background:#dbeafe; color:#1e40af; padding:2px 9px; border-radius:12px; font-size:10.5px; font-weight:700; white-space:nowrap; }

        .tfoot-total td {
            background: #f1f5f9 !important;
            font-weight: 700;
            font-size: 13px;
            border-top: 2px solid #94a3b8 !important;
        }
    </style>
`;
    $(wrapper).append(styles);

    // --- Build UI Skeleton ---
    const $layout = $(`
        <div class="bom-search-container">
            <input type="text" id="bom-search" class="form-control" placeholder="🔍 Search globally by FG Name or Item Code...">
        </div>
        <div id="bom-cards-container">
            <div class="text-muted text-center py-5">⏳ Initializing Cost Engine & Fetching Portfolio...</div>
        </div>
    `).appendTo(page.main);

    const $container = $('#bom-cards-container');
    const $searchInput = $('#bom-search');
    let all_cards = [];

    frappe.call({
        method: `nexus_supply_chain.nexus_supply_chain.page.bom_cards.bom_cards.get_fg_cards`,
        callback: function(r) {
            $container.empty();
            if(r.message && r.message.length > 0) {
                all_cards = r.message;
                render_cards(all_cards);
                
                parse_hash_parameters();
            } else {
                $container.html(`<div class="alert alert-warning">No Finished Goods with active BOMs found.</div>`);
            }
        }
    });

    function parse_hash_parameters() {
        let hash = window.location.hash;
        if (hash && hash.includes('item_code=')) {
            let params = new URLSearchParams(hash.substring(hash.indexOf('?')));
            let target_item = params.get('item_code');
            
            if (target_item) {
                $searchInput.val(target_item);
                $searchInput.trigger('keyup');

                setTimeout(() => {
                    let exact_card = $(`.bom-card[data-item-code="${target_item}"]`);
                    if (exact_card.length > 0) {
                        exact_card.find('.btn-expand-bom').trigger('click');
                        $('html, body').animate({
                            scrollTop: exact_card.offset().top - 120
                        }, 500);
                    }
                }, 200);
            }
        }
    }

    function render_cards(items) {
    $container.empty();
    const currency = frappe.boot.sysdefaults.currency;

    items.forEach(item => {
        const formatted_cost = format_currency(item.total_cogs, currency);
        const safe_id = item.item_code.replace(/[^a-zA-Z0-9]/g, '-');

        const card_html = `
            <div class="bom-card" data-item-code="${item.item_code}" data-item-name="${item.item_name.toLowerCase()}">
                <div class="bom-card-header">
                    <div class="bom-card-header-left">
                        <h4>${item.item_name}</h4>
                        <span class="item-code-tag">${item.item_code}</span>
                    </div>
                    <div class="bom-card-header-right">
                        <div>
                            <span class="cogs-label">Theoretical COGS</span><br>
                            <span class="cogs-value">${formatted_cost}</span>
                        </div>
                        <button class="btn-expand-bom" data-target="body-${safe_id}">
                            <span class="btn-icon">▶</span> View BOM Formulation
                        </button>
                    </div>
                </div>
                <div class="bom-card-body" id="body-${safe_id}">
                </div>
            </div>
        `;
        $container.append(card_html);
    });

    $(document).on('click', '.btn-expand-bom', function(e) {
        e.stopPropagation();
        const target_id = $(this).data('target');
        const $body = $('#' + target_id);
        const item_code = $(this).closest('.bom-card').attr('data-item-code');
        const $btn = $(this);

        $body.slideToggle(220, function() {
            if ($body.is(':visible')) {
                $btn.html('<span class="btn-icon">▼</span> Hide BOM Formulation');
                $btn.addClass('active');
            } else {
                $btn.html('<span class="btn-icon">▶</span> View BOM Formulation');
                $btn.removeClass('active');
            }
        });

        if ($body.children().length === 0) {
            $body.html(`
                <div style="padding:12px 0; color:#64748b; font-size:13px;">
                    <span style="animation: spin 1s linear infinite; display:inline-block;">⚙️</span>
                    &nbsp; Exploding BOM — computing formulation...
                </div>
            `);
            fetch_bom_explosion(item_code, $body);
        }
    });
}

    function fetch_bom_explosion(item_code, $body_container) {
    frappe.call({
        method: `nexus_supply_chain.nexus_supply_chain.page.bom_cards.bom_cards.get_bom_explosion`,
        args: { item_code: item_code },
        callback: function(r) {
            if (r.message) {
                const data = r.message;
                const currency = frappe.boot.sysdefaults.currency;

                // BOM header banner
                const header_html = `
                    <div class="bom-header-banner">
                        <div>
                            <div class="bom-ref-label">Default BOM Reference</div>
                            <div class="bom-ref-value">${data.bom_no || '—'}</div>
                        </div>
                        <div class="bom-total-right">
                            <div class="bom-ref-label">Theoretical Rollup Total</div>
                            <div class="bom-ref-value" style="font-size:15px;">
                                ${format_currency(data.total_cogs, currency)}
                            </div>
                        </div>
                    </div>
                `;

                const tree_html = build_tree_table(data.tree, currency);
                $body_container.html(header_html + tree_html);
            } else {
                $body_container.html(`<div class="alert alert-warning">Could not load BOM data.</div>`);
            }
        },
        error: function() {
            $body_container.html(`<div class="alert alert-danger">Error fetching BOM formulation.</div>`);
        }
    });
}

    function build_tree_table(tree, currency) {
    let rows = "";
    let grand_total = 0;

    function traverse(nodes, depth) {
        nodes.forEach(n => {
            const indent_html = depth > 0
                ? `<span class="tree-indent">${'&nbsp;&nbsp;'.repeat(depth * 3)}↳</span>`
                : '';

            let badge = '';
            let row_class = '';
            let code_style = '';

            if (n.is_subassembly) {
                badge       = '<span class="lbl-subassembly">⚙ Sub-Assembly</span>';
                row_class   = 'row-subassembly';
                code_style  = 'color:#1d4ed8; font-weight:700;';
            } else if (n.source === 'Price List') {
                badge = '<span class="lbl-price-list">✔ Price List</span>';
            } else if (n.source === 'System Valuation') {
                badge = '<span class="lbl-system-val">⚠ System Val (Fallback)</span>';
            } else {
                badge = '<span class="lbl-zero-cost">✖ Update Required</span>';
            }

            if (depth === 0) grand_total += (n.extended_cost || 0);

            const separator = (n.is_subassembly && depth === 0)
                ? `<tr><td colspan="7" style="background:#dbeafe;padding:3px 10px;font-size:11px;color:#1e40af;font-weight:600;border-top:2px solid #93c5fd;">
                       ⚙ Sub-Assembly Breakdown — ${n.item_code} : ${n.item_name}
                   </td></tr>`
                : '';

            rows += `
                ${separator}
                <tr class="${row_class}">
                    <td style="${code_style}">${indent_html}<strong>${n.item_code}</strong></td>
                    <td>${n.item_name}</td>
                    <td class="text-center" style="color:#475569;font-size:12px;">${n.uom || ''}</td>
                    <td class="text-right">${parseFloat(n.qty || 0).toFixed(4)}</td>
                    <td class="text-right">${format_currency(n.unit_cost || 0, currency)}</td>
                    <td class="text-right"><strong>${format_currency(n.extended_cost || 0, currency)}</strong></td>
                    <td class="text-center">${badge}</td>
                </tr>
            `;

            if (n.children && n.children.length > 0) {
                traverse(n.children, depth + 1);

                rows += `
                    <tr style="background:#eff6ff;">
                        <td colspan="5" class="text-right" style="font-size:11.5px;color:#1e40af;padding:5px 10px;font-style:italic;">
                            ${'&nbsp;&nbsp;'.repeat(depth * 3)} Sub-Assembly Rollup — ${n.item_code}
                        </td>
                        <td class="text-right" style="font-weight:700;color:#1e40af;">
                            ${format_currency(n.unit_cost || 0, currency)}
                        </td>
                        <td></td>
                    </tr>
                `;
            }
        });
    }

    traverse(tree, 0);

    return `
        <div class="table-responsive">
            <table class="table table-bordered table-bom-explosion">
                <thead>
                    <tr>
                        <th style="width:16%">Component Code</th>
                        <th>Item Name</th>
                        <th class="text-center" style="width:7%">UOM</th>
                        <th class="text-right" style="width:9%">Req. Qty</th>
                        <th class="text-right" style="width:12%">Unit Cost</th>
                        <th class="text-right" style="width:12%">Extended Cost</th>
                        <th class="text-center" style="width:16%">Pricing Source</th>
                    </tr>
                </thead>
                <tbody>
                    ${rows}
                </tbody>
                <tfoot>
                    <tr class="tfoot-total">
                        <td colspan="5" class="text-right">Theoretical Rollup Total</td>
                        <td class="text-right">${format_currency(grand_total, currency)}</td>
                        <td></td>
                    </tr>
                </tfoot>
            </table>
        </div>
    `;
}

    $searchInput.on('keyup', function() {
    const raw = $(this).val().toLowerCase().trim();

    function normalize(str) {
        return str
            .toLowerCase()
            .replace(/[-_]+/g, ' ')   // hyphens & underscores → space
            .replace(/\s+/g, ' ')     // collapse multiple spaces
            .trim();
    }

    const normalizedTerm = normalize(raw);
    const words = normalizedTerm.split(' ').filter(w => w.length > 0);

    $('.bom-card').each(function() {
        const normCode = normalize($(this).attr('data-item-code'));
        const normName = normalize($(this).attr('data-item-name'));
        const haystack = normCode + ' ' + normName;

        const match = words.length === 0 || words.every(w => haystack.includes(w));

        $(this).toggle(match);
    });
});
function format_currency(value, currency) {
        return frappe.format(value, { fieldtype: 'Currency', currency: currency });
    }
};