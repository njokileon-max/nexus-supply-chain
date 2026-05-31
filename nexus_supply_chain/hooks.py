# hooks.py

app_name = "nexus_supply_chain"
app_title = "Nexus Supply Chain"
app_publisher = "leon"
app_description = "Nexus Supply Chain"
app_email = "gnleon29@gmail.com"
app_license = ""


# apps/nexus_supply_chain/nexus_supply_chain/hooks.py

# Document events - Mapping your Python logic to Frappe triggers
# apps/nexus_supply_chain/nexus_supply_chain/hooks.py

# apps/nexus_supply_chain/nexus_supply_chain/hooks.py

# apps/nexus_supply_chain/nexus_supply_chain/hooks.py

doc_events = {
    "Customer": {
        "before_save": [
            "nexus_supply_chain.utils.geocoding.queue_customer_geocoding",
            "nexus_supply_chain.api.trigger_cache_eviction_and_notify"
        ]
    },
    
    # 🚨 UNIFIED CATALOG TRIGGERS
    # Routes to the centralized Redis cache eviction engine.
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
    
    # 🚨 STOCK MOVEMENT TRIGGERS
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
        # 🚨 LIVE LIFECYCLE TRACKING + CACHE EVICTION
        "on_update": [
            "nexus_supply_chain.api.trigger_order_status_update",
            "nexus_supply_chain.api.trigger_cache_eviction_and_notify"
        ],
        "on_cancel": [
            "nexus_supply_chain.reservation_hooks.process_sales_order_update",
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

