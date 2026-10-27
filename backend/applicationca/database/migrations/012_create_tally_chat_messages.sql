-- 012_create_tally_chat_messages
--
-- Persists TallyChat history per (user, company, thread). Each chat session is
-- one thread keyed by `thread_id`. Messages are stored append-only so we can
-- rebuild the conversation context to feed back to the LLM on the next turn —
-- that's what makes TallyChat a multi-turn assistant instead of a one-shot Q&A
-- surface. Mirrors qb_chat_messages (008), but Tally is scoped by company_name
-- rather than a QBO realm_id.
--
-- Tool calls / SQL results are stored inside `metadata` on the assistant row so
-- the UI can re-render the transcript without a second table.

CREATE TABLE IF NOT EXISTS tally_chat_messages (
    id            BIGSERIAL PRIMARY KEY,
    user_id       TEXT NOT NULL,
    company_name  TEXT NOT NULL DEFAULT '',
    thread_id     TEXT NOT NULL,
    role          TEXT NOT NULL CHECK (role IN ('user', 'assistant')),
    content       TEXT NOT NULL,
    metadata      JSONB,
    created_at    TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_tally_chat_messages_thread
    ON tally_chat_messages (user_id, company_name, thread_id, created_at);

CREATE INDEX IF NOT EXISTS idx_tally_chat_messages_user_recent
    ON tally_chat_messages (user_id, company_name, created_at DESC);
