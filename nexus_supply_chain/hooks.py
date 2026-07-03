app_name = "nexus_supply_chain"
app_title = "Nexus Supply Chain"
app_publisher = "leon"
app_description = "Nexus Supply Chain"
app_email = "gnleon29@gmail.com"
app_license = ""


# Document events - Mapping your Python logic to Frappe triggers

# apps/nexus_supply_chain/nexus_supply_chain/hooks.py

doc_events = {
    "Customer": {
        # 🚨 EVENTUAL CONSISTENCY MODEL: 0-Lag Background Geocoding.
        # Fires only AFTER MariaDB has safely committed the record and released the row lock.
        # This completely eliminates UI freezing on the "Save" button.
        "after_insert": [
            "nexus_supply_chain.api.queue_customer_geocoding",
            "nexus_supply_chain.api.trigger_cache_eviction_and_notify"
        ],
        "on_change": [
            "nexus_supply_chain.api.queue_customer_geocoding",
            "nexus_supply_chain.api.trigger_cache_eviction_and_notify"
        ]
    },
    
   # 🚨 UNIFIED CATALOG TRIGGERS
    "Item": {
        "on_update": [
            "nexus_supply_chain.api.trigger_cache_eviction_and_notify",
            "nexus_supply_chain.api.publish_catalog_update" #
        ]
    },
    "Item Price": {
        "on_update": [
            "nexus_supply_chain.api.trigger_cache_eviction_and_notify",
            "nexus_supply_chain.api.publish_catalog_update" # 
        ]
    },

    # 🚨 INACTIVE RESERVATION HOOKS COMMENTED OUT
    # "Nexus Inventory Reservation": {
    #     "on_update": "nexus_supply_chain.reservation_hooks.process_reservation_update",
    #     "before_submit": "nexus_supply_chain.reservation_hooks.prepare_reservation_submission",
    #     "before_cancel": "nexus_supply_chain.reservation_hooks.validate_reservation_cancel",
    #     "on_cancel": "nexus_supply_chain.reservation_hooks.process_reservation_cancel"
    # },
    
    # 🚨 STOCK MOVEMENT TRIGGERS
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
        # 🚨 LIVE LIFECYCLE TRACKING + CACHE EVICTION
        "on_update": [
            "nexus_supply_chain.api.trigger_order_status_update",
            "nexus_supply_chain.api.trigger_cache_eviction_and_notify"
        ],
        "on_cancel": [
            # "nexus_supply_chain.reservation_hooks.process_sales_order_update",
            "nexus_supply_chain.api.trigger_cache_eviction_and_notify"
        ]
    },
    
    # 🚨 FINANCIAL TRIGGERS
    # Clears Redis cache to update debt snapshots and dashboard MTD collections.
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
            "nexus_supply_chain.page.nexus_executive_command.nexus_executive_command.publish_realtime_production"
        ],
        "on_cancel": [
            # "nexus_supply_chain.reservation_hooks.process_stock_movement_cancel",
            "nexus_supply_chain.api.trigger_cache_eviction_and_notify",
            "nexus_supply_chain.page.nexus_executive_command.nexus_executive_command.publish_realtime_production"
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
    
    # 🚨 TARGET MANAGEMENT TRIGGER
    "Sales Person": {
        "on_update": "nexus_supply_chain.api.trigger_cache_eviction_and_notify"
    },

    # 🚨 MASTER METADATA STRUCTURAL TRIGGERS
    # Forces absolute global system invalidation cache sweeps if dropdown matrices change.
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

    # 🚨 BULK DATA IMPORT TRIGGERS (The Thundering Herd Shield)
    # Fires exactly once after a Frappe v15 Data Import completely finishes.
    "Data Import": {
        "on_update": "nexus_supply_chain.api.trigger_post_import_cache_eviction"
    }
}

# 🚨 SCHEDULED ORCHESTRATOR (STRATEGY B: The Redis Debounce Buffer)
# This cron job runs every 1 minute. It will check the Redis cache flag and 
# fire a single webhook to FastAPI if any records were updated in the last 60 seconds.
# 🚨 SCHEDULED ORCHESTRATORS
scheduler_events = {
    "cron": {
        # 1. Strategy B: Redis Debounce Buffer (Runs every 1 minute)
        # Checks the Redis cache flag and fires a single cache-eviction webhook to FastAPI 
        # if any records were updated in the last 60 seconds.
        "* * * * *": [
            "nexus_supply_chain.api.process_debounced_cache_eviction"
        ],
        
        # 2. Strategy D: The Slow-Drip Batcher (Runs every 10 minutes)
        # The Cleanup Crew: Quietly processes bulk-imported customers in the background.
        # It bypasses Frappe's synchronous UI hooks and avoids Google Map API bans.
        "*/10 * * * *": [
            "nexus_supply_chain.api.process_bulk_geocoding_queue"
        ]
    }
}

# -----------------------------------------------------------
# ENTERPRISE SECURITY HOOKS: Row-Level Access Control
# -----------------------------------------------------------
# Optional: if you later add more frequent checks via settings
# You can dynamically enable/disable in code, but for now daily is safe

# ────────────────────────────────────────────────────────────────────────────────
# Document Hooks
# ────────────────────────────────────────────────────────────────────────────────


# ────────────────────────────────────────────────────────────────────────────────
# Required for App Functionality
# ────────────────────────────────────────────────────────────────────────────────
# Ensure your custom background jobs or periodic cleanup for expired 
# reservations are defined in scheduler_events if needed.