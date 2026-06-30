frappe.pages['nexus_production_cards'].on_page_load = function(wrapper) {
    var page = frappe.ui.make_app_page({
        parent: wrapper,
        title: 'Nexus Production Command',
        single_column: true
    });

    let masterData = [];
    let autoRefreshInterval;
    let currentlyOpenBip = null;

    // 1. Inject Styles: "Dark Cards on White Canvas"
    $(page.main).html(`
        <style>
            /* The White Canvas Wrapper */
            .nexus-prod-wrapper { padding: 20px; font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; background-color: #f8fafc; min-height: 100vh;}
            
            /* Light Search Bar & Toggles */
            .nexus-search-row { display: flex; gap: 20px; align-items: center; margin-bottom: 25px;}
            .nexus-search-input { flex-grow: 1; padding: 14px 20px; border-radius: 8px; border: 1px solid #cbd5e1; background-color: #ffffff; color: #1e293b; font-size: 15px; box-shadow: 0 2px 4px rgba(0,0,0,0.05); transition: all 0.2s ease;}
            .nexus-search-input:focus { outline: none; border-color: #38bdf8; box-shadow: 0 2px 8px rgba(56, 189, 248, 0.15); }
            .nexus-search-input::placeholder { color: #94a3b8; }
            
            .nexus-toggle-label { display: flex; align-items: center; gap: 8px; font-weight: 700; color: #475569; white-space: nowrap; cursor: pointer; user-select: none; background: #ffffff; padding: 12px 16px; border-radius: 8px; border: 1px solid #cbd5e1; box-shadow: 0 2px 4px rgba(0,0,0,0.02);}
            .nexus-toggle-checkbox { width: 18px; height: 18px; cursor: pointer; accent-color: #38bdf8;}

            /* The Dark Cards */
            .nexus-bip-card { background: #1e293b; border: 1px solid #334155; border-radius: 10px; margin-bottom: 20px; overflow: hidden; box-shadow: 0 8px 15px rgba(0,0,0,0.1); transition: 0.3s;}
            .nexus-bip-header { padding: 20px 25px; display: flex; justify-content: space-between; align-items: center; background: #1e293b; cursor: pointer; transition: background 0.2s;}
            .nexus-bip-header:hover { background: #27354f; }
            .nexus-bip-title { font-size: 18px; font-weight: 700; color: #38bdf8;}
            
            .nexus-metrics-row { display: flex; gap: 25px; text-align: right; align-items: center;}
            .nexus-metric-label { font-size: 11px; color: #94a3b8; text-transform: uppercase; font-weight: 700; letter-spacing: 0.5px;}
            .nexus-metric-val { font-size: 16px; font-weight: 800; color: #f8fafc;}
            .nexus-val-alert { color: #f43f5e; }
            .nexus-val-success { color: #10b981; }
            
            .nexus-btn-expand { background: #38bdf8; color: #0f172a; border: none; padding: 10px 20px; border-radius: 6px; font-weight: 700; cursor: pointer; font-size: 13px; transition: background 0.2s;}
            .nexus-btn-expand:hover { background: #0ea5e9; }
            .nexus-btn-close { background: #475569; color: #f8fafc; border: none; padding: 10px 20px; border-radius: 6px; font-weight: 700; cursor: pointer; font-size: 13px;}
            
            /* Expanded Area & Inner Sub-Cards (Slightly Lighter Dark) */
            .nexus-expanded-area { padding: 0 25px 25px 25px; background: #1e293b; border-top: 1px solid #334155;}
            .nexus-summary-block { background: #334155; padding: 20px; border-radius: 8px; margin: 25px 0; border: 1px solid #475569; color: #f8fafc;}
            .nexus-sub-card { background: #334155; border-left: 4px solid #475569; padding: 18px 20px; border-radius: 8px; margin-bottom: 15px; box-shadow: 0 4px 6px rgba(0,0,0,0.2); border-left-width: 4px; color: #f8fafc;}
            
            /* Table Styling */
            .nexus-table-wrapper { max-height: 300px; overflow-y: auto; overflow-x: auto; margin-bottom: 25px; border: 1px solid #475569; border-radius: 8px; box-shadow: 0 4px 6px rgba(0,0,0,0.2);}
            .nexus-table { width: 100%; min-width: 1200px; border-collapse: collapse; text-align: left; background: #334155; }
            .nexus-table th { background-color: #0f172a; color: #94a3b8; font-size: 12px; font-weight: 700; text-transform: uppercase; padding: 14px; position: sticky; top: 0; z-index: 10;}
            .nexus-table td { padding: 14px; border-bottom: 1px solid #475569; color: #f8fafc; font-size: 14px;}
            .nexus-nowrap { white-space: nowrap; }

            /* Hyperlink Styling for Sales Orders */
            .nexus-so-link { color: #38bdf8; font-weight: 700; text-decoration: none; transition: color 0.2s;}
            .nexus-so-link:hover { text-decoration: underline; color: #0ea5e9; }
        </style>

        <div class="nexus-prod-wrapper">
            <div class="nexus-search-row">
                <input type="text" id="nexus-bip-search" class="nexus-search-input" placeholder="Search Bulk Intermediate Products by Name or ID...">
                <label class="nexus-toggle-label">
                    <input type="checkbox" id="nexus-hide-stocked" class="nexus-toggle-checkbox" checked>
                    Hide Fully Stocked (Zero Net Produce)
                </label>
            </div>
            <div id="nexus-bip-list-container">
                <div style="text-align:center; padding: 50px; color: #64748b; font-weight: 600; font-size: 16px;">Syncing Live Production Requirements...</div>
            </div>
        </div>
    `);

    // 2. Data Fetching
    function fetchData() {
        frappe.call({
            method: "nexus_supply_chain.api.get_nexus_production_data",
            callback: function(r) {
                if(r.message && r.message.length > 0) {
                    masterData = r.message;
                    processAndRender();
                } else {
                    $('#nexus-bip-list-container').html(`<div style="text-align:center; padding: 50px; color: #64748b; font-weight: 600; font-size: 16px;">System Stable. No Active Production Requirements.</div>`);
                }
            }
        });
    }

    // 3. Sorting & Smart Filtering
    function processAndRender() {
        let searchString = $('#nexus-bip-search').val().toLowerCase();
        let hideStocked = $('#nexus-hide-stocked').is(':checked');
        
        let filtered = masterData.filter(bip => {
            let matchSearch = bip.bip_code.toLowerCase().includes(searchString) || 
                              bip.bip_name.toLowerCase().includes(searchString);
            let matchActionable = hideStocked ? bip.ideal_bulk > 0 : true;
            return matchSearch && matchActionable;
        });

        filtered.sort((a, b) => {
            let a_actionable = a.ideal_bulk >= a.min_batch ? 1 : 0;
            let b_actionable = b.ideal_bulk >= b.min_batch ? 1 : 0;
            if (a_actionable !== b_actionable) return b_actionable - a_actionable;
            return b.ideal_bulk - a.ideal_bulk; 
        });

        renderMinimizedCards(filtered);
    }

    $('#nexus-bip-search').on('input', processAndRender);
    $('#nexus-hide-stocked').on('change', processAndRender);

    // 4. Rendering Minimized Cards
    function renderMinimizedCards(data) {
        if(data.length === 0) {
            $('#nexus-bip-list-container').html(`<div style="text-align:center; padding: 50px; color: #64748b; font-weight: 600; font-size: 16px;">No batches meet the current filter criteria.</div>`);
            return;
        }

        let html = "";
        data.forEach(bip => {
            let minBatchClass = bip.remainder > 0 ? "nexus-val-alert" : "";
            
            html += `
                <div class="nexus-bip-card" id="card-${bip.bip_code}">
                    <div class="nexus-bip-header" onclick="window.toggleCard('${bip.bip_code}')">
                        <div class="nexus-bip-title">${bip.bip_name} <span style="color:#64748b; font-weight:600; font-size:15px;">[${bip.bip_code}]</span></div>
                        <div class="nexus-metrics-row">
                            <div><div class="nexus-metric-label">Min Batch</div><div class="nexus-metric-val ${minBatchClass}">${bip.min_batch} KG</div></div>
                            <div><div class="nexus-metric-label">Bulk Req</div><div class="nexus-metric-val nexus-val-success">${bip.ideal_bulk} KG</div></div>
                            <div><div class="nexus-metric-label">Remainder</div><div class="nexus-metric-val">${bip.remainder} KG</div></div>
                            <button class="nexus-btn-expand" id="btn-${bip.bip_code}">View Full Card</button>
                        </div>
                    </div>
                    <div id="expanded-${bip.bip_code}" class="nexus-expanded-area" style="display: none;"></div>
                </div>
            `;
        });
        $('#nexus-bip-list-container').html(html);

        if (currentlyOpenBip && $(`#card-${currentlyOpenBip}`).length) {
            window.toggleCard(currentlyOpenBip, true);
        }
    }

    // 5. Accordion Logic
    window.toggleCard = function(bipCode, isSilentRefresh = false) {
        let expandContainer = $(`#expanded-${bipCode}`);
        let btn = $(`#btn-${bipCode}`);

        if (!isSilentRefresh && expandContainer.is(":visible")) {
            expandContainer.slideUp(200, () => expandContainer.empty());
            btn.text("View Full Card").removeClass("nexus-btn-close").addClass("nexus-btn-expand");
            currentlyOpenBip = null;
            return;
        }

        if (currentlyOpenBip && currentlyOpenBip !== bipCode) {
            $(`#expanded-${currentlyOpenBip}`).slideUp(200, function() { $(this).empty(); });
            $(`#btn-${currentlyOpenBip}`).text("View Full Card").removeClass("nexus-btn-close").addClass("nexus-btn-expand");
        }

        let bipData = masterData.find(b => b.bip_code === bipCode);
        if(!bipData) return;

        let fullHtml = buildInnerCardHtml(bipData);
        
        expandContainer.html(fullHtml);
        if (!isSilentRefresh) expandContainer.slideDown(300);
        else expandContainer.show();

        btn.text("Close Details").removeClass("nexus-btn-expand").addClass("nexus-btn-close");
        currentlyOpenBip = bipCode;
    };

    // Helper: Turns comma-separated SO strings into clickable anchor tags
    function formatSOLinks(soString) {
        if (!soString || soString === "None") return "None";
        return soString.split(', ').map(so => `<a href="/app/sales-order/${so}" target="_blank" class="nexus-so-link">${so}</a>`).join(', ');
    }

    // 6. The Complex HTML Builder (3-Tier View)
    function buildInnerCardHtml(bip) {
        let fgs = bip.fgs;
        
        let packSummary = fgs.map(f => {
            let reqPack = Math.max(f.gross, f.net_produce) + f.excess;
            return `&bull; <strong style="color:#38bdf8;">${f.pack_code}</strong> (${f.name}): <strong>${reqPack}</strong> total units required<br>`;
        }).join('');

        // Tier 1 Logistics logic
        let tier1Html = '';
        let requiresProduction = fgs.filter(f => f.primary > 0);
        let satisfiedFromStock = fgs.filter(f => f.primary === 0 && f.gross > 0);

        if (requiresProduction.length > 0) {
            tier1Html += requiresProduction.map(f => `
                <div style="margin: 8px 0; font-size: 15px;">Package <span style="color:#38bdf8; font-weight:700;">${f.id}</span> <em>${f.name}</em> into <span style="color:#38bdf8; font-weight:700;">${f.primary} units</span> fulfilling SO ${formatSOLinks(f.so_list)}</div>
            `).join('');
        }
        if (satisfiedFromStock.length > 0) {
            tier1Html += satisfiedFromStock.map(f => `
                <div style="margin: 8px 0; font-size: 15px; color: #10b981; display: flex; align-items: center; gap: 8px;">
                    <svg width="18" height="18" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24" style="flex-shrink: 0;"><path stroke-linecap="round" stroke-linejoin="round" d="M5 13l4 4L19 7"></path></svg>
                    <span>Order for <span style="font-weight:700;">${f.id}</span> (${f.gross} units for ${formatSOLinks(f.so_list)}) is fully satisfied from existing Bin stock. No production needed.</span>
                </div>
            `).join('');
        }
        if (tier1Html === '') {
            tier1Html = '<div style="color: #94a3b8; font-style: italic; font-size: 14px;">No active sales orders.</div>';
        }

        let html = `
            <div class="nexus-summary-block">
                <div style="font-weight: 800; color: #94a3b8; text-transform: uppercase; font-size: 12px; margin-bottom: 12px; letter-spacing: 0.5px;">Production Card Details</div>
                <div style="line-height: 1.8; font-size: 15px;">
                    Minimum Batch = <span style="color: #f43f5e; font-weight: 800;">${bip.min_batch} KG</span><br>
                    Bulk Required (Sales + Shelf) = <span style="color: #10b981; font-weight: 800;">${bip.ideal_bulk} KG</span><br>
                    Remainder (To be forced full) = <strong style="color: #f8fafc;">${bip.remainder} KG</strong>
                </div>
                <div style="font-weight: 800; color: #94a3b8; text-transform: uppercase; font-size: 12px; margin-top: 20px; margin-bottom: 10px; letter-spacing: 0.5px;">Total Packaging Required</div>
                <div style="line-height: 1.8; font-size: 15px;">${packSummary}</div>
            </div>

            <div class="nexus-table-wrapper">
                <table class="nexus-table">
                    <thead>
                        <tr>
                            <th>FG ID</th>
                            <th>FG Name</th>
                            <th>Pack Code</th>
                            <th>Gross Order</th>
                            <th style="color: #f8fafc;">Actual (Bin)</th>
                            <th>Avail (FIFO)</th>
                            <th>MRL</th>
                            <th>Max Shelf</th>
                            <th style="color: #10b981;">Net Produce (Units)</th>
                            <th style="color: #38bdf8;">Net Produce (KG)</th>
                        </tr>
                    </thead>
                    <tbody>
                        ${fgs.map(f => `
                        <tr>
                            <td class="nexus-nowrap" style="font-weight:700;">${f.id}</td>
                            <td>${f.name}</td>
                            <td class="nexus-nowrap">${f.pack_code}</td>
                            <td>${f.gross}</td>
                            <td style="font-weight:700; color: #f8fafc;">${f.actual}</td>
                            <td>${f.available}</td>
                            <td>${f.mrl}</td>
                            <td>${f.max_shelf}</td>
                            <td style="color: #10b981; font-weight: 800; font-size: 15px;">${f.net_produce}</td>
                            <td style="color: #38bdf8; font-weight: 800; font-size: 15px;">${f.batch_net_produce_kg}</td>
                        </tr>`).join('')}
                    </tbody>
                </table>
            </div>

            <div style="padding-bottom: 10px;">
                <div class="nexus-sub-card" style="border-left-color: #3b82f6;">
                    <div style="font-weight: 800; color: #94a3b8; text-transform: uppercase; font-size: 12px; margin-bottom: 10px; letter-spacing: 0.5px;">1. Primary Fulfilment (Sales Orders)</div>
                    ${tier1Html}
                </div>

                <div class="nexus-sub-card" style="border-left-color: #10b981;">
                    <div style="font-weight: 800; color: #94a3b8; text-transform: uppercase; font-size: 12px; margin-bottom: 10px; letter-spacing: 0.5px;">2. Shelf Life Fulfillment (MRL Hit)</div>
                    ${fgs.filter(f => f.shelf > 0).map(f => `
                        <div style="margin: 8px 0; font-size: 15px;">Package <span style="color:#10b981; font-weight:700;">${f.id}</span> <em>${f.name}</em> into <span style="color:#10b981; font-weight:700;">${f.shelf} units</span> to fill shelf capacity.</div>
                    `).join('') || '<div style="color: #94a3b8; font-style: italic; font-size: 14px;">Shelves are stable above MRL limits.</div>'}
                </div>

                <div class="nexus-sub-card" style="border-left-color: #8b5cf6;">
                    <div style="font-weight: 800; color: #94a3b8; text-transform: uppercase; font-size: 12px; margin-bottom: 10px; letter-spacing: 0.5px;">3. Strategic Restock (From Excess Bulk)</div>
                    ${fgs.filter(f => f.excess > 0).map(f => `
                        <div style="margin: 8px 0; font-size: 15px;">Package <span style="color:#a78bfa; font-weight:700;">${f.id}</span> <em>${f.name}</em> into <span style="color:#a78bfa; font-weight:700;">${f.excess} units</span> (${roundKg(f.excess_kg)} KG) to replenish stock.</div>
                    `).join('') || '<div style="color: #94a3b8; font-style: italic; font-size: 14px;">No excess bulk generated requiring distribution.</div>'}
                </div>
            </div>
        `;
        return html;
    }

    function roundKg(val) { return Math.round(val * 100) / 100; }

    page.set_primary_action('Refresh Now', fetchData);
    fetchData(); 
    
    // Auto-Polling Engine (Every 5 Minutes / 300,000ms)
    autoRefreshInterval = setInterval(fetchData, 300000); 
    frappe.router.on('change', () => clearInterval(autoRefreshInterval));
}