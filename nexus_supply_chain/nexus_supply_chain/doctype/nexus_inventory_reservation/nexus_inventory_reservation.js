// apps/nexus_supply_chain/nexus_supply_chain/doctype/nexus_inventory_reservation/nexus_inventory_reservation.js

frappe.ui.form.on('Nexus Inventory Reservation', {
    
    // Intercept the standard cancel action
    before_cancel: function(frm) {
        // If the reason is empty, halt the cancellation and show a prompt
        if (!frm.doc.release_reason) {
            
            // Halt the initial click so the prompt can take over
            frappe.validated = false; 
            
            frappe.prompt(
                [
                    {
                        fieldname: 'reason',
                        fieldtype: 'Select', // Changed from Small Text to Select
                        label: __('Release Reason'),
                        reqd: 1,
                        // Exactly matching the options from your DocType
                        options: '\nLoad Plan Cancelled\nStock Reallocation\nCustomer Request\nReservation Expired\nInventory Correction',
                        description: __('Please select the reason for releasing these reserved items.')
                    }
                ],
                function(values) {
                    // 1. Inject the selected reason into the field
                    frm.set_value('release_reason', values.reason);
                    
                    // 2. Programmatically trigger the cancel action again
                    frm.save('Cancel');
                },
                __('Reason for Cancellation'),
                __('Proceed to Cancel')
            );
        }
    }
});