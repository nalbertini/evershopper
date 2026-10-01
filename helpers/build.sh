#!/bin/sh
# Compila l'helper Swift per Promemoria (solo macOS) in due forme:
#   bin/reminders-helper              eseguibile da terminale
#   bin/EvershopperReminders.app      lo stesso eseguibile dentro un'app invisibile
#
# Lanciata con `open`, l'app chiede il permesso a Promemoria a nome proprio,
# qualunque sia il terminale (iTerm, Warp, VS Code… spesso vengono rifiutati
# senza finestra) e anche da launchd. La firma ad-hoc con identificativo fisso
# fa ricordare il permesso tra una compilazione e l'altra.
set -eu
cd "$(dirname "$0")/.."
mkdir -p bin
swiftc -O -swift-version 5 helpers/reminders.swift -o bin/reminders-helper \
    -Xlinker -sectcreate -Xlinker __TEXT -Xlinker __info_plist -Xlinker helpers/Info.plist
codesign --force --sign - --identifier it.evershopper.reminders-helper bin/reminders-helper

APP=bin/EvershopperReminders.app
rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS"
cp helpers/Info.plist "$APP/Contents/Info.plist"
cp bin/reminders-helper "$APP/Contents/MacOS/reminders-helper"
codesign --force --sign - "$APP"

echo "Compilati bin/reminders-helper e $APP"
echo "Prova: python -m evershopper reminders --lists   (la prima volta macOS chiede il permesso)"
