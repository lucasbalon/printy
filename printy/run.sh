#!/usr/bin/with-contenv bashio
# shellcheck shell=bash
set -e

export PRINTY_LOG_LEVEL="$(bashio::config 'log_level')"
bashio::log.info "Démarrage de Printy"

cd /app
# No --proxy-headers: the Ingress check relies on the real client address.
exec python3 -m uvicorn printy.main:app --host 0.0.0.0 --port 8099 --log-level warning
