#!/usr/bin/env bash
# Prepara l'add-on in dist/evershopper: la cartella homeassistant/ più il codice, versione = data.
# Poi si copia quella cartella in `addons` di Home Assistant (condivisione Samba) e si installa da "Add-on locali".
set -euo pipefail
cd "$(dirname "$0")/.."
OUT=dist/evershopper
rm -rf "$OUT"
mkdir -p "$OUT/app"
cp homeassistant/* "$OUT/"
# solo il codice: mai config.yaml, .state, cache, logs o catture (dati personali)
cp -R evershopper config.example.yaml README.md "$OUT/app/"
find "$OUT/app" -name __pycache__ -type d -prune -exec rm -rf {} +
VERSION="$(date +%Y.%m.%d.%H%M)"
sed -i.bak "s/^version: .*/version: \"$VERSION\"/" "$OUT/config.yaml" && rm "$OUT/config.yaml.bak"
echo "Add-on pronto in $OUT (versione $VERSION)"
