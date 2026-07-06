"""Password hashing and session-token helpers for the AXIM auth system.

Pure logic, no DB access (core/database.py owns persistence, same split
as risk_manager.py/database.py elsewhere in this codebase). Uses stdlib
hashlib.pbkdf2_hmac rather than adding a bcrypt/argon2 dependency - one
fewer native-build dependency on Windows, and PBKDF2-HMAC-SHA256 with a
high iteration count is still a widely-accepted password hash.
"""
import hashlib
import hmac
import os
import secrets

_PBKDF2_ITERATIONS = 600_000
_SALT_BYTES = 16


def hash_password(password):
    salt = secrets.token_bytes(_SALT_BYTES)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, _PBKDF2_ITERATIONS)
    return f"pbkdf2_sha256${_PBKDF2_ITERATIONS}${salt.hex()}${digest.hex()}"


def verify_password(password, stored_hash):
    try:
        scheme, iterations, salt_hex, digest_hex = stored_hash.split("$")
        if scheme != "pbkdf2_sha256":
            return False
        salt = bytes.fromhex(salt_hex)
        expected = bytes.fromhex(digest_hex)
    except (ValueError, AttributeError):
        return False
    actual = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, int(iterations))
    return hmac.compare_digest(actual, expected)


def generate_session_token():
    """Returns (raw_token, token_hash) - the raw token goes in the cookie
    sent to the browser, only the hash is ever persisted, same reasoning
    as a password hash: a DB read alone can't be replayed as a valid
    session."""
    raw = secrets.token_urlsafe(32)
    token_hash = hashlib.sha256(raw.encode("utf-8")).hexdigest()
    return raw, token_hash


def hash_token(raw_token):
    return hashlib.sha256(raw_token.encode("utf-8")).hexdigest()
