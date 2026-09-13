#!/bin/sh
set -eu
mkdir -p /data /config /config/www/mutual_fund_tracker
cp -f /app/www/mutual-fund-tracker-card.js /config/www/mutual_fund_tracker/mutual-fund-tracker-card.js
exec python3 /app/app.py
