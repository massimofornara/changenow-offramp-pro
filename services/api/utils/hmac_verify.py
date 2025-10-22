import hmac, hashlib

def verify_hmac_sha256(payload_raw: bytes, signature: str, secret: str) -> bool:
    if not signature or not secret:
        return False
    mac = hmac.new(secret.encode('utf-8'), msg=payload_raw, digestmod=hashlib.sha256)
    expected = mac.hexdigest()
    return hmac.compare_digest(expected, signature)
