// Copyright (c) 2026, Nexus Supply Chain and contributors
// For license information, please see license.txt

frappe.ui.form.on('Nexus Monthly Overhead Ledger', {
    refresh: function(frm) {
        // Aesthetic improvements
        frm.set_df_property('total_monthly_overhead', 'reqd', 1);
    },

    month: function(frm) { frm.trigger('clear_data_on_change'); },
    year: function(frm) { frm.trigger('clear_data_on_change'); },
    company: function(frm) { frm.trigger('clear_data_on_change'); },

    clear_data_on_change: function(frm) {
        // If parameters change, clear the table to force a re-fetch
        if (frm.doc.table_zgwz && frm.doc.table_zgwz.length > 0) {
            frm.clear_table('table_zgwz');
            frm.set_value('total_production_kg', 0);
            frm.trigger('calculate_totals');
            frm.refresh_fields();
        }
    },

    working_days: function(frm) {
        frm.trigger('calculate_totals');
    },

    total_production_kg: function(frm) {
        frm.trigger('calculate_totals');
    },

    fetch_erp_data: function(frm) {
        if (!frm.doc.month || !frm.doc.year || !frm.doc.company) {
            frappe.msgprint({
                title: __('Missing Fields'),
                indicator: 'orange',
                message: __('Please select Month, Year, and Company before fetching data.')
            });
            return;
        }

        frappe.call({
            method: 'nexus_supply_chain.nexus_supply_chain.doctype.nexus_monthly_overhead_ledger.nexus_monthly_overhead_ledger.fetch_erp_monthly_data',
            args: {
                month: frm.doc.month,
                year: frm.doc.year,
                company: frm.doc.company
            },
            freeze: true,
            freeze_message: __('Querying General Ledger & Factory Yield...'),
            callback: function(r) {
                if (r.message) {
                    // 1. Clear existing table to prevent duplicate stacking
                    frm.clear_table('table_zgwz');

                    // 2. Populate the Child Table with active GL expenses
                    let expenses = r.message.expenses;
                    if (expenses && expenses.length > 0) {
                        expenses.forEach(row => {
                            let child = frm.add_child('table_zgwz');
                            child.account_number = row.account_number;
                            child.account_name = row.account_name;
                            child.amount_kes = row.amount_kes;
                        });
                    } else {
                        frappe.show_alert({message: __('No expense transactions found for this period.'), indicator: 'orange'});
                    }

                    // 3. Set the Factory Yield
                    frm.set_value('total_production_kg', r.message.total_kg);

                    // 4. Update the DOM and calculate math
                    frm.refresh_field('table_zgwz');
                    frm.trigger('calculate_totals');

                    frappe.show_alert({message: __('ERP Data Successfully Audited & Fetched.'), indicator: 'green'});
                }
            }
        });
    },

    calculate_totals: function(frm) {
        // 0-Lag Instant Math Execution
        let total_fetched = 0;
        let total_manual = 0;

        // Sum Fetched GL Data
        if (frm.doc.table_zgwz) {
            frm.doc.table_zgwz.forEach(row => {
                total_fetched += flt(row.amount_kes);
            });
        }

        // Sum Ad-Hoc Manual Data (Assuming the field is 'amount')
        if (frm.doc.manual_entries) {
            frm.doc.manual_entries.forEach(row => {
                total_manual += flt(row.amount); 
            });
        }

        let grand_total = total_fetched + total_manual;
        frm.set_value('total_monthly_overhead', grand_total);

        // Apportionments
        let days = flt(frm.doc.working_days) || 1; // Prevent divide by zero
        frm.set_value('overhead_rate_per_day', grand_total / days);

        let yield_kg = flt(frm.doc.total_production_kg);
        if (yield_kg > 0) {
            frm.set_value('overhead_rate_per_kg', grand_total / yield_kg);
        } else {
            frm.set_value('overhead_rate_per_kg', 0);
        }
    }
});

// Trigger recalculations instantly if manual entries are added or removed
frappe.ui.form.on('Nexus Overhead Manual Entry', {
    amount: function(frm) { frm.trigger('calculate_totals'); },
    manual_entries_remove: function(frm) { frm.trigger('calculate_totals'); }
});

// Trigger recalculations if someone manually deletes a fetched row
frappe.ui.form.on('Nexus Overhead Account Detail', {
    table_zgwz_remove: function(frm) { frm.trigger('calculate_totals'); }
});