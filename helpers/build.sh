#!/bin/sh
# Compila l'helper Swift per Promemoria in bin/reminders-helper (solo macOS).
# L'Info.plist incorporato serve a macOS per mostrare la richiesta di accesso;
# la firma ad-hoc con identificativo fisso fa ricordare il permesso tra una
# compilazione e l'altra.
set -eu
cd "$(dirname "$0")/.."
mkdir -p bin
swiftc -O -swift-version 5 helpers/reminders.swift -o bin/reminders-helper \
    -Xlinker -sectcreate -Xlinker __TEXT -Xlinker __info_plist -Xlinker helpers/Info.plist
codesign --force --sign - --identifier it.evershopper.reminders-helper bin/reminders-helper
echo "Compilato bin/reminders-helper"
echo "Prova: bin/reminders-helper --lists   (la prima volta macOS chiede il permesso)"
