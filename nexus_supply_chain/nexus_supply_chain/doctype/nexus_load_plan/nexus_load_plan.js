// Copyright (c) 2026, Administrator and contributors
// For license information, please see license.txt

frappe.ui.form.on('Nexus Load Plan', {
    refresh: function(frm) {
        
        // ==========================================
        // BLOCK 1: VISUAL INDICATORS 
        // ==========================================
        try {
            const current_res_status = frm.doc.reservation_status || 'Soft';
            if (current_res_status) {
                const status_colors = {
                    'Reserved': 'green', 'Partially Reserved': 'orange',
                    'Consumed': 'blue', 'Partially Consumed': 'light-blue',
                    'Released': 'darkgrey', 'Expired': 'red', 'Soft': 'purple'
                };
                frm.page.set_indicator(current_res_status, status_colors[current_res_status] || 'gray');
            }
        } catch (error) { console.warn("Nexus UI Warning: Failed to set indicator."); }

        // ==========================================
        // BLOCK 2: PERSISTENT BUTTONS (Nexus Actions)
        // ==========================================
        if (!frm.is_new()) {
            
            // ---------------------------------------------------
            // ACTION 1: REANALYZE & RESERVE
            // ---------------------------------------------------
            try {
                frm.add_custom_button(__('Reanalyze & Reserve'), function() {
                    frappe.confirm(
                        __('Analyze stock in <b>CAL Warehouse</b> and generate a Draft Reservation?'),
                        () => {
                            frappe.call({
                                method: 'nexus_supply_chain.nexus_supply_chain.page.nexus_load_optimizer.nexus_load_optimizer.reanalyze_load_plan',
                                args: { load_plan_name: frm.doc.name },
                                freeze: true,
                                freeze_message: __('Nexus Brain is compiling Draft Data...'),
                                callback: function(r) {
                                    if (r.message) {
                                        let is_existing = r.message.status === "exists";
                                        let indicator_color = is_existing ? 'orange' : 'green';
                                        let title_text = is_existing ? __('Reservation Already Exists') : __('Draft Generation Complete');
                                        
                                        let modal_body = `
                                            <div class="text-center" style="padding: 10px;">
                                                <p class="text-muted">${r.message.message}</p>
                                                <div style="margin: 20px 0;">
                                                    <a class="btn btn-primary" 
                                                       href="/app/nexus-inventory-reservation/${r.message.reservation_id}" 
                                                       target="_blank">
                                                       Open Reservation: <b>${r.message.reservation_id}</b>
                                                    </a>
                                                </div>
                                            </div>`;
                                        
                                        // If it's a NEW draft, fetch and display the shortfall intelligence
                                        if (!is_existing) {
                                            frappe.model.with_doc('Nexus Load Plan', frm.doc.name, function() {
                                                const updated_doc = frappe.get_doc('Nexus Load Plan', frm.doc.name);
                                                if (updated_doc.custom_inventory_shortfall_notes) {
                                                    modal_body += `
                                                        <hr>
                                                        <div style="text-align: left;">
                                                            <b>Shortfall Intel:</b>
                                                            <pre style="background:#f8f9fa; border:1px solid #ddd; padding:10px; margin-top:5px; font-size: 11px;">${updated_doc.custom_inventory_shortfall_notes}</pre>
                                                        </div>`;
                                                }
                                                show_dialog(title_text, indicator_color, modal_body);
                                            });
                                        } else {
                                            // If it exists, just show the warning and the link
                                            show_dialog(title_text, indicator_color, modal_body);
                                        }

                                        // Helper function to render the popup
                                        function show_dialog(title, color, body) {
                                            frappe.msgprint({
                                                title: title,
                                                indicator: color, 
                                                message: body,
                                                primary_action: { 
                                                    label: __('Close & Reload'), 
                                                    action: function() {
                                                        frappe.hide_msgprint();
                                                        setTimeout(() => frm.reload_doc(), 300);
                                                    } 
                                                }
                                            });
                                        }
                                    }
                                }
                            });
                        }
                    );
                }, __('Nexus Actions')); 
            } catch (error) { console.error("Error loading Reanalyze button."); }

            // ---------------------------------------------------
            // ACTION 2: CREATE DELIVERY MANIFEST
            // ---------------------------------------------------
            try {
                frm.add_custom_button(__('Create Delivery Manifest'), function() {
                    
                    if(frm.doc.docstatus !== 1) {
                        frappe.msgprint({title: 'Action Denied', indicator: 'red', message: 'You must Submit the Load Plan before generating a Manifest.'});
                        return;
                    }

                    frappe.confirm(
                        __('Generate a Vehicle Delivery Manifest for this Load Plan?'),
                        () => {
                            frappe.call({
                                method: 'nexus_supply_chain.nexus_supply_chain.doctype.nexus_load_plan.nexus_load_plan.make_delivery_manifest',
                                args: { source_name: frm.doc.name },
                                freeze: true,
                                freeze_message: __('Generating Manifest...'),
                                callback: function(r) {
                                    if (r.message) {
                                        // Handle both 'success' (newly created) and 'exists' (already created) dynamically
                                        let is_existing = r.message.status === 'exists';
                                        let indicator_color = is_existing ? 'orange' : 'green';
                                        let title_text = is_existing ? __('Manifest Already Exists') : __('Delivery Manifest Created');
                                        
                                        let modal_body = `
                                            <div class="text-center" style="padding: 10px;">
                                                <p class="text-muted">${r.message.message}</p>
                                                <div style="margin: 20px 0;">
                                                    <a class="btn btn-primary" 
                                                       href="/app/vehicle-delivery-manifest/${r.message.manifest_id}" 
                                                       target="_blank">
                                                       Open Manifest: <b>${r.message.manifest_id}</b>
                                                    </a>
                                                </div>
                                            </div>`;
                                            
                                        frappe.msgprint({
                                            title: title_text,
                                            indicator: indicator_color, 
                                            message: modal_body,
                                            primary_action: { 
                                                label: __('Done'), 
                                                action: function() {
                                                    frappe.hide_msgprint();
                                                } 
                                            }
                                        });
                                    }
                                }
                            });
                        }
                    );
                }, __('Nexus Actions')); 
            } catch (error) { console.error("Error loading Manifest button."); }
        }
    }
});