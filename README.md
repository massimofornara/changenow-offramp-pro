# ChangeNOW Offramp PRO (API + SEPA via NOWPayments)

Backend FastAPI pronto per Render che:
- legge/gestisce **OTC listing** (es. `$NENO` con prezzo manuale in EUR),
- genera **link SELL ChangeNOW** (widget pubblico),
- espone API **server-side** per orchestrare un **off‑ramp fiat (SEPA)** via **NOWPayments**,
- mantiene **ordini** e **stati** in Postgres (Render External DB),
- espone **webhook** per aggiornare lo stato dei payout.

> ⚠️ Importante: questo progetto è un **template tecnico**. Per andare in produzione devi:
> - avere contratti/accordi attivi con ChangeNOW/NOWPayments e API key **live**,
> - rispettare KYC/AML/PCI/GDPR, limiti e compliance del provider,
> - mappare esattamente i campi richiesti dalle loro API (IBAN, BIC, dati beneficiario ecc).

---

## 🚀 Deploy rapido su Render

1. **Fork/Upload** questa repo.
2. Su Render → **New + Web Service** → collega la repo.
3. `Environment` = **Python**  
   Build: `pip install -r services/api/requirements.txt`  
   Start: `uvicorn services.api.main:app --host 0.0.0.0 --port $PORT`
4. Configura le **Environment Variables** (vedi `.env.example`).
5. **Deploy**.

---

## 🗄️ Database

Alla partenza, il servizio crea automaticamente:
- tabella `otc_listings` (seed con default da env),
- tabella `orders`.

### Test rapido (listing OTC)
```
curl https://<your-service>.onrender.com/otc/listings
curl -X POST https://<your-service>.onrender.com/otc/set-price \
  -H "Content-Type: application/json" \
  -d '{"token_symbol":"NENO","price_eur":5000,"available_amount":1000000}'
```

---

## 🔗 Widget SELL (ChangeNOW) — semplice

Genera un link alla pagina **SELL** di ChangeNOW con parametri precompilati:

```
curl "https://<your-service>.onrender.com/changenow/widget-sell-eur?amount=100000&from_symbol=usdt&redirect_url=https://blkpanthcoin.world"
# => { "url": "https://changenow.io/sell?from=usdt&to=eur&amount=100000&ref_id=YOUR_REF&redirect_url=..." }
```

Apri l’URL nel browser per avviare il flusso SELL (KYC + payout).  
Puoi anche incorporarlo in un `<iframe>` nel tuo frontend.

---

## 🧩 Flusso PRO (server-side)

### 1) Crea ordine OTC (NENO → EUR)
L’ordine calcola `amount_eur = amount_tokens * price_eur` usando il tuo listing manuale.

```
curl -X POST https://<your-service>.onrender.com/offramp/create-order \
  -H "Content-Type: application/json" \
  -d '{
        "token_symbol": "NENO",
        "amount_tokens": 1000,
        "iban": "IT60X0542811101000000123456",
        "beneficiary_name": "MASSIMO FORNARA",
        "redirect_url": "https://blkpanthcoin.world"
      }'
# ↩️ { "order_id": "...", "status": "quoted", "amount_eur": 5000000.0, "changenow_payment_url": "https://changenow.io/sell?..."}
```

> Nota: Il link SELL è lato-utente. Per automazione completa con bonifico server-side, usa lo step 2 dopo la conferma (manuale o webhook) che i fondi fiat sono pronti.

### 2) Trigger payout SEPA (server → NOWPayments)

Quando ChangeNOW completa la conversione e i fondi EUR sono disponibili per l’invio al beneficiario:

```
curl -X POST https://<your-service>.onrender.com/offramp/trigger-payout/{order_id}
# ↩️ { "ok": true, "order_id": "...", "payout_id": "np_...", "status": "payout_pending" }
```

### 3) Webhook NOWPayments (IPN)

Configura su NOWPayments il webhook:  
`https://<your-service>.onrender.com/offramp/webhooks/nowpayments`

Il backend verifica la **firma HMAC-SHA256** (`x-nowpayments-sig`) con `NOWPAYMENTS_IPN_SECRET` e aggiorna lo stato ordine:
- `completed` (successo),
- `failed` / `payout_pending` a seconda dell’evento.

### 4) Consultare ordini
```
curl https://<your-service>.onrender.com/offramp/sales
curl https://<your-service>.onrender.com/offramp/sales/{order_id}
```

---

## 🔐 Sicurezza & Compliance (checklist)

- **KYC/AML**: integra gli esiti KYC del provider (ChangeNOW) prima di creare payout.
- **HMAC Webhooks**: usa segreti robusti e valida le firme.
- **Rate limiting & RBAC**: aggiungi auth (API key/Bearer) agli endpoint di creazione ordine e payout.
- **PCI/GDPR**: non loggare dati sensibili (IBAN completo, PII non necessari).
- **Idempotency**: aggiungi header `Idempotency-Key` per evitare doppi payout.
- **Reconcile**: registra `changenow_tx_id` e `nowpayments_payout_id` per audit.

---

## 🔧 Estensioni (TODO)

- Integrazione DEX per swap automatico **NENO → USDT** on‑chain prima del SELL.
- Endpoint webhook ChangeNOW per auto‑trigger del payout.
- Scheduler per retry su `payout_pending`.
- Dashboard admin (RBAC, 2FA) per gestione ordini.

---

## 📦 Struttura

```
changenow-offramp-pro/
├─ render.yaml
├─ .env.example
├─ README.md
└─ services/
   └─ api/
      ├─ Dockerfile
      ├─ requirements.txt
      ├─ services/
      │  ├─ __init__.py
      │  ├─ changenow.py
      │  └─ nowpayments.py
      ├─ routers/
      │  ├─ __init__.py
      │  ├─ otc.py
      │  ├─ offramp.py
      │  └─ changenow_widget.py
      ├─ utils/
      │  ├─ __init__.py
      │  └─ hmac_verify.py
      ├─ config.py
      ├─ db.py
      ├─ schemas.py
      └─ main.py
```

---

## ✅ Note finali

- Questo template usa API generiche per ChangeNOW/NOWPayments; **verifica nei documenti ufficiali** i campi esatti per `transactions` (SELL) e `payout`.  
- Se `$NENO` non è listato su ChangeNOW, effettua prima uno **swap verso USDT** (on‑chain/DEX) e poi usa `from=usdt → to=eur`.

Buon lavoro! 🚀
# changenow-offramp-pro-
# changenow-offramp-pro-
