#!/bin/sh
# Desinstala las corridas de job-radar de launchd (no toca la base ni los logs).
set -u
for LABEL in com.jobradar.daily com.jobradar.catchup; do
  DEST="$HOME/Library/LaunchAgents/$LABEL.plist"
  launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null && echo "Descargado $LABEL" || echo "$LABEL no estaba cargado"
  rm -f "$DEST"
done
