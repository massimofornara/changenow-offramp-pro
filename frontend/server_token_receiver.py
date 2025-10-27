# server_token_receiver.py (Flask minimal)
from flask import Flask, request, jsonify
app = Flask(__name__)

@app.route("/card-token", methods=["POST"])
def card_token():
    j = request.get_json(force=True)
    token = j.get("token")
    name = j.get("name")
    # qui puoi creare l'external account o memorizzare il token associato a un ordine
    # Esempio di risposta:
    return jsonify({"ok": True, "received_token": token, "cardholder": name})

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
