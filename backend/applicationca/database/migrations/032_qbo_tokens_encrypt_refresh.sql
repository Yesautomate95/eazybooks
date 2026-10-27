-- Migration 032: QBO tokens — encrypt refresh_token at rest, stop persisting access_token.
--
-- access_token now lives only in an in-process memory cache
-- (applicationca/services/qbo_connector.py — _access_token_cache), never
-- written here, satisfying Intuit's "stores access tokens in volatile memory
-- only" requirement. refresh_token is now encrypted at the application layer
-- (Fernet, QBO_TOKEN_ENCRYPTION_KEY) before being written, satisfying
-- "encrypts access tokens before storing them".
--
-- Existing rows have a PLAINTEXT refresh_token from before this change —
-- load_tokens() can't decrypt those, so it treats them as disconnected and
-- the user reconnects once; the new connection is stored encrypted. We
-- proactively blank the now-unused access_token column for hygiene (it only
-- ever held short-lived, by-now-expired plaintext tokens).

ALTER TABLE qbo_tokens ALTER COLUMN access_token SET DEFAULT '';
UPDATE qbo_tokens SET access_token = '' WHERE access_token <> '';
