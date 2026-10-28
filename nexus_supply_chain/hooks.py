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
        # queue_party_geocoding queues at most ONE job per save (after_insert
        # and on_change both fire on insert; a per-save flag dedupes), and the
        # job runs only after the commit, so Save never waits on Google.
        "after_insert": [
            "nexus_supply_chain.api.queue_party_geocoding",
            "nexus_supply_chain.api.trigger_cache_eviction_and_notify"
        ],
        # 🚨 LEAD CONVERSION: fires once when a Customer is created from a Lead
        # (lead_name newly set) — closes open lead visits and notifies the app.
        "on_update": [
            "nexus_supply_chain.api.handle_lead_conversion"
            # Desk pin edits move the open delivery stops and recalculate those
            # trips' planned distance (app and geocoder writes call it directly).
            "nexus_supply_chain.driver_api.on_customer_update"
        ],
        "on_change": [
            "nexus_supply_chain.api.queue_party_geocoding",
            "nexus_supply_chain.api.trigger_cache_eviction_and_notify"
        ]
    },

    # 🚨 LEAD SYNC + GEOCODING: office-side lead changes reach the owner's and
    # managers' apps (on_update also fires on insert). A pasted Google Maps
    # link is turned into coordinates in the background, same as customers.
    "Lead": {
        "after_insert": "nexus_supply_chain.api.queue_party_geocoding",
        "on_update": "nexus_supply_chain.api.trigger_lead_refresh",
        "on_change": "nexus_supply_chain.api.queue_party_geocoding",
        "on_trash": "nexus_supply_chain.api.trigger_lead_refresh"
    },

    # 🚨 Lead status changes made by ERPNext itself (Opportunity / Quotation
    # linked to a Lead) reach the app live, not only on the next sync.
    "Opportunity": {
        "on_change": "nexus_supply_chain.api.trigger_lead_refresh_from_linked",
        "on_trash": "nexus_supply_chain.api.trigger_lead_refresh_from_linked"
    },
    "Quotation": {
        "on_change": "nexus_supply_chain.api.trigger_lead_refresh_from_linked",
        "on_trash": "nexus_supply_chain.api.trigger_lead_refresh_from_linked"
    },

    # 🚨 UNIFIED CATALOG TRIGGERS
    "Item": {
        "validate": "nexus_supply_chain.production.validations.validate_item",
        "on_update": [
            "nexus_supply_chain.api.trigger_cache_eviction_and_notify",
            "nexus_supply_chain.api.publish_catalog_update"
        ],
        "on_change": "nexus_supply_chain.production.signals.on_doc_change",   # NEXUS LIVE (production fields only)
        "on_trash": "nexus_supply_chain.production.signals.on_doc_change"     # NEXUS LIVE
    },
    "Item Price": {
        "on_update": [
            "nexus_supply_chain.api.trigger_cache_eviction_and_notify",
            "nexus_supply_chain.api.publish_catalog_update"
        ]
    },

    # 🚨 STOCK MOVEMENT TRIGGERS
    "Delivery Note": {
        "after_insert": "nexus_supply_chain.production.signals.on_doc_change",   # NEXUS LIVE (a draft DN takes its order out)
        "on_submit": [
            "nexus_supply_chain.api.trigger_cache_eviction_and_notify",
            "nexus_supply_chain.production.signals.on_doc_change"              # NEXUS LIVE
        ],
        "on_cancel": [
            "nexus_supply_chain.api.trigger_cache_eviction_and_notify",
            "nexus_supply_chain.production.signals.on_doc_change"              # NEXUS LIVE
        ],
        "on_trash": "nexus_supply_chain.production.signals.on_doc_change",      # NEXUS LIVE
        "before_submit": [
            "nexus_supply_chain.utils.delivery_note_hooks.set_cogs_snapshot"
        ]
    },

    "Sales Order": {
        "before_submit": "nexus_supply_chain.production.validations.stamp_confirmed_on",
        "on_update": [
            "nexus_supply_chain.api.trigger_order_status_update",
            "nexus_supply_chain.api.trigger_cache_eviction_and_notify"
        ],
        "on_cancel": [
            "nexus_supply_chain.api.trigger_cache_eviction_and_notify"
        ],
        "on_change": "nexus_supply_chain.production.signals.on_doc_change",     # NEXUS LIVE (submit, close, hold, delivered)
        "on_trash": "nexus_supply_chain.production.signals.on_doc_change"       # NEXUS LIVE (deleted drafts)
    },

    # 🚨 FINANCIAL TRIGGERS
    # Clears Redis cache to update debt snapshots and dashboard MTD collections.
    "Payment Entry": {
        "on_submit": "nexus_supply_chain.api.trigger_financial_refresh",
        "on_cancel": "nexus_supply_chain.api.trigger_financial_refresh",
        "on_update_after_submit": "nexus_supply_chain.api.trigger_financial_refresh",
    },
    "Journal Entry": {
        "on_submit": "nexus_supply_chain.api.trigger_financial_refresh",
        "on_cancel": "nexus_supply_chain.api.trigger_financial_refresh",
    },

    "Sales Invoice": {
        "after_insert": "nexus_supply_chain.production.signals.on_doc_change",   # NEXUS LIVE (a draft SI takes its order out)
        "on_submit": [
            "nexus_supply_chain.api.trigger_cache_eviction_and_notify",
            "nexus_supply_chain.production.signals.on_doc_change"              # NEXUS LIVE
        ],
        "on_cancel": [
            "nexus_supply_chain.api.trigger_cache_eviction_and_notify",
            "nexus_supply_chain.production.signals.on_doc_change"              # NEXUS LIVE
        ],
        "on_trash": "nexus_supply_chain.production.signals.on_doc_change",      # NEXUS LIVE
        "before_submit": "nexus_supply_chain.utils.sales_invoice_hooks.set_cogs_snapshot"
    },

    "Stock Entry": {
        "on_submit": [
            "nexus_supply_chain.api.trigger_cache_eviction_and_notify"
        ],
        "on_cancel": [
            "nexus_supply_chain.api.trigger_cache_eviction_and_notify"
        ],
        "on_change": "nexus_supply_chain.production.signals.on_doc_change"      # NEXUS LIVE
    },

    "Stock Reconciliation": {
        "on_submit": [
            "nexus_supply_chain.api.trigger_cache_eviction_and_notify"
        ],
        "on_cancel": [
            "nexus_supply_chain.api.trigger_cache_eviction_and_notify"
        ],
        "on_change": "nexus_supply_chain.production.signals.on_doc_change"      # NEXUS LIVE
    },

    "Purchase Receipt": {
        "on_submit": [
            "nexus_supply_chain.api.trigger_cache_eviction_and_notify"
        ],
        "on_cancel": [
            "nexus_supply_chain.api.trigger_cache_eviction_and_notify"
        ],
        "on_change": "nexus_supply_chain.production.signals.on_doc_change"      # NEXUS LIVE
    },

    # NEXUS LIVE: production documents (only submitted / cancelled ones send a signal)
    "Work Order": {
        "on_change": "nexus_supply_chain.production.signals.on_doc_change"
    },
    "Material Request": {
        "on_change": "nexus_supply_chain.production.signals.on_doc_change"
    },
    "Purchase Order": {
        "on_change": "nexus_supply_chain.production.signals.on_doc_change"
    },
    "Nexus Production Settings": {
        "on_change": "nexus_supply_chain.production.signals.on_doc_change"
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
    },

    # NEXUS PRODUCTION: keep parent BOMs on each component's current default BOM
    "BOM": {
        "on_submit": "nexus_supply_chain.production.bom_sync.on_bom_default_change",
        "on_update_after_submit": "nexus_supply_chain.production.bom_sync.on_bom_default_change",
        "on_change": "nexus_supply_chain.production.signals.on_doc_change"      # NEXUS LIVE
    }
}


