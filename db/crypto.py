"""Column-level encryption for the approved sensitive columns (SEC-03).

Scope — approved by the owner 2026-10-23:

* ``auth_sessions.csrf_token`` — the only *plaintext* secret the platform stores
  (the session cookie itself is stored as a SHA-256 hash).
* ``code_reviews.code_snippet`` — pasted source, which is routinely the most
  sensitive text in the database (proprietary code, sometimes secrets inside it).

Deliberately **not** encrypted, with reasons:

* ``users.password_hash`` — already one-way (scrypt/PBKDF2) and ``UNIQUE``;
  encrypting it would break equality lookups and add nothing.
* ``auth_sessions.token_hash`` — already SHA-256, ``UNIQUE``, indexed by lookup.
* Every ``String``/``Text`` column that is filtered or sorted (``host``,
  ``source``, ``timestamp``, statuses) — ciphertext is not queryable.

Key management (no key material in the repo, ever):

* The key comes from ``SOC_FIELD_KEY`` — 32 bytes as 64 hex chars or base64.
  ``scripts/start``/``scripts/dev`` inject it like any other credential; a
  clone without it still runs (see below).
* **Unset key = pass-through.** The columns behave exactly as they did before
  SEC-03: plaintext in, plaintext out. That keeps every existing test, clone
  and dump readable, and makes the rollout a deployment step (set the key, run
  the backfill), not a migration that can strand a checkout.
* **Set but malformed = loud failure** at first use. A typo must never degrade
  silently to plaintext.
* Rotation and backfill: ``scripts/rotate_field_key`` (``--check``,
  ``--backfill``, ``--rotate``). It works on raw SQL rows, so it never depends
  on the running code's key.

Storage format: ``enc:v1:<base64(nonce || ciphertext || tag)>`` — AES-256-GCM
with a fresh 12-byte nonce per write (identical plaintexts produce different
ciphertexts). A stored value without the prefix is legacy/plaintext and is read
as-is, so setting the key never breaks existing rows; the backfill encrypts them
whenever the operator wants.

Honest limitations (documented in ``docs/COLUMN_ENCRYPTION.md``):

* Encryption protects data at rest *only* — a dump, a stolen disk, a backup
  copied to a laptop. It does not protect against SQL injection, an API bug, or
  anyone who has the key (the API process itself).
* Encrypted columns cannot be indexed, searched with ``LIKE``, or compared in
  SQL. That is why the column list is short and curated.
* ``code_reviews.code_snippet`` becomes ~1.4× larger on disk (base64 + tag).
"""

from __future__ import annotations

import base64
import binascii
import os

from sqlalchemy import Text
from sqlalchemy.types import TypeDecorator

PREFIX = "enc:v1:"
KEY_ENV = "SOC_FIELD_KEY"
NONCE_BYTES = 12
KEY_BYTES = 32


class FieldKeyError(ValueError):
    """The configured field key is malformed — refuse rather than store plaintext."""


def _aesgcm():
    """Import AESGCM lazily so the module imports on a key-less install."""
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    return AESGCM


def parse_key(raw: str) -> bytes:
    """Decode a 32-byte key from hex or base64 (raises FieldKeyError)."""
    value = (raw or "").strip()
    if not value:
        raise FieldKeyError("%s is empty" % KEY_ENV)
    try:
        if len(value) == 64:
            key = bytes.fromhex(value)
        else:
            key = base64.b64decode(value, validate=True)
    except (ValueError, binascii.Error) as exc:
        raise FieldKeyError(
            "%s must be 32 bytes as 64 hex chars or base64: %s" % (KEY_ENV, exc)
        ) from exc
    if len(key) != KEY_BYTES:
        raise FieldKeyError(
            "%s decoded to %d bytes, expected %d" % (KEY_ENV, len(key), KEY_BYTES)
        )
    return key


def current_key():
    """The configured key as bytes, or None when SOC_FIELD_KEY is unset."""
    raw = os.environ.get(KEY_ENV, "")
    if not raw.strip():
        return None
    return parse_key(raw)


def encryption_enabled() -> bool:
    """True when a usable key is configured (malformed keys raise instead)."""
    return current_key() is not None


def is_encrypted(value) -> bool:
    """True when a stored value carries the ciphertext prefix."""
    return isinstance(value, str) and value.startswith(PREFIX)


def encrypt_text(value: str, key: bytes = None) -> str:
    """Encrypt to the ``enc:v1:`` format. Already-encrypted input is returned as-is."""
    if value is None or is_encrypted(value):
        return value
    key = current_key() if key is None else key
    if key is None:
        return value
    aesgcm = _aesgcm()(key)
    nonce = os.urandom(NONCE_BYTES)
    sealed = aesgcm.encrypt(nonce, str(value).encode("utf-8"), None)
    return PREFIX + base64.b64encode(nonce + sealed).decode("ascii")


def decrypt_text(value: str, key: bytes = None) -> str:
    """Decrypt an ``enc:v1:`` value; plaintext (legacy) input is returned as-is.

    A wrong key or tampered ciphertext raises FieldKeyError: reporting a
    decryption failure as empty text would silently destroy a CSRF token or a
    code review without anyone noticing.
    """
    if value is None or not is_encrypted(value):
        return value
    key = current_key() if key is None else key
    if key is None:
        raise FieldKeyError(
            "value is encrypted but %s is not set — inject the key to read it" % KEY_ENV
        )
    try:
        blob = base64.b64decode(value[len(PREFIX):], validate=True)
        nonce, sealed = blob[:NONCE_BYTES], blob[NONCE_BYTES:]
        return _aesgcm()(key).decrypt(nonce, sealed, None).decode("utf-8")
    except Exception as exc:  # InvalidTag, binascii.Error, UnicodeDecodeError
        raise FieldKeyError(
            "cannot decrypt %s value (wrong key or corrupted data): %s"
            % (PREFIX.rstrip(":"), type(exc).__name__)
        ) from exc


class EncryptedText(TypeDecorator):
    """Text column whose value is AES-GCM ciphertext at rest.

    Transparent to callers: they set and get plaintext. With no key configured
    the column stores plaintext (the pre-SEC-03 behavior), which is why the
    suite and every existing row keep working; ``scripts/rotate_field_key``
    backfills plaintext rows once the key is deployed.
    """

    impl = Text
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        return encrypt_text(value)

    def process_result_value(self, value, dialect):
        if value is None:
            return None
        return decrypt_text(value)
