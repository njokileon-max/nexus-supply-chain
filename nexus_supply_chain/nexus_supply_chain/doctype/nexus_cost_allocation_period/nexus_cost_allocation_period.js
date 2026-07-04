// apps/nexus_supply_chain/nexus_supply_chain/doctype/nexus_cost_allocation_period/nexus_cost_allocation_period.js

frappe.ui.form.on('Nexus Cost Allocation Period', {
    refresh: function(frm) {
        if (!frm.doc.docstatus || frm.doc.docstatus === 0) {
            frm.add_custom_button(__('Fetch Live Ledger & Yields'), function() {
                if (!frm.doc.month || !frm.doc.year) {
                    frappe.msgprint(__('Please select a Month and Year before fetching.'));
                    return;
                }
                
                frappe.call({
                    method: "nexus_supply_chain.nexus_supply_chain.doctype.nexus_cost_allocation_period.nexus_cost_allocation_period.get_monthly_ledger_and_yields",
                    args: {
                        month: frm.doc.month,
                        year: frm.doc.year
                    },
                    callback: function(r) {
                        if (r.message) {
                            let data = r.message;
                            
                            frm.set_value('total_labour_pool', data.pools.Labour || 0.0);
                            frm.set_value('total_energy_pool', data.pools.Energy || 0.0);
                            frm.set_value('total_admin_pool', data.pools.Admin || 0.0);
                            
                            frm.set_value('total_global_overheads', data.total_global_overheads || 0.0);

                            frm.set_value('total_invoiced_sales', data.total_invoiced_sales || 0.0);
                            
                            frm.clear_table('allocation_matrix');
                            let total_net_yield = 0.0;
                            
                            data.yields.forEach(row => {
                                let child = frm.add_child('allocation_matrix');
                                child.item_group = row.item_group;
                                child.volume_yield = row.yield_kg;
                                child.labour_allocation = 0.0;
                                child.energy_allocation = 0.0;
                                total_net_yield += row.yield_kg;
                            });
                            
                            frm.set_value('total_net_yield', total_net_yield);
                            frm.refresh_field('allocation_matrix');
                            
                            frappe.msgprint({
                                title: __('Ledger Synchronized'),
                                indicator: 'green',
                                message: __('Successfully imported Pure FG Yields and GL Balances for ' + frm.doc.month + ' ' + frm.doc.year)
                            });
                        }
                    }
                });
            }).addClass('btn-primary');
        }
    },
    
    validate: function(frm) {
        let total_labour = 0.0;
        let total_energy = 0.0;
        
        $.each(frm.doc.allocation_matrix || [], function(i, row) {
            total_labour += flt(row.labour_allocation);
            total_energy += flt(row.energy_allocation);
        });
        
        if (frm.doc.allocation_matrix && frm.doc.allocation_matrix.length > 0) {
            if (total_labour < 99.9 || total_labour > 100.1) {
                frappe.throw(__('Total Labour Allocation must exactly equal 100%. Current sum: ' + total_labour.toFixed(1) + '%'));
            }
            if (total_energy < 99.9 || total_energy > 100.1) {
                frappe.throw(__('Total Energy Allocation must exactly equal 100%. Current sum: ' + total_energy.toFixed(1) + '%'));
            }
        }
    }
});

frappe.ui.form.on('Nexus Costing Matrix Row', {
    labour_allocation: function(frm, cdt, cdn) {
        calculate_row_totals(frm, cdt, cdn);
    },
    energy_allocation: function(frm, cdt, cdn) {
        calculate_row_totals(frm, cdt, cdn);
    }
});

function calculate_row_totals(frm, cdt, cdn) {
    let row = frappe.get_doc(cdt, cdn);
    
    let labour_cost = flt(frm.doc.total_labour_pool) * (flt(row.labour_allocation) / 100.0);
    let energy_cost = flt(frm.doc.total_energy_pool) * (flt(row.energy_allocation) / 100.0);
    
    let admin_ratio = flt(frm.doc.total_net_yield) > 0 ? (flt(row.volume_yield) / flt(frm.doc.total_net_yield)) : 0.0;
    let admin_cost = flt(frm.doc.total_admin_pool) * admin_ratio;
    
    let total_row_overhead = labour_cost + energy_cost + admin_cost;
    let rate_per_kg = flt(row.volume_yield) > 0 ? (total_row_overhead / flt(row.volume_yield)) : 0.0;
    
    frappe.model.set_value(cdt, cdn, 'admin_allocation', admin_cost);
    frappe.model.set_value(cdt, cdn, 'rate_per_kg', rate_per_kg);
}