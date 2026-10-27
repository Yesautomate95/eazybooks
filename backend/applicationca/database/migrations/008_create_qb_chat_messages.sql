-- 008_create_qb_chat_messages
--
-- Persists QB chat history per (user, realm, thread). Each chat session is one
-- thread keyed by `thread_id`. Messages are stored append-only so we can rebuild
-- the conversation context to feed back to the LLM on the next turn — that's
-- what makes `qb_chat` feel like a multi-turn assistant instead of a one-shot
-- Q&A surface.
--
-- We persist both user messages and final assistant answers. Tool calls /
-- tool results are stored inside `metadata` on the assistant row so the UI
-- can re-render the transcript faithfully without us having to write a
-- second table just for them.

CREATE TABLE IF NOT EXISTS qb_chat_messages (
    id           BIGSERIAL PRIMARY KEY,
    user_id      TEXT NOT NULL,
    realm_id     TEXT NOT NULL DEFAULT '',
    thread_id    TEXT NOT NULL,
    role         TEXT NOT NULL CHECK (role IN ('user', 'assistant')),
    content      TEXT NOT NULL,
    metadata     JSONB,
    created_at   TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_qb_chat_messages_thread
    ON qb_chat_messages (user_id, realm_id, thread_id, created_at);

CREATE INDEX IF NOT EXISTS idx_qb_chat_messages_user_recent
    ON qb_chat_messages (user_id, realm_id, created_at DESC);