# 🚨 SCHEDULED ORCHESTRATORS
scheduler_events = {
    "cron": {
        # 1. Strategy B: Redis Debounce Buffer (Runs every 1 minute)
        # Checks the Redis cache flag and fires a single cache-eviction webhook to FastAPI
        # if any records were updated in the last 60 seconds.
        "* * * * *": [
            "nexus_supply_chain.api.process_debounced_cache_eviction",
            # NEXUS LIVE: sends production board changes held back by the 30 s limit
            "nexus_supply_chain.production.signals.flush_pending"
        ],

        # 2. Trip distances (every 5 minutes): retries planned-route
        # recalculations that couldn't reach the routing server, and records
        # the distance actually driven for trips completed 10+ minutes ago.
        "*/5 * * * *": [
            "nexus_supply_chain.route_recompute.run_maintenance"
        ],

        # 3. Strategy D: The Slow-Drip Batcher (Runs every 10 minutes)
        # The Cleanup Crew: Quietly processes bulk-imported customers in the background.
        # It bypasses Frappe's synchronous UI hooks and avoids Google Map API bans.
        "*/10 * * * *": [
            "nexus_supply_chain.api.process_bulk_geocoding_queue"
        ]
    }
}

before_request = [
    "nexus_supply_chain.api.enforce_minimum_app_version"
]

