-- Migration 024: Cache the grouped ledger payload alongside the flat list.
--
-- /get-ledgers (flat) and the AI suggestion service (grouped: party / purchase
-- / sales) speak two different shapes of the same data. Until now only the
-- flat list was cached, so when the WebSocket bridge was offline the
-- suggestion pipeline had nothing to fall back to and the user saw no
-- suggestions at all.
--
-- We extend the existing per-(user, company) row with an extra JSONB column
-- so a single live fetch warms both views, and the offline path can rebuild
-- the suggestion universe without another round-trip.

ALTER TABLE tally_cached_ledgers
    ADD COLUMN IF NOT EXISTS grouped_ledgers JSONB,
    ADD COLUMN IF NOT EXISTS grouped_refreshed_at TIMESTAMPTZ;
