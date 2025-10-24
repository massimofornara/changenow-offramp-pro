# np_jwt_cmd.sh
#!/data/data/com.termux/files/usr/bin/bash
set -euo pipefail
curl -s -X POST "https://api.nowpayments.io/v1/auth" \
  -H "Content-Type: application/json" \
  -d '{"email":"mfornara93@gmail.com","password":"AbilitaPagamenti26$"}' | jq
echo "$JWT"
