frappe.pages['customer_spatial_map'].on_page_load = function(wrapper) {
	let page = frappe.ui.make_app_page({
		parent: wrapper,
		title: 'Nexus Spatial Intelligence',
		single_column: true
	});

	$(wrapper).find('.layout-main-section').append(`
		<style>
			.nexus-static-marker {
				background-color: #1e3a8a;
				color: #ffffff;
				width: 28px;
				height: 28px;
				display: flex;
				align-items: center;
				justify-content: center;
				border-radius: 50%;
				border: 2px solid #ffffff;
				box-shadow: 0 2px 5px rgba(0, 0, 0, 0.4);
			}
			
			.nexus-company-marker {
				background-color: #0f172a;
				color: #fbbf24;
				width: 42px;
				height: 42px;
				display: flex;
				align-items: center;
				justify-content: center;
				border-radius: 10px;
				border: 2px solid #ffffff;
				box-shadow: 0 5px 15px rgba(0, 0, 0, 0.5);
				z-index: 1000 !important;
			}

			.custom-nexus-icon {
				background: none;
				border: none;
			}
		</style>

		<div id="spatial-map-container" style="height: 75vh; width: 100%; border-radius: 12px; box-shadow: 0 8px 24px rgba(0,0,0,0.12); z-index: 1; border: 1px solid #e2e8f0;"></div>
	\`);

	frappe.require([
		"/assets/nexus_supply_chain/leaflet/leaflet.css",
		"/assets/nexus_supply_chain/leaflet/leaflet.js",
		"/assets/nexus_supply_chain/leaflet/MarkerCluster.css",
		"/assets/nexus_supply_chain/leaflet/MarkerCluster.Default.css",
		"/assets/nexus_supply_chain/leaflet/leaflet.markercluster.js"
	], function() {
		init_spatial_map();
	});

	function init_spatial_map() {
		let map = L.map('spatial-map-container', {
			preferCanvas: true 
		}).setView([-1.2921, 36.8219], 6);

		map.attributionControl.setPrefix('<b>Nexus Spatial Intelligence</b>');
		L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
			attribution: 'Data &copy; <a href="https://www.openstreetmap.org/copyright">OSM</a> | Routing by Crystal Adhesives Ltd'
		}).addTo(map);

		let markersClusterGroup = L.markerClusterGroup({
			chunkedLoading: true,
			maxClusterRadius: 50
		});

		let bounds = [];

		frappe.db.get_list('Company', {
			fields: ['name', 'company_name', 'custom_latitude', 'custom_longitude']
		}).then(companies => {
			
			let companyIcon = L.divIcon({
				className: 'custom-nexus-icon',
				html: `
					<div class="nexus-company-marker">
						<i class="fa fa-industry" style="font-size: 20px;"></i>
					</div>
				`,
				iconSize: [42, 42],
				iconAnchor: [21, 21],
				popupAnchor: [0, -22]
			});

			companies.forEach(comp => {
				let c_lat = parseFloat(comp.custom_latitude);
				let c_lng = parseFloat(comp.custom_longitude);

				if (c_lat && c_lng) {
					let comp_marker = L.marker([c_lat, c_lng], { icon: companyIcon, zIndexOffset: 1000 }).addTo(map);
					
					let popup_html = `
						<div style="min-width: 200px; text-align: left;">
							<h6 class="fw-bold mb-1" style="color: #0f172a;"><i class="fa fa-star text-warning"></i> \${comp.company_name || comp.name}</h6>
							<hr class="my-2" style="border-top: 1px solid #e2e8f0;">
							<div class="small"><b>Primary Dispatch / Factory Location</b></div>
						</div>
					`;
					
					comp_marker.bindPopup(popup_html, { closeButton: false });
					bounds.push([c_lat, c_lng]);
				}
			});

			frappe.call({
				method: "nexus_supply_chain.nexus_supply_chain.page.customer_spatial_map.customer_spatial_map.get_mapped_customers",
				freeze: true,
				freeze_message: "Acquiring & Clustering Spatial Data...",
				callback: function(r) {
					if (r.message && r.message.length > 0) {
						
						let nexusStaticIcon = L.divIcon({
							className: 'custom-nexus-icon',
							html: `
								<div class="nexus-static-marker">
									<i class="fa fa-store" style="font-size: 10px;"></i>
								</div>
							`,
							iconSize: [28, 28],
							iconAnchor: [14, 14],
							popupAnchor: [0, -15]
						});

						r.message.forEach(cust => {
							let lat = parseFloat(cust.custom_latitude);
							let lng = parseFloat(cust.custom_longitude);
							
							if (lat && lng) {
								let marker = L.marker([lat, lng], { icon: nexusStaticIcon });
								
								let popup_html = `
									<div style="min-width: 200px; text-align: left;">
										<h6 class="fw-bold mb-1 text-primary" style="color: #1e3a8a;">\${cust.customer_name}</h6>
										<div class="text-muted small mb-2">\${cust.name}</div>
										<hr class="my-2" style="border-top: 1px solid #e2e8f0;">
										<div class="small"><b>Territory:</b> \${cust.territory || 'Unassigned'}</div>
										<div class="small"><b>Group:</b> \${cust.customer_group || 'Standard'}</div>
									</div>
								`;
								
								marker.bindPopup(popup_html, { closeButton: false });
								
								markersClusterGroup.addLayer(marker);
								bounds.push([lat, lng]);
							}
						});

						map.addLayer(markersClusterGroup);

						if (bounds.length > 0) {
							map.fitBounds(bounds, {padding: [50, 50]});
						}
						
						frappe.show_alert({message: `Mapped Factory and clustered \${r.message.length} customers.`, indicator: "green"});
					} else {
						if (bounds.length > 0) {
							map.fitBounds(bounds, {padding: [50, 50], maxZoom: 12});
						}
						frappe.msgprint({
							title: "No Customer Data",
							message: "No customers were found with extracted GPS coordinates.",
							indicator: "orange"
						});
					}
				}
			});
		});
	}
};
