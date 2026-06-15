app_name = "nexus_supply_chain"
app_title = "Nexus Supply Chain"
app_publisher = "leon"
app_description = "Nexus Supply Chain"
app_email = "gnleon29@gmail.com"
app_license = "mit"

doc_events = {
    "Customer": {
        "before_save": [
            "nexus_supply_chain.utils.geocoding.queue_customer_geocoding",
            "nexus_supply_chain.api.trigger_cache_eviction_and_notify"
        ]
    },
    
    "Item": {
        "on_update": "nexus_supply_chain.api.trigger_cache_eviction_and_notify"
    },
    "Item Price": {
        "on_update": [
            "nexus_supply_chain.api.trigger_cache_eviction_and_notify",
            "nexus_supply_chain.api.trigger_portfolio_cache_invalidation" 
        ]
    },

    "Nexus Inventory Reservation": {
        "on_update": "nexus_supply_chain.reservation_hooks.process_reservation_update",
        "before_submit": "nexus_supply_chain.reservation_hooks.prepare_reservation_submission",
        "before_cancel": "nexus_supply_chain.reservation_hooks.validate_reservation_cancel",
        "on_cancel": "nexus_supply_chain.reservation_hooks.process_reservation_cancel"
    },
    
    "Delivery Note": {
        "validate": "nexus_supply_chain.reservation_hooks.validate_delivery_note_submission",
        "on_submit": [
            "nexus_supply_chain.reservation_hooks.process_delivery_note",
            "nexus_supply_chain.api.trigger_cache_eviction_and_notify"
        ],
        "on_cancel": [
            "nexus_supply_chain.reservation_hooks.process_delivery_note_cancel",
            "nexus_supply_chain.api.trigger_cache_eviction_and_notify"
        ]
    },
    
    "Sales Order": {
        "on_update": [
            "nexus_supply_chain.api.trigger_order_status_update",
            "nexus_supply_chain.api.trigger_cache_eviction_and_notify"
        ],
        "on_cancel": [
            "nexus_supply_chain.reservation_hooks.process_sales_order_update",
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
            "nexus_supply_chain.reservation_hooks.process_stock_movement",
            "nexus_supply_chain.api.trigger_cache_eviction_and_notify"
        ],
        "on_cancel": [
            "nexus_supply_chain.reservation_hooks.process_stock_movement_cancel",
            "nexus_supply_chain.api.trigger_cache_eviction_and_notify"
        ]
    },
    
    "Stock Reconciliation": {
        "on_submit": [
            "nexus_supply_chain.reservation_hooks.process_stock_movement",
            "nexus_supply_chain.api.trigger_cache_eviction_and_notify"
        ],
        "on_cancel": [
            "nexus_supply_chain.reservation_hooks.process_stock_movement_cancel",
            "nexus_supply_chain.api.trigger_cache_eviction_and_notify"
        ]
    },
    
    "Purchase Receipt": {
        "on_submit": [
            "nexus_supply_chain.reservation_hooks.process_stock_movement",
            "nexus_supply_chain.api.trigger_cache_eviction_and_notify"
        ],
        "on_cancel": [
            "nexus_supply_chain.reservation_hooks.process_stock_movement_cancel",
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
