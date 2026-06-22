app_name = "nexus_supply_chain"
app_title = "Nexus Supply Chain"
app_publisher = "leon"
app_description = "Nexus Supply Chain"
app_email = "gnleon29@gmail.com"
app_license = ""

doc_events = {
    "Customer": {
        "after_insert": [
            "nexus_supply_chain.api.queue_customer_geocoding",
            "nexus_supply_chain.api.trigger_cache_eviction_and_notify"
        ],
        "on_change": [
            "nexus_supply_chain.api.queue_customer_geocoding",
            "nexus_supply_chain.api.trigger_cache_eviction_and_notify"
        ]
    },
    
    "Item": {
        "on_update": [
            "nexus_supply_chain.api.trigger_cache_eviction_and_notify",
            "nexus_supply_chain.api.publish_catalog_update"
        ]
    },
    "Item Price": {
        "on_update": [
            "nexus_supply_chain.api.trigger_cache_eviction_and_notify",
            "nexus_supply_chain.api.publish_catalog_update"
        ]
    },

    "Nexus Inventory Reservation": {
        # "on_update": "nexus_supply_chain.reservation_hooks.process_reservation_update",
        # "before_submit": "nexus_supply_chain.reservation_hooks.prepare_reservation_submission",
        # "before_cancel": "nexus_supply_chain.reservation_hooks.validate_reservation_cancel",
        # "on_cancel": "nexus_supply_chain.reservation_hooks.process_reservation_cancel"
    },
    
    "Delivery Note": {
        # "validate": "nexus_supply_chain.reservation_hooks.validate_delivery_note_submission",
        "on_submit": [
            # "nexus_supply_chain.reservation_hooks.process_delivery_note",
            "nexus_supply_chain.api.trigger_cache_eviction_and_notify"
        ],
        "on_cancel": [
            # "nexus_supply_chain.reservation_hooks.process_delivery_note_cancel",
            "nexus_supply_chain.api.trigger_cache_eviction_and_notify"
        ]
    },
    
    "Sales Order": {
        "on_update": [
            "nexus_supply_chain.api.trigger_order_status_update",
            "nexus_supply_chain.api.trigger_cache_eviction_and_notify"
        ],
        "on_cancel": [
            # "nexus_supply_chain.reservation_hooks.process_sales_order_update",
            "nexus_supply_chain.api.trigger_cache_eviction_and_notify"
        ]
    },
    
    "Payment Entry": {
        "on_submit": "nexus_supply_chain.api.trigger_cache_eviction_and_notify",
        "on_cancel": "nexus_supply_chain.api.trigger_cache_eviction_and_notify"
    },
    
    "Sales Invoice": {
        "on_submit": "nexus_supply_chain.api.trigger_cache_eviction_and_notify",
        "on_cancel": "nexus_supply_chain.api.trigger_cache_eviction_and_notify"
    },
    
    "Stock Entry": {
        "on_submit": [
            # "nexus_supply_chain.reservation_hooks.process_stock_movement",
            "nexus_supply_chain.api.trigger_cache_eviction_and_notify",
            # "nexus_supply_chain.page.nexus_executive_command.nexus_executive_command.publish_realtime_production"
        ],
        "on_cancel": [
            # "nexus_supply_chain.reservation_hooks.process_stock_movement_cancel",
            "nexus_supply_chain.api.trigger_cache_eviction_and_notify",
            # "nexus_supply_chain.page.nexus_executive_command.nexus_executive_command.publish_realtime_production"
        ]
    },
    
    "Stock Reconciliation": {
        "on_submit": [
            # "nexus_supply_chain.reservation_hooks.process_stock_movement",
            "nexus_supply_chain.api.trigger_cache_eviction_and_notify"
        ],
        "on_cancel": [
            # "nexus_supply_chain.reservation_hooks.process_stock_movement_cancel",
            "nexus_supply_chain.api.trigger_cache_eviction_and_notify"
        ]
    },
    
    "Purchase Receipt": {
        "on_submit": [
            # "nexus_supply_chain.reservation_hooks.process_stock_movement",
            "nexus_supply_chain.api.trigger_cache_eviction_and_notify"
        ],
        "on_cancel": [
            # "nexus_supply_chain.reservation_hooks.process_stock_movement_cancel",
            "nexus_supply_chain.api.trigger_cache_eviction_and_notify"
        ]
    },
    
    "Sales Person": {
        "on_update": "nexus_supply_chain.api.trigger_cache_eviction_and_notify"
    },

    "Customer Group": {
        "on_update": "nexus_supply_chain.api.trigger_cache_eviction_and_notify",
        "on_change": "nexus_supply_chain.api.trigger_cache_eviction_and_notify",
        "on_trash": "nexus_supply_chain.api.trigger_cache_eviction_and_notify"
    },
    "Territory": {
        "on_update": "nexus_supply_chain.api.trigger_cache_eviction_and_notify",
        "on_change": "nexus_supply_chain.api.trigger_cache_eviction_and_notify",
        "on_trash": "nexus_supply_chain.api.trigger_cache_eviction_and_notify"
    },
    "Currency": {
        "on_update": "nexus_supply_chain.api.trigger_cache_eviction_and_notify",
        "on_change": "nexus_supply_chain.api.trigger_cache_eviction_and_notify",
        "on_trash": "nexus_supply_chain.api.trigger_cache_eviction_and_notify"
    },
    "Tax Category": {
        "on_update": "nexus_supply_chain.api.trigger_cache_eviction_and_notify",
        "on_change": "nexus_supply_chain.api.trigger_cache_eviction_and_notify",
        "on_trash": "nexus_supply_chain.api.trigger_cache_eviction_and_notify"
    },

    "Data Import": {
        "on_update": "nexus_supply_chain.api.trigger_post_import_cache_eviction"
    }
}

scheduler_events = {
    "cron": {
        "* * * * *": [
            "nexus_supply_chain.api.process_debounced_cache_eviction"
        ],
        
        "*/10 * * * *": [
            "nexus_supply_chain.api.process_bulk_geocoding_queue"
        ]
    }
}