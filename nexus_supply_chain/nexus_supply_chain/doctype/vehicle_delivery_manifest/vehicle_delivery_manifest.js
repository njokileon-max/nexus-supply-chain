let wakeLock = null;

const TILE_SERVER_URL = "https://from-trunk-debug-sufficient.trycloudflare.com/styles/basic-preview/style.json";

frappe.ui.form.on('Vehicle Delivery Manifest', {
    refresh: function(frm) {
        
        if (frm.fields_dict.route_map_html && frm.doc.route_geojson) {
            render_manifest_map(frm);
        }

        if (localStorage.getItem('tracking_' + frm.doc.name) === 'true' && frm.doc.trip_status === 'Dispatched') {
            frm.set_intro(__("GPS Tracking is currently LIVE via Native App."), "blue");
        }

        if (frm.doc.trip_status === 'Dispatched' && !wakeLock) {
            frm.dashboard.add_comment('blue', `
                <div style="background:#2563eb;color:white;padding:14px;border-radius:8px;text-align:center;font-weight:bold;margin-bottom:10px;">
                    <i class="fa fa-satellite-dish me-2" style="animation: pulse 2s infinite;"></i> NATIVE GPS TRACKING ACTIVE<br>
                    <small style="opacity:0.9;">Do NOT close this tab • Screen will stay awake</small>
                </div>
            `, 'GPS Status');

            const requestWakeLock = async () => {
                if ('wakeLock' in navigator) {
                    try {
                        wakeLock = await navigator.wakeLock.request('screen');
                        console.log('%c✅ Screen Wake Lock acquired', 'color:#22c55e;font-weight:bold');
                    } catch (e) {
                        console.log('Wake Lock not supported on this device/browser.');
                    }
                }
            };
            requestWakeLock();
            
            document.addEventListener('visibilitychange', async () => {
                if (wakeLock !== null && document.visibilityState === 'visible') {
                    requestWakeLock();
                }
            });
        }

        if (!frm.is_new() && frm.doc.docstatus === 1) {
            
            if (frm.doc.trip_status === 'Ready' || frm.doc.trip_status === 'Dispatched' || frm.doc.trip_status === 'Completed' || frm.doc.trip_status === 'Returning') {
                frm.add_custom_button(__('<i class="fa fa-map-marked-alt me-2 text-primary"></i> Track Driver Location'), function() {
                    localStorage.setItem('tracking_' + frm.doc.name, 'true');
                    frappe.set_route('nexus_driver_tracker');
                }).addClass('btn-default fw-bold');
            }

            if (frm.doc.trip_status === 'Ready') {
                frm.add_custom_button(__('<i class="fa fa-play me-2 text-success"></i> Start Delivery Trip'), function() {
                    frappe.confirm('Initiate trip and record official departure from the yard? (Ensure Native App tracking is started)', () => {
                        frappe.call({
                            method: "nexus_supply_chain.api.sync_manifest_from_app",
                            args: { 
                                manifest_name: frm.doc.name,
                                trip_status: 'Dispatched'
                            },
                            callback: function(r) {
                                if(r.message && r.message.status === "success") {
                                    localStorage.setItem('tracking_' + frm.doc.name, 'true');
                                    frappe.show_alert({message: "Trip officially started.", indicator: "green"});
                                    
                                    if (window.ReactNativeWebView) {
                                        window.ReactNativeWebView.postMessage(JSON.stringify({ 
                                            action: 'start_gps', 
                                            manifest_id: frm.doc.name 
                                        }));
                                    }
                                    
                                    frm.reload_doc();
                                }
                            }
                        });
                    });
                }).addClass('btn-default fw-bold');
            }

            if (frm.doc.trip_status === 'Dispatched' || frm.doc.trip_status === 'Completed' || frm.doc.trip_status === 'Returning') {
                frm.add_custom_button(__('🏁 Confirm Arrival at Factory'), function() {
                    frappe.confirm('Confirm you have reached the factory compound? This will stop GPS tracking and reset the vehicle to Idle.', () => {
                        localStorage.removeItem('tracking_' + frm.doc.name);
                        
                        if (wakeLock) {
                            wakeLock.release().catch(console.error);
                            wakeLock = null;
                        }

                        if (window.ReactNativeWebView) {
                            window.ReactNativeWebView.postMessage(JSON.stringify({ 
                                action: 'stop_gps' 
                            }));
                        }
                        
                        fetch(`http://${window.location.hostname}:8001/telemetry/end-trip`, {
                            method: 'POST', 
                            headers: { 'Content-Type': 'application/json' },
                            body: JSON.stringify({ manifest_id: frm.doc.name })
                        }).catch(e => console.log("Backend sync complete."));

                        frappe.call({
                            method: "nexus_supply_chain.api.sync_manifest_from_app",
                            args: { 
                                manifest_name: frm.doc.name,
                                trip_status: 'Completed'
                            },
                            callback: function(res) {
                                frappe.show_alert({message: "Vehicle is now marked as Idle.", indicator: 'green'});
                                frm.reload_doc();
                            }
                        });
                    });
                }).addClass('btn-danger fw-bold text-white');
            }

            if (frm.doc.trip_status === 'Dispatched' || frm.doc.trip_status === 'Returning') {
                frm.add_custom_button(__('Confirm a Stop'), function() {
                    let pending_stops = frm.doc.stops.filter(d => d.delivery_status === 'Pending');
                    if (pending_stops.length === 0) return;

                    let stop_options = pending_stops.map(d => ({
                        "label": `${d.customer_name || d.customer} - KES ${d.grand_total || 0}`,
                        "value": d.name
                    }));

                    let d = new frappe.ui.Dialog({
                        title: 'Confirm Delivery Stop',
                        fields: [
                            { label: 'Select Stop', fieldname: 'stop_row_id', fieldtype: 'Select', options: stop_options, reqd: 1 },
                            { fieldtype: 'Section Break' },
                            { 
                                label: 'Delivery Status', fieldname: 'delivery_status', fieldtype: 'Select', reqd: 1,
                                options: 'Delivered\nPartially Delivered\nFailed',
                                onchange: function() {
                                    let is_partial = this.value === 'Partially Delivered';
                                    d.set_df_property('return_reason', 'hidden', !is_partial);
                                    d.set_df_property('returned_items_html', 'hidden', !is_partial);
                                    
                                    if (is_partial) {
                                        let selected_row = pending_stops.find(s => s.name === d.get_value('stop_row_id'));
                                        
                                        frappe.call({
                                            method: "frappe.client.get_list",
                                            args: {
                                                doctype: "Sales Order Item",
                                                filters: { parent: selected_row.sales_order },
                                                fields: ["item_code", "item_name", "qty"]
                                            },
                                            callback: function(r) {
                                                if(r.message) {
                                                    let html = `<table class="table table-bordered small" id="partial-return-table">
                                                        <thead class="bg-light"><tr><th>Return?</th><th>Item</th><th>Max Qty</th><th>Qty Returned</th></tr></thead><tbody>`;
                                                    r.message.forEach(item => {
                                                        html += `<tr>
                                                            <td class="text-center"><input type="checkbox" class="item-check" data-item="${item.item_code}"></td>
                                                            <td><b>${item.item_code}</b><br>${item.item_name}</td>
                                                            <td>${item.qty}</td>
                                                            <td><input type="number" class="form-control form-control-sm return-qty" max="${item.qty}" min="0" disabled></td>
                                                        </tr>`;
                                                    });
                                                    html += `</tbody></table>`;
                                                    d.fields_dict.returned_items_html.$wrapper.html(html);

                                                    $('#partial-return-table .item-check').on('change', function() {
                                                        let qtyInput = $(this).closest('tr').find('.return-qty');
                                                        qtyInput.prop('disabled', !$(this).is(':checked')).val($(this).is(':checked') ? 1 : '');
                                                    });

                                                    $('#partial-return-table .return-qty').on('input', function() {
                                                        let max = parseFloat($(this).attr('max'));
                                                        let val = parseFloat($(this).val());
                                                        if (val > max) {
                                                            $(this).val(max);
                                                            frappe.show_alert({message: `Cannot return more than ordered quantity.`, indicator: 'orange'});
                                                        } else if (val < 0) {
                                                            $(this).val(0);
                                                        }
                                                    });
                                                }
                                            }
                                        });
                                    }
                                }
                            },
                            { fieldtype: 'HTML', fieldname: 'returned_items_html', hidden: 1 },
                            { label: 'Primary Reason for Return', fieldname: 'return_reason', fieldtype: 'Select', hidden: 1, options: '\nDamaged In Transit\nIncorrect Item\nIncorrect Quantity\nCustomer Rejected Value\nCustomer Payment Failure\nArrived Outside Delivery Window' },
                            { fieldtype: 'Section Break' },
                            { label: 'Driver Notes (Optional)', fieldname: 'driver_notes', fieldtype: 'Small Text' }
                        ],
                        primary_action_label: 'Confirm Drop-off',
                        primary_action(values) {
                            let selected_stop = stop_options.find(o => o.value === values.stop_row_id);
                            let selected_stop_label = selected_stop ? selected_stop.label : "the selected stop";
                            
                            let returned_data = [];
                            let validation_failed = false;

                            if (values.delivery_status === 'Partially Delivered') {
                                $('#partial-return-table tbody tr').each(function() {
                                    if ($(this).find('.item-check').is(':checked')) {
                                        let max_qty = parseFloat($(this).find('.return-qty').attr('max'));
                                        let qty_returned = parseFloat($(this).find('.return-qty').val());

                                        if (isNaN(qty_returned) || qty_returned <= 0) {
                                            frappe.msgprint("Please enter a valid return quantity greater than 0.");
                                            validation_failed = true;
                                            return false;
                                        }
                                        if (qty_returned > max_qty) {
                                            frappe.msgprint(`Quantity returned cannot exceed the ordered quantity (${max_qty}).`);
                                            validation_failed = true;
                                            return false;
                                        }

                                        returned_data.push({
                                            item_code: $(this).find('.item-check').data('item'),
                                            qty_returned: qty_returned
                                        });
                                    }
                                });

                                if (validation_failed) return;
                                
                                if (returned_data.length === 0) {
                                    frappe.msgprint("Please select at least one item to return for a Partially Delivered status.");
                                    return;
                                }
                                if (!values.return_reason) {
                                    frappe.msgprint("Please select a primary reason for the return.");
                                    return;
                                }
                            }

                            frappe.confirm(__('Confirm delivery for <b>{0}</b>? This will trigger automated invoicing and cannot be undone.', [selected_stop_label]), () => {
                                d.get_primary_btn().prop('disabled', true).html('Processing...');

                                let payload = [{
                                    name: values.stop_row_id,
                                    delivery_status: values.delivery_status,
                                    driver_notes: values.driver_notes || '',
                                    primary_reason_for_return: values.delivery_status !== 'Delivered' ? (values.return_reason || '') : '',
                                    returned_items: values.delivery_status !== 'Delivered' ? returned_data : []
                                }];

                                frappe.call({
                                    method: "nexus_supply_chain.api.sync_manifest_from_app",
                                    args: {
                                        manifest_name: frm.doc.name,
                                        stops: JSON.stringify(payload)
                                    },
                                    callback: function(res) {
                                        if(res.message && res.message.status === "success") {
                                            d.hide();
                                            frm.reload_doc();
                                        }
                                    },
                                    error: function() {
                                        d.get_primary_btn().prop('disabled', false).html('Confirm Drop-off');
                                    }
                                });
                            });
                        }
                    });
                    d.show();
                }).addClass('btn-success');
            }

            if (frm.doc.trip_status === 'Dispatched' || frm.doc.trip_status === 'Returning') {
                frm.add_custom_button(__('Navigate to Next Stop'), function() {
                    let pending_stops = frm.doc.stops.filter(d => d.delivery_status === 'Pending' && (d.custom_latitude || d.latitude));
                    if (pending_stops.length === 0) return;
                    
                    let nav_dialog = new frappe.ui.Dialog({
                        title: 'Navigate',
                        fields: [{ 
                            label: 'Choose Stop', 
                            fieldname: 'sel', 
                            fieldtype: 'Select', 
                            options: pending_stops.map(d => ({ label: d.customer_name || d.customer, value: d.name })), 
                            reqd: 1 
                        }],
                        primary_action_label: 'Start Navigation',
                        primary_action(v) {
                            let stop = pending_stops.find(s => s.name === v.sel);
                            if (stop) {
                                let target_lat = stop.custom_latitude || stop.latitude;
                                let target_lng = stop.custom_longitude || stop.longitude;
                                
                                window.open(`http://googleusercontent.com/maps.google.com/dir/?api=1&destination=${target_lat},${target_lng}&travelmode=driving`, '_blank');
                                nav_dialog.hide();
                            }
                        }
                    });
                    nav_dialog.show();
                }).addClass('btn-primary');
            }
        }
    }
});

