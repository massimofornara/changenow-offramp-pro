# np_jwt_daemon.sh
#!/data/data/com.termux/files/usr/bin/bash
set -euo pipefail

# === Configurazione ===
CMD="${CMD:-/data/data/com.termux/files/home/np_jwt_cmd.sh}"   # comando che restituisce il JWT
TOKEN_FILE="${TOKEN_FILE:-/data/data/com.termux/files/home/.now_jwt}"
LOG_FILE="${LOG_FILE:-/data/data/com.termux/files/home/.now_jwt.log}"
REFRESH_SECS="${REFRESH_SECS:-240}"  # 4 minuti

mkdir -p "$(dirname "$TOKEN_FILE")"
touch "$LOG_FILE"

log(){ printf '%s %s\n' "$(date '+%F %T')" "$*" | tee -a "$LOG_FILE" >/dev/null; }

is_jwt(){
  # Controllo “soft”: tre parti base64 separate da punti e lunghezza minima
  local t="$1"
  [[ "$t" == *.*.* ]] && [[ "${#t}" -gt 100 ]]
}

log "JWT daemon avviato. Comando: $CMD  | File token: $TOKEN_FILE  | Refresh: ${REFRESH_SECS}s"

while :; do
  if JWT="$($CMD 2>>"$LOG_FILE" | tr -d '\r\n' )"; then
    if is_jwt "$JWT"; then
      printf '%s' "$JWT" > "$TOKEN_FILE"
      chmod 600 "$TOKEN_FILE"
      log "JWT aggiornato. Bytes: ${#JWT}"
    else
      log "ERRORE: output non sembra un JWT valido. Bytes: ${#JWT}"
    fi
  else
    log "ERRORE: comando fallito ($CMD)"
  fi

  sleep "$REFRESH_SECS"
done
