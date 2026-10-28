# Nexus Production: planning board, settings, rules and BOM housekeeping.
#
# Batch 1 modules:
#   settings.py       read Nexus Production Settings as a plain dict
#   custom_fields.py  the custom fields this feature owns (created/updated on migrate)
#   validations.py    Item save rules and the Sales Order "Confirmed On" stamp
#   bom_sync.py       keeps parent BOMs pointing at each item's current default BOM
