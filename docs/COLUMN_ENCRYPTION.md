# Column encryption (SEC-03)

**Status:** landed 2026-10-23, uncommitted pending owner review. Encryption is
**off** until `SOC_FIELD_KEY` is set — every existing row, dump, test and clone
keeps working exactly as before.

## What is encrypted

| Table.column | Why |
|---|---|
| `auth_sessions.csrf_token` | The only *plaintext* secret the platform stores. The session cookie itself is stored as a SHA-256 hash, but the CSRF token is compared verbatim every mutation, so it was plaintext on disk and in every dump. |
| `code_reviews.code_snippet` | Pasted source code — routinely the most sensitive free text in the database (proprietary code, sometimes credentials inside it). |

### What is deliberately **not** encrypted

| Column | Reason |
|---|---|
| `users.password_hash` | Already one-way (scrypt, PBKDF2 fallback) **and** `UNIQUE`. Encrypting it breaks equality lookup on login and buys nothing an attacker with the key could not also read. |
| `auth_sessions.token_hash` | Already SHA-256, `UNIQUE`, looked up by value on every request. |
| `splunk_events.host`, `.source`, `.timestamp`, status/severity columns | Filtered, sorted and grouped in SQL. Ciphertext cannot be indexed or `LIKE`-matched; encrypting them would mean decrypting the whole table in Python per query. |
| `investigation_states.payload`, `closure_notes.*` | Contain case evidence, but are also read/merged by `services/` code that compares structure. Left plaintext by decision (2026-10-23) to keep the blast radius of this change small; revisit if the threat model changes. |

## Design

`db/crypto.py` provides an `EncryptedText` SQLAlchemy `TypeDecorator`
(`impl = Text`). Callers set and get plaintext; the decorator encrypts on bind
and decrypts on result. Two properties make the rollout safe:

* **Unset key = pass-through.** No key configured means the column stores
  plaintext — the pre-SEC-03 behavior. Nothing breaks, nothing silently
  half-encrypts.
* **Legacy values are readable.** Anything without the `enc:v1:` prefix is
  returned as-is, so setting the key on an existing database does not strand
  old rows; `--backfill` converts them when convenient.

Ciphertext format: `enc:v1:<base64(nonce || ciphertext || tag)>`, AES-256-GCM,
fresh 12-byte nonce per write (identical plaintexts produce different
ciphertexts). A wrong key or tampered value raises `FieldKeyError` — never an
empty string, because a silently blank CSRF token or code review is worse than
an error.

## Key management

* `SOC_FIELD_KEY` — 32 bytes, **64 hex chars or base64**. Generate with
  `python3 -c "import secrets;print(secrets.token_hex(32))"`.
* It is a credential: store it in the macOS keychain / BWS, never in `.env`,
  never in chat or a commit. `scripts/secrets-keychain put SOC_FIELD_KEY` then
  `scripts/dev` / `scripts/start` inject it (`SOC_FIELD_KEY` is now part of the
  keychain key set).
* A malformed key fails loudly at first use (`FieldKeyError`) rather than
  degrading to plaintext.
* Losing the key loses the encrypted columns. It belongs in the backup/restore
  plan next to `DATABASE_URL`, in a different place from the dumps: a dump plus
  its key on the same disk is not encryption.

## Rollout (existing Postgres database)

1. **Widen the column first.** `auth_sessions.csrf_token` was `varchar(64)`; a
   43-char token encrypts to ~104 chars. On a fresh database `db/schema.py`
   creates `TEXT` and this step is unnecessary.

   ```sql
   ALTER TABLE auth_sessions ALTER COLUMN csrf_token TYPE varchar(255);
   ALTER TABLE code_reviews  ALTER COLUMN code_snippet TYPE text;  -- already text
   ```

2. **Set the key** (keychain/BWS) and restart the API.
3. **Backfill** the existing plaintext rows:

   ```bash
   scripts/rotate_field_key --check       # counts per column, exit 1 if action needed
   scripts/rotate_field_key --backfill    # encrypt plaintext rows under the current key
   scripts/rotate_field_key --check       # expect: N rows, N encrypted, 0 plaintext
   ```

4. `--check` exits 1 when plaintext rows remain, when ciphertext exists with no
   key, or when the configured key does not decrypt the data — so it can gate a
   launch script. It also prints the `ALTER` when it detects a narrow column.

## Rotation

```bash
SOC_FIELD_KEY_OLD=<current> SOC_FIELD_KEY=<new> scripts/rotate_field_key --rotate
```

`--rotate` decrypts with the old key and re-encrypts with the new one in a
single transaction: a wrong old key aborts with `FieldKeyError` and **nothing is
written** (verified: after a failed rotate the data still decrypts under the old
key). It refuses to write to anything that is not this machine — `SOC_DB_TARGET=shared`,
or an explicit `DATABASE_URL` whose host is not loopback — unless `--allow-shared`
is passed, because a key rotation against the Neon mirror is a production
operation. Nothing printed ever includes a value, a key, or a credential.

## Backups

A dump taken with encryption on contains ciphertext for these two columns and
is unreadable without the key. That is the point. Consequences:

* Restore drills must have `SOC_FIELD_KEY` available, or the two columns come
  back unreadable (`--check` reports it).
* `scripts/mirror_db pull` copies ciphertext as-is; the mirror is only useful on
  a machine with the same key.
* If the key is rotated *after* a dump was taken, that dump needs the old key to
  read those columns. Keep retired keys for as long as you keep dumps.

## Honest limitations

* Protects data **at rest** only — a stolen disk, a copied dump, a backup on a
  laptop. It does not defend against SQL injection, an API bug, or anyone
  holding the key (including the API process itself).
* The two encrypted columns cannot be indexed, searched, or compared in SQL.
  That is why the list is short.
* `code_reviews.code_snippet` grows ~1.4× on disk (base64 + GCM tag).
* The key is symmetric and single-tenant: no per-user keys, no envelope
  encryption, no KMS. Adequate for a single-operator platform; a multi-tenant
  deployment would want envelope encryption with a KMS-held data key.

## Tests

`tests/test_column_encryption.py` (14 tests) covers the crypto contract (hex and
base64 keys, malformed key, pass-through, round trip, nonce freshness,
idempotence, wrong key, missing key, ciphertext length), the ORM/decorator
behavior at rest (plaintext with no key, ciphertext with a key, legacy rows
readable, `code_snippet`), and the integration case that matters most: **login →
`/api/auth/session` → a CSRF-gated mutation still works with encryption on**,
including the negative control that the same mutation 403s without the header.

Negative control: replacing `process_bind_param` with a pass-through makes the
two at-rest tests fail; restoring it returns the file to 14/14.
