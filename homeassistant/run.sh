#!/usr/bin/with-contenv bashio
# Avvio dell'add-on: segreti dalle opzioni (variabili d'ambiente, mai su file), config.yaml e cache in /share,
# pagina sulla porta 8791 (solo Ingress) e `run` all'orario di config.yaml. Se si ferma, riparte dopo 30 secondi.

set -u
export EVERSHOPPER_HOME=/share/evershopper
mkdir -p "$EVERSHOPPER_HOME"

# opzioni facoltative: vuote nella UI, bashio::config darebbe la stringa "null"
opt() { if bashio::config.has_value "$1"; then bashio::config "$1"; fi; }
export EVERSHOPPER_SESSION="$(opt everli_session)"
export EVERSHOPPER_NOTIFY="$(opt notify_service)"
export ANTHROPIC_API_KEY="$(opt anthropic_api_key)"
[ -n "$EVERSHOPPER_SESSION" ] || unset EVERSHOPPER_SESSION
[ -n "$ANTHROPIC_API_KEY" ] || unset ANTHROPIC_API_KEY

if [ ! -f "$EVERSHOPPER_HOME/config.yaml" ]; then
  bashio::log.fatal "Manca $EVERSHOPPER_HOME/config.yaml: copia quello del Mac nella cartella share/evershopper (Samba)."
  exit 1
fi
if [ -z "${EVERSHOPPER_SESSION:-}" ]; then
  bashio::log.warning "everli_session vuota: sul Mac lancia \`python -m evershopper export-session\` e incollala nelle opzioni"
fi

bashio::log.info "config e cache in $EVERSHOPPER_HOME · pagina nella barra laterale di Home Assistant (\"Spesa\")"
cd /app
while true; do
  .venv/bin/python -m evershopper serve
  bashio::log.warning "l'add-on si è fermato (codice $?): riparto tra 30 secondi"
  sleep 30
done
