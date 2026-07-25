"""
ooe/auth.py
~~~~~~~~~~~
Lightweight JWT + password-hashing helpers using Python's stdlib only.
No external dependencies — hmac / hashlib / base64 are all standard library.

JWT format is fully RFC 7519-compatible (HS256).  Any standard JWT library
can verify tokens produced here, and vice-versa, as long as the shared secret
matches.

Password hashing uses PBKDF2-HMAC-SHA256 with 260 000 iterations (NIST 2023
recommendation) and a 32-byte random salt.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import time
from typing import Any


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _b64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64url_decode(s: str) -> bytes:
    # Restore stripped padding
    padding = (4 - len(s) % 4) % 4
    return base64.urlsafe_b64decode(s + "=" * padding)


# ---------------------------------------------------------------------------
# JWT
# ---------------------------------------------------------------------------

_JWT_HEADER = _b64url_encode(
    json.dumps({"alg": "HS256", "typ": "JWT"}, separators=(",", ":")).encode()
)


def create_token(
    payload: dict[str, Any],
    secret: str,
    expires_in: int = 60 * 60,  # 1 hour (short-lived access token)
) -> str:
    """Return a signed JWT string."""
    now = int(time.time())
    full_payload = {**payload, "iat": now, "exp": now + expires_in}
    payload_b64 = _b64url_encode(
        json.dumps(full_payload, separators=(",", ":")).encode()
    )
    message = f"{_JWT_HEADER}.{payload_b64}"
    sig = hmac.new(secret.encode(), message.encode(), hashlib.sha256).digest()
    return f"{message}.{_b64url_encode(sig)}"


def verify_token(token: str, secret: str) -> dict[str, Any] | None:
    """Verify signature and expiry.  Returns payload dict or None if invalid."""
    try:
        parts = token.split(".")
        if len(parts) != 3:
            return None
        message = f"{parts[0]}.{parts[1]}"
        expected_sig = _b64url_encode(
            hmac.new(secret.encode(), message.encode(), hashlib.sha256).digest()
        )
        # Constant-time comparison prevents timing attacks
        if not hmac.compare_digest(expected_sig, parts[2]):
            return None
        payload: dict[str, Any] = json.loads(_b64url_decode(parts[1]))
        if payload.get("exp", 0) < time.time():
            return None
        return payload
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Password hashing
# ---------------------------------------------------------------------------

_PBKDF2_ITERATIONS = 260_000


def hash_password(password: str) -> str:
    """Return a storable string: '<salt_hex>:<key_hex>'."""
    salt = os.urandom(32)
    key = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, _PBKDF2_ITERATIONS)
    return f"{salt.hex()}:{key.hex()}"


def verify_password(password: str, stored_hash: str) -> bool:
    """Return True if *password* matches *stored_hash*."""
    try:
        salt_hex, key_hex = stored_hash.split(":", 1)
        salt = bytes.fromhex(salt_hex)
        expected_key = bytes.fromhex(key_hex)
        actual_key = hashlib.pbkdf2_hmac(
            "sha256", password.encode(), salt, _PBKDF2_ITERATIONS
        )
        return hmac.compare_digest(actual_key, expected_key)
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Stripe webhook signature verification (HMAC-SHA256)
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Convenience helpers
# ---------------------------------------------------------------------------

def create_access_token(
    user_id: str, secret: str, expires_in: int = 60 * 60,
    extra_claims: dict[str, Any] | None = None,
) -> str:
    """Create a short-lived JWT access token for the given user_id."""
    payload: dict[str, Any] = {"sub": user_id}
    if extra_claims:
        payload.update(extra_claims)
    return create_token(payload, secret, expires_in=expires_in)


# ---------------------------------------------------------------------------
# Refresh tokens (opaque, stored as SHA-256 hash in DB)
# ---------------------------------------------------------------------------

def create_refresh_token() -> tuple[str, str]:
    """Return (raw_token, sha256_hex_hash).
    Store only the hash — never the raw token — in the database.
    """
    raw = secrets.token_urlsafe(48)
    h = hashlib.sha256(raw.encode()).hexdigest()
    return raw, h


def hash_token(raw: str) -> str:
    """SHA-256 hex of an opaque token value."""
    return hashlib.sha256(raw.encode()).hexdigest()


# ---------------------------------------------------------------------------
# Stripe webhook signature verification (HMAC-SHA256)
# ---------------------------------------------------------------------------

def verify_stripe_webhook(
    payload: bytes,
    sig_header: str,
    secret: str,
    *,
    tolerance_seconds: int = 300,
) -> bool:
    """Verify a Stripe webhook signature header (Stripe-Signature: t=...,v1=...).

    Adds Stripe's recommended timestamp-tolerance check (default 5 minutes) to
    block replay attacks, and supports multiple v1 signatures (rotating
    secrets / multi-endpoint configs) by accepting any matching one.
    """
    try:
        timestamp = ""
        v1_sigs: list[str] = []
        for item in sig_header.split(","):
            if "=" not in item:
                continue
            k, v = item.split("=", 1)
            if k == "t":
                timestamp = v
            elif k == "v1":
                v1_sigs.append(v)

        if not timestamp or not v1_sigs:
            return False

        # Replay-attack protection
        try:
            ts_int = int(timestamp)
        except ValueError:
            return False
        if abs(int(time.time()) - ts_int) > tolerance_seconds:
            return False

        signed_payload = f"{timestamp}.".encode() + payload
        expected = hmac.new(secret.encode(), signed_payload, hashlib.sha256).hexdigest()
        return any(hmac.compare_digest(expected, sig) for sig in v1_sigs)
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Razorpay signature verification (HMAC-SHA256)
# ---------------------------------------------------------------------------

def verify_razorpay_payment(
    order_id: str,
    payment_id: str,
    signature: str,
    key_secret: str,
) -> bool:
    """Verify a Razorpay payment signature.

    Razorpay signs ``order_id + "|" + payment_id`` with the key secret.
    """
    try:
        message = f"{order_id}|{payment_id}"
        expected = hmac.new(
            key_secret.encode(), message.encode(), hashlib.sha256
        ).hexdigest()
        return hmac.compare_digest(expected, signature)
    except Exception:
        return False


def verify_razorpay_webhook(payload: bytes, signature: str, webhook_secret: str) -> bool:
    """Verify the X-Razorpay-Signature header on incoming webhooks."""
    try:
        expected = hmac.new(
            webhook_secret.encode(), payload, hashlib.sha256
        ).hexdigest()
        return hmac.compare_digest(expected, signature)
    except Exception:
        return False


# ---------------------------------------------------------------------------
# OAuth – Google
# ---------------------------------------------------------------------------

def verify_google_token(credential: str, client_id: str) -> dict[str, Any] | None:
    """
    Verify a Google Identity Services credential (ID token) by calling
    Google's tokeninfo endpoint.  Returns the payload dict on success,
    None on failure.
    """
    import urllib.request
    import urllib.parse
    try:
        url = "https://oauth2.googleapis.com/tokeninfo?" + urllib.parse.urlencode({"id_token": credential})
        with urllib.request.urlopen(url, timeout=8) as resp:
            payload = json.loads(resp.read().decode())
        # Verify audience matches our client_id
        if client_id and payload.get("aud") != client_id:
            return None
        if payload.get("email_verified") not in ("true", True):
            return None
        return payload
    except Exception:
        return None


# ---------------------------------------------------------------------------
# OAuth – Apple
# ---------------------------------------------------------------------------

def verify_apple_token(id_token: str, client_id: str) -> dict[str, Any] | None:
    """
    Verify a Sign in with Apple id_token using Apple's public JWKS.
    Returns the decoded payload dict on success, None on failure.
    Requires the `cryptography` package.
    """
    import urllib.request
    try:
        from cryptography.hazmat.primitives.asymmetric.rsa import RSAPublicNumbers
        from cryptography.hazmat.backends import default_backend
        from cryptography.hazmat.primitives.asymmetric import padding
        from cryptography.hazmat.primitives import hashes
        from cryptography.exceptions import InvalidSignature
    except ImportError:
        return None

    try:
        # Decode header to find kid
        parts = id_token.split(".")
        if len(parts) != 3:
            return None

        def _b64d(s: str) -> bytes:
            s += "=" * (-len(s) % 4)
            return base64.urlsafe_b64decode(s)

        header  = json.loads(_b64d(parts[0]))
        payload = json.loads(_b64d(parts[1]))
        kid     = header.get("kid")

        # Fetch Apple's JWKS
        with urllib.request.urlopen("https://appleid.apple.com/auth/keys", timeout=8) as resp:
            jwks = json.loads(resp.read().decode())

        key_data = next((k for k in jwks.get("keys", []) if k.get("kid") == kid), None)
        if not key_data:
            return None

        # Reconstruct RSA public key from n, e
        def _b64_to_int(s: str) -> int:
            return int.from_bytes(_b64d(s), "big")

        pub_key = RSAPublicNumbers(
            e=_b64_to_int(key_data["e"]),
            n=_b64_to_int(key_data["n"]),
        ).public_key(default_backend())

        # Verify signature
        message   = f"{parts[0]}.{parts[1]}".encode()
        signature = _b64d(parts[2])
        pub_key.verify(signature, message, padding.PKCS1v15(), hashes.SHA256())

        # Validate claims
        now = int(time.time())
        if payload.get("iss") != "https://appleid.apple.com":
            return None
        if client_id and payload.get("aud") != client_id:
            return None
        if payload.get("exp", 0) < now:
            return None

        return payload
    except Exception:
        return None


# ---------------------------------------------------------------------------
# 2FA – TOTP
# ---------------------------------------------------------------------------

def generate_totp_secret() -> str:
    """Return a new random base32 TOTP secret."""
    import pyotp
    return pyotp.random_base32()


def totp_provisioning_uri(secret: str, email: str, issuer: str = "BriefMe Pro") -> str:
    """Return the otpauth:// URI for QR code generation."""
    import pyotp
    return pyotp.TOTP(secret).provisioning_uri(name=email, issuer_name=issuer)


def verify_totp(secret: str, code: str) -> bool:
    """Verify a 6-digit TOTP code.  Allows ±1 window for clock skew."""
    import pyotp
    try:
        return pyotp.TOTP(secret).verify(code.strip(), valid_window=1)
    except Exception:
        return False


def generate_backup_codes(count: int = 8) -> list[str]:
    """Generate one-time backup codes in XXXX-XXXX format."""
    def _chunk(s: str, n: int) -> str:
        return "-".join(s[i:i+n] for i in range(0, len(s), n))
    return [_chunk(secrets.token_hex(4).upper(), 4) for _ in range(count)]
