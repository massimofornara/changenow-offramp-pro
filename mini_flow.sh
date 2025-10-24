#!/usr/bin/env bash
# ======================================================
# MINI-FLOW: OTC reale + payout automatico via NOWPayments
# Backend: https://changenow-offramp-pro.onrender.com
# Requisiti locali: bash, curl, jq
# ======================================================

set -euo pipefail

# ---------- CONFIG MODIFICA QUI ----------
SERVICE="https://changenow-offramp-pro.onrender.com"

# Prodotto OTC
TOKEN_SYMBOL="NENO"
PRICE_EUR=5000          # prezzo per 1 NENO (EUR)
AVAILABLE=1000000       # quantità totale vendibile

# Ordine da creare
TOKENS_TO_SELL=1000     # es. 1000 NENO -> 5.000.000 EUR
BENEFICIARY="MASSIMO FORNARA"
IBAN="IT22B0200822800000103317304"
REDIRECT_URL="https://blkpanthcoin.world"  # dove rimandare l’utente post pagamento

# Polling stato
POLL_MAX=60             # numero tentativi
POLL_SLEEP=10           # secondi tra un tentativo e l’altro

# ---------- HELPERS ----------
j() { jq -r "${1}" 2>/dev/null || true; }
log() { echo -e "\n==== $* ===="; }
api() {
  local method="$1"; shift
  local url="$1"; shift
  local body="${1:-}"
  if [[ -n "$body" ]]; then
    curl -sS -X "$method" "$url" -H "Content-Type: application/json" -d "$body"
  else
    curl -sS -X "$method" "$url"
  fi
}

# ---------- FLOW ----------
echo "🚀 Avvio Mini-flow OTC reale + NOWPayments"

# 0) Health
log "0) Health check"
api GET "$SERVICE/" | jq .

# 1) Crea/Aggiorna listing OTC
log "1) Imposta listing $TOKEN_SYMBOL"
LISTING_PAYLOAD="$(jq -nc \
  --arg t "$TOKEN_SYMBOL" \
  --argjson p "$PRICE_EUR" \
  --argjson a "$AVAILABLE" \
  '{token_symbol:$t, price_eur:$p, available_amount:$a}')"
api POST "$SERVICE/otc/set-price" "$LISTING_PAYLOAD" | jq .

# 1b) Verifica listing
log "1b) Listing correnti"
api GET "$SERVICE/otc/listings" | jq .

# 2) Crea ordine OTC REALE (verrà usato per scatenare il payout bancario)
log "2) Crea ordine OTC"
CREATE_PAYLOAD="$(jq -nc \
  --arg t  "$TOKEN_SYMBOL" \
  --argjson amt "$TOKENS_TO_SELL" \
  --arg iban "$IBAN" \
  --arg bn  "$BENEFICIARY" \
  --arg ru  "$REDIRECT_URL" \
  '{token_symbol:$t, amount_tokens:$amt, iban:$iban, beneficiary_name:$bn, redirect_url:$ru}')"

CREATE_RES="$(api POST "$SERVICE/offramp/create-order" "$CREATE_PAYLOAD")"
echo "$CREATE_RES" | jq .

ORDER_ID="$(echo "$CREATE_RES" | j '.order_id')"
EUR_AMOUNT="$(echo "$CREATE_RES" | j '.eur_amount')"

if [[ -z "${ORDER_ID}" || "${ORDER_ID}" == "null" ]]; then
  echo "❌ Creazione ordine fallita"; exit 1
fi
echo "📦 ORDER_ID: $ORDER_ID  |  💶 EUR_AMOUNT: ${EUR_AMOUNT:-?}"

# 3) Trigger payout automatico tramite NOWPayments (BACKEND usa API reali)
log "3) Trigger payout NOWPayments"
TRIGGER_RES="$(api POST "$SERVICE/offramp/trigger-payout/$ORDER_ID")"
echo "$TRIGGER_RES" | jq .

NP_PAYOUT_ID="$(echo "$TRIGGER_RES" | j '.nowpayments_payout_id')"
STATUS_AFTER_TRIGGER="$(echo "$TRIGGER_RES" | j '.status')"
echo "🏦 NOWPayments payout_id: ${NP_PAYOUT_ID:-null} | stato_interno: ${STATUS_AFTER_TRIGGER:-unknown}"

# 4) Polling finché NOWPayments notifica (IPN) e l’ordine diventa completed/failed
log "4) Poll stato ordine fino a completamento"
for ((i=1; i<=POLL_MAX; i++)); do
  RES="$(api GET "$SERVICE/offramp/sales/$ORDER_ID" || true)"
  STATUS="$(echo "$RES" | j '.status')"
  NP_PID="$(echo "$RES" | j '.nowpayments_payout_id')"
  echo "⏳ [$i/$POLL_MAX] status=$STATUS  payout_id=${NP_PID:-null}"
  echo "$RES" | jq -c .
  if [[ "$STATUS" == "completed" || "$STATUS" == "failed" || "$STATUS" == "cancelled" ]]; then
    break
  fi
  sleep "$POLL_SLEEP"
done

# 5) Esito finale
log "5) Esito finale ordine"
api GET "$SERVICE/offramp/sales/$ORDER_ID" | jq .

echo -e "\n✅ Flow terminato. Se lo stato è 'completed' il bonifico è stato accettato su NOWPayments.\n"