after_migrate = [
    "nexus_supply_chain.production.custom_fields.ensure_custom_fields"
]

boot_session = "nexus_supply_chain.boot.boot_session"



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


# Apps
# ------------------

# required_apps = []

# Each item in the list will be shown as an app in the apps page
# add_to_apps_screen = [
# 	{
# 		"name": "nexus_supply_chain",
# 		"logo": "/assets/nexus_supply_chain/logo.png",
# 		"title": "Nexus Supply Chain",
# 		"route": "/nexus_supply_chain",
# 		"has_permission": "nexus_supply_chain.api.permission.has_app_permission"
# 	}
# ]

# Includes in <head>
# ------------------

# include js, css files in header of desk.html
# app_include_css = "/assets/nexus_supply_chain/css/nexus_supply_chain.css"
# app_include_js = "/assets/nexus_supply_chain/js/nexus_supply_chain.js"

# include js, css files in header of web template
# web_include_css = "/assets/nexus_supply_chain/css/nexus_supply_chain.css"
# web_include_js = "/assets/nexus_supply_chain/js/nexus_supply_chain.js"

# include custom scss in every website theme (without file extension ".scss")
# website_theme_scss = "nexus_supply_chain/public/scss/website"

# include js, css files in header of web form
# webform_include_js = {"doctype": "public/js/doctype.js"}
# webform_include_css = {"doctype": "public/css/doctype.css"}

# include js in page
# page_js = {"page" : "public/js/file.js"}

# include js in doctype views
# doctype_js = {"doctype" : "public/js/doctype.js"}
# doctype_list_js = {"doctype" : "public/js/doctype_list.js"}
# doctype_tree_js = {"doctype" : "public/js/doctype_tree.js"}
# doctype_calendar_js = {"doctype" : "public/js/doctype_calendar.js"}

# Svg Icons
# ------------------
# include app icons in desk
# app_include_icons = "nexus_supply_chain/public/icons.svg"

# Home Pages
# ----------

# application home page (will override Website Settings)
# home_page = "login"

# website user home page (by Role)
# role_home_page = {
# 	"Role": "home_page"
# }

# Generators
# ----------

# automatically create page for each record of this doctype
# website_generators = ["Web Page"]

# Jinja
# ----------

# add methods and filters to jinja environment
# jinja = {
# 	"methods": "nexus_supply_chain.utils.jinja_methods",
# 	"filters": "nexus_supply_chain.utils.jinja_filters"
# }

# Installation
# ------------

# before_install = "nexus_supply_chain.install.before_install"
# after_install = "nexus_supply_chain.install.after_install"

# Uninstallation
# ------------

# before_uninstall = "nexus_supply_chain.uninstall.before_uninstall"
# after_uninstall = "nexus_supply_chain.uninstall.after_uninstall"

# Integration Setup
# ------------------
# To set up dependencies/integrations with other apps
# Name of the app being installed is passed as an argument

