DOMAIN = "mutual_fund_tracker"
STATE_FILES = ("/share/mutual_fund_tracker/integration_state.json", "/config/mutual_fund_tracker/integration_state.json")
UPDATE_INTERVAL_SECONDS = 30
STATE_VERSION = 3
REFRESH_REQUEST_FILE = "/share/mutual_fund_tracker/refresh.request"
EXPORT_REQUEST_FILE = "/share/mutual_fund_tracker/export.request"
INTEGRATION_RELOAD_ACK_FILE = "/share/mutual_fund_tracker/integration_reload.ack"
EXPORT_SHARE_DIR = "/share/mutual_fund_tracker/exports"
# Exports live in the config directory, NOT under /config/www (which HA serves
# unauthenticated at /local/). They are only reachable through ExportView.
EXPORT_SUBDIR = ("mutual_fund_tracker", "exports")
LEGACY_WWW_SUBDIR = ("www", "mutual_fund_tracker")
EXPORT_URL_BASE = "/api/mutual_fund_tracker/export"
EXPORT_EVENT = "mutual_fund_tracker_export_ready"
EXPORT_SIGNED_URL_TTL_SECONDS = 3600
EXPORT_RETENTION_SECONDS = 24 * 3600
CARD_URL = "/api/mutual_fund_tracker/mutual-fund-tracker-card.js"