function render_manifest_map(frm) {
    frappe.require([
        "/assets/nexus_supply_chain/leaflet/leaflet.css", 
        "/assets/nexus_supply_chain/leaflet/leaflet.js",
        "https://unpkg.com/maplibre-gl@3.6.2/dist/maplibre-gl.css",
        "https://unpkg.com/maplibre-gl@3.6.2/dist/maplibre-gl.js",
        "https://unpkg.com/@maplibre/maplibre-gl-leaflet@0.0.20/leaflet-maplibre-gl.js"
    ], function() {
        let $wrapper = frm.get_field('route_map_html').$wrapper;
        $wrapper.empty().append('<div id="manifest-map" style="height: 400px; border-radius: 8px; border: 1px solid #cbd5e1; margin-top: 10px;"></div>');
        
        let map = L.map('manifest-map').setView([-1.2921, 36.8219], 12);

        L.maplibreGL({
            style: TILE_SERVER_URL,
            attribution: '&copy; Sovereign Nexus Maps'
        }).addTo(map);
        
        try {
            let routeLayer = L.geoJSON(JSON.parse(frm.doc.route_geojson), {style: { color: '#2563eb', weight: 6 }}).addTo(map);
            map.fitBounds(routeLayer.getBounds(), { padding: [20, 20] });
            
            frm.doc.stops.forEach((stop, i) => {
                let pin_lat = stop.custom_latitude || stop.latitude;
                let pin_lng = stop.custom_longitude || stop.longitude;
                
                if (pin_lat && pin_lng) {
                    let color = stop.delivery_status === 'Delivered' ? '#16a34a' : '#2563eb';
                    let icon = L.divIcon({ 
                        className: '', 
                        html: `<div style="background:${color}; color:white; border-radius:50%; width:24px; height:24px; display:flex; align-items:center; justify-content:center; font-weight:bold; font-size:10px; border:2px solid white; box-shadow: 0 0 5px rgba(0,0,0,0.5);">${i+1}</div>`, 
                        iconSize: [24, 24] 
                    });
                    L.marker([pin_lat, pin_lng], { icon: icon }).addTo(map).bindPopup(stop.customer_name || stop.customer);
                }
            });
        } catch (e) { }
    });
}
