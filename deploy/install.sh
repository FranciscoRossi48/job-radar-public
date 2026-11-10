#!/bin/sh
# Instala (o reinstala) las corridas de job-radar en launchd:
#   - com.jobradar.daily    corrida completa (ingest + evaluate + export), una vez por día
#                           desde HORA:MINUTO, apenas la Mac esté despierta (reintenta cada 30 min)
#   - com.jobradar.catchup  rescate: evalúa lo que quedó pendiente (ej. Gemini saturado)
# Uso: deploy/install.sh [HORA] [MINUTO] [HORA_RESCATE] [MINUTO_RESCATE]
#      default: 7 30 13 0 (hora local de la Mac)
# Ojo: reinstalar corta una corrida que esté en curso (launchctl bootout).
set -eu
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
UV="$(command -v uv || { [ -x "$HOME/.local/bin/uv" ] && echo "$HOME/.local/bin/uv"; } || echo /opt/homebrew/bin/uv)"
[ -x "$UV" ] || { echo "No encuentro uv. Instalalo con: brew install uv"; exit 1; }
mkdir -p "$ROOT/logs" "$HOME/Library/LaunchAgents"

# Escapa caracteres especiales de sed en la ruta (por ej. espacios, & o |).
esc() { printf '%s' "$1" | sed -e 's/[&|\\]/\\&/g'; }

install_one() {  # LABEL HORA MINUTO
  label="$1"; dest="$HOME/Library/LaunchAgents/$label.plist"
  sed -e "s|__UV__|$(esc "$UV")|g" -e "s|__ROOT__|$(esc "$ROOT")|g" \
      -e "s|__HOUR__|$2|g" -e "s|__MINUTE__|$3|g" \
      -e "s|__NOT_BEFORE__|$(printf '%02d:%02d' "$2" "$3")|g" \
      "$ROOT/deploy/$label.plist.template" > "$dest"
  plutil -lint "$dest" >/dev/null
  launchctl bootout "gui/$(id -u)/$label" 2>/dev/null || true
  launchctl bootstrap "gui/$(id -u)" "$dest"
}

install_one com.jobradar.daily "${1:-7}" "${2:-30}"
printf 'Instalado com.jobradar.daily: una vez por día desde las %02d:%02d (o al despertar)\n' \
  "${1:-7}" "${2:-30}"
install_one com.jobradar.catchup "${3:-13}" "${4:-0}"
printf 'Instalado com.jobradar.catchup: todos los días a las %02d:%02d\n' "${3:-13}" "${4:-0}"
echo "Logs: $ROOT/logs/"
echo "Probar ahora:  launchctl kickstart gui/$(id -u)/com.jobradar.daily"
