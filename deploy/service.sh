#!/bin/bash
# Started by launchd (com.nightcity.bot). Do not run by hand while the service is on: two bots conflict.
# app.webapp.boot = app.main plus the Telegram Mini App (docs/WEBAPP.md); without that package the plain bot starts.
cd "$(dirname "$0")/.."
mkdir -p data
MOD=app.main
if [ -f app/webapp/boot.py ]; then MOD=app.webapp.boot; fi
exec /usr/bin/caffeinate -i .venv/bin/python -m "$MOD"