# before_app_install = "nexus_supply_chain.utils.before_app_install"
# after_app_install = "nexus_supply_chain.utils.after_app_install"

# Integration Cleanup
# -------------------
# To clean up dependencies/integrations with other apps
# Name of the app being uninstalled is passed as an argument

# before_app_uninstall = "nexus_supply_chain.utils.before_app_uninstall"
# after_app_uninstall = "nexus_supply_chain.utils.after_app_uninstall"

# Desk Notifications
# ------------------
# See frappe.core.notifications.get_notification_config

# notification_config = "nexus_supply_chain.notifications.get_notification_config"

# Permissions
# -----------
# Permissions evaluated in scripted ways

# permission_query_conditions = {
# 	"Event": "frappe.desk.doctype.event.event.get_permission_query_conditions",
# }
#
# has_permission = {
# 	"Event": "frappe.desk.doctype.event.event.has_permission",
# }

# DocType Class
# ---------------
# Override standard doctype classes

# override_doctype_class = {
# 	"ToDo": "custom_app.overrides.CustomToDo"
# }

# Document Events
# ---------------
# Hook on document methods and events

# doc_events = {
# 	"*": {
# 		"on_update": "method",
# 		"on_cancel": "method",
# 		"on_trash": "method"
# 	}
# }

# Scheduled Tasks
# ---------------

# scheduler_events = {
# 	"all": [
# 		"nexus_supply_chain.tasks.all"
# 	],
# 	"daily": [
# 		"nexus_supply_chain.tasks.daily"
# 	],
# 	"hourly": [
# 		"nexus_supply_chain.tasks.hourly"
# 	],
# 	"weekly": [
# 		"nexus_supply_chain.tasks.weekly"
# 	],
# 	"monthly": [
# 		"nexus_supply_chain.tasks.monthly"
# 	],
# }

# Testing
# -------

# before_tests = "nexus_supply_chain.install.before_tests"

# Overriding Methods
# ------------------------------
#
# override_whitelisted_methods = {
# 	"frappe.desk.doctype.event.event.get_events": "nexus_supply_chain.event.get_events"
# }
#
# each overriding function accepts a `data` argument;
# generated from the base implementation of the doctype dashboard,
# along with any modifications made in other Frappe apps
# override_doctype_dashboards = {
# 	"Task": "nexus_supply_chain.task.get_dashboard_data"
# }

# exempt linked doctypes from being automatically cancelled
#
# auto_cancel_exempted_doctypes = ["Auto Repeat"]

# Ignore links to specified DocTypes when deleting documents
# -----------------------------------------------------------

# ignore_links_on_delete = ["Communication", "ToDo"]

# Request Events
# ----------------
# before_request = ["nexus_supply_chain.utils.before_request"]
# after_request = ["nexus_supply_chain.utils.after_request"]

# Job Events
# ----------
# before_job = ["nexus_supply_chain.utils.before_job"]
# after_job = ["nexus_supply_chain.utils.after_job"]

# User Data Protection
# --------------------

# user_data_fields = [
# 	{
# 		"doctype": "{doctype_1}",
# 		"filter_by": "{filter_by}",
# 		"redact_fields": ["{field_1}", "{field_2}"],
# 		"partial": 1,
# 	},
# 	{
# 		"doctype": "{doctype_2}",
# 		"filter_by": "{filter_by}",
# 		"partial": 1,
# 	},
# 	{
# 		"doctype": "{doctype_3}",
# 		"strict": False,
# 	},
# 	{
# 		"doctype": "{doctype_4}"
# 	}
# ]

# Authentication and authorization
# --------------------------------

# auth_hooks = [
# 	"nexus_supply_chain.auth.validate"
# ]

# Automatically update python controller files with type annotations for this app.
# export_python_type_annotations = True

# default_log_clearing_doctypes = {
# 	"Logging DocType Name": 30  # days to retain logs
# }

# Translation
# ------------
# List of apps whose translatable strings should be excluded from this app's translations.
# ignore_translatable_strings_from = []