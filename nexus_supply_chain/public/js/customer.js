// apps/nexus_supply_chain/nexus_supply_chain/public/js/customer.js

frappe.ui.form.on('Customer', {
    refresh: function(frm) {
        // Load local Leaflet files!
        if (frm.doc.custom_latitude && frm.doc.custom_longitude) {
            frappe.require([
                "/assets/nexus_supply_chain/leaflet/leaflet.css",
                "/assets/nexus_supply_chain/leaflet/leaflet.js"
            ], function() {
                render_mini_map(frm);
            });
        }
    },
    
    custom_google_maps_link: function(frm) {
        if (frm.doc.custom_google_maps_link) {
            // Show a non-blocking toast notification
            frappe.show_alert({message: "Extracting coordinates in the background...", indicator: "blue"});
            
            // Dynamically match the current browser hostname to avoid CORS/Localhost blocks
            let api_url = `http://${window.location.hostname}:8001/extract-coordinates`;
            
            // Asynchronous fetch - does not freeze the UI!
            fetch(api_url, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ url: frm.doc.custom_google_maps_link })
            })
            .then(response => response.json())
            .then(data => {
                if (data.status === 'success') {
                    // Inject data instantly
                    frm.set_value('custom_latitude', data.lat);
                    frm.set_value('custom_longitude', data.lng);
                    frappe.show_alert({message: "Location Locked! You can now save.", indicator: "green"});
                    
                    // Load local Leaflet files!
                    frappe.require([
                        "/assets/nexus_supply_chain/leaflet/leaflet.css",
                        "/assets/nexus_supply_chain/leaflet/leaflet.js"
                    ], function() {
                        render_mini_map(frm);
                    });
                } else {
                    frappe.msgprint({title: 'Extraction Failed', indicator: 'red', message: data.message});
                }
            })
            .catch(error => {
                console.error("API Error:", error);
                frappe.msgprint("Could not reach Nexus Brain API. Is it running on port 8001?");
            });
        }
    }
});

function render_mini_map(frm) {
    let lat = frm.doc.custom_latitude;
    let lng = frm.doc.custom_longitude;
    
    if (!lat || !lng) return;

    // Build the HTML wrapper for the map WITH embedded custom CSS for our premium marker
    let map_html = `
        <style>
            /* CSS for our professional Nexus Map Marker */
            .nexus-marker-wrapper {
                background-color: #1e3a8a; /* Deep corporate blue */
                color: #ffffff;
                width: 36px;
                height: 36px;
                display: flex;
                align-items: center;
                justify-content: center;
                border-radius: 50%;
                border: 3px solid #ffffff;
                box-shadow: 0 4px 10px rgba(0, 0, 0, 0.3);
                transition: transform 0.2s ease-in-out;
            }
            .nexus-marker-wrapper:hover {
                transform: scale(1.15); /* Interactive pop on hover */
                background-color: #2563eb;
            }
            .nexus-marker-pulse {
                position: absolute;
                width: 36px;
                height: 36px;
                background-color: #2563eb;
                border-radius: 50%;
                z-index: -1;
                animation: pulse-animation 2s infinite;
            }
            @keyframes pulse-animation {
                0% { transform: scale(1); opacity: 0.7; }
                100% { transform: scale(1.8); opacity: 0; }
            }
        </style>
        
        <div style="border: 1px solid #d1d5db; border-radius: 8px; overflow: hidden; margin-top: 15px;">
            <div id="customer-mini-map" style="height: 300px; width: 100%;"></div>
        </div>
    `;
    
    // Clear any existing map HTML to prevent Leaflet "already initialized" errors, then inject
    $(frm.fields_dict.custom_location_map.wrapper).empty().html(map_html);

    // Initialize Leaflet
    setTimeout(() => {
        let map_container = document.getElementById('customer-mini-map');
        if (!map_container) return;

        let map = L.map('customer-mini-map').setView([lat, lng], 15);
        
        // Change the Prefix
        map.attributionControl.setPrefix('<b>Nexus Spatial Intelligence</b>');

        // Change the Map Tile Attribution
        L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
            attribution: 'Data &copy; <a href="https://www.openstreetmap.org/copyright">OSM</a> | Routing by Crystal Adhesives Ltd'
        }).addTo(map);
        
        let customer_name = frm.doc.customer_name || 'Customer';

        // THE UPGRADE: Create the Professional Nexus Icon
        let nexusIcon = L.divIcon({
            className: 'custom-nexus-icon', // Using a custom class removes the default white box background
            html: `
                <div class="nexus-marker-pulse"></div>
                <div class="nexus-marker-wrapper">
                    <i class="fa fa-store" style="font-size: 14px;"></i>
                </div>
            `,
            iconSize: [36, 36],
            iconAnchor: [18, 18], // Centers the icon perfectly on the coordinate
            popupAnchor: [0, -20] // Opens the popup directly above the icon
        });

        // Add the marker and fix the ERPNext #close router error
        L.marker([lat, lng], { icon: nexusIcon }).addTo(map)
            .bindPopup(
                `<div style="text-align: center; min-width: 120px;">
                    <b style="font-size: 13px; color: #1e3a8a;">${customer_name}</b><br>
                    <span style="color: #64748b; font-size: 11px;">Delivery Location</span>
                </div>`,
                { closeButton: false } // THIS LINE FIXES THE "PAGE NOT FOUND" ERROR
            )
            .openPopup();

        // Automatically force Leaflet to redraw the moment the hidden tab is opened
        const observer = new ResizeObserver(() => {
            map.invalidateSize();
        });
        observer.observe(map_container);

    }, 300);
}