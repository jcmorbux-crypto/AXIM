"""Encrypted-at-rest storage for operator-entered secrets (Telegram API
credentials today). Uses a locally-generated Fernet key
(data/.secret_key, gitignored) - not derived from a password, since this
is a local single-operator tool decrypting its own config at process
startup, not protecting against someone who already has filesystem
access to the machine. Rotating the key (see rotate_key()) re-encrypts
everything so a leaked old key stops being useful.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from cryptography.fernet import Fernet, InvalidToken

KEY_FILE = Path("data/.secret_key")


def _load_or_create_key():
    KEY_FILE.parent.mkdir(exist_ok=True)
    if KEY_FILE.exists():
        return KEY_FILE.read_bytes()
    key = Fernet.generate_key()
    KEY_FILE.write_bytes(key)
    return key


def _fernet():
    return Fernet(_load_or_create_key())


def encrypt(plaintext):
    if plaintext is None:
        return None
    return _fernet().encrypt(plaintext.encode("utf-8")).decode("utf-8")


def decrypt(ciphertext):
    if ciphertext is None:
        return None
    try:
        return _fernet().decrypt(ciphertext.encode("utf-8")).decode("utf-8")
    except InvalidToken:
        return None


def mask(plaintext, keep=4):
    """For display only - never send the real value back over HTTP."""
    if not plaintext:
        return None
    if len(plaintext) <= keep:
        return "*" * len(plaintext)
    return "*" * (len(plaintext) - keep) + plaintext[-keep:]
